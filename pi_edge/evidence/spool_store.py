"""Single Unified Bounded SQLite WAL Spool for Pi Edge (LCK-05).

Replaces dual remote_sync.db + event_queue.db with ONE bounded store (edge_spool.db):
  - PRAGMA journal_mode=WAL & PRAGMA busy_timeout=3000 on EVERY connection
  - Monotonic retry backoff (immune to Pi NTP wall-clock jumps)
  - Idempotency-Key per event (prevents duplicate Server incidents on retry)
  - Hard disk quota (default 512 MB) with automatic FIFO eviction (never bricking storage!)
  - Max attempts cap (dead-letter instead of infinite 400/401 retry storms)
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class UnifiedSpoolStore:
    def __init__(self, storage_dir: Path, max_storage_mb: float = 512.0, max_attempts: int = 20):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.bursts_dir = self.storage_dir / "bursts"
        self.bursts_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_dir / "edge_spool.db"
        self.max_bytes = int(max(1.0, float(max_storage_mb)) * 1024 * 1024)
        self.max_attempts = max(1, int(max_attempts))
        self._lock = threading.RLock()
        self._conn = self._open_conn()
        self._init_schema()
        self.reset_retry_timers_on_boot()

    def reset_retry_timers_on_boot(self) -> int:
        """Reset monotonic retry deadlines on startup so rebooted Pi never stalls PENDING uploads (F-04)."""
        with self._lock:
            cur = self._conn.execute("UPDATE spool_items SET next_retry_mono=0.0 WHERE status='PENDING'")
            self._conn.commit()
            return int(cur.rowcount or 0)

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=3000;")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS spool_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL,            -- 'violation_burst' | 'alert_dismissed'
                    meta_json TEXT NOT NULL,
                    burst_rel_dir TEXT,
                    size_bytes INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'PENDING', -- PENDING | UPLOADED | DEAD_LETTER
                    attempts INTEGER DEFAULT 0,
                    next_retry_mono REAL DEFAULT 0,
                    last_error TEXT DEFAULT '',
                    created_at REAL DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_spool_status ON spool_items(status, next_retry_mono);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _dir_bytes(self) -> int:
        total = 0
        for p in self.storage_dir.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def enforce_quota(self) -> int:
        """FIFO evict uploaded items first, then oldest pending bursts, in O(N) without repeated full rglob scans."""
        evicted = 0
        with self._lock:
            cur_bytes = self._dir_bytes()
            while cur_bytes > self.max_bytes:
                row = self._conn.execute(
                    """SELECT id, burst_rel_dir, size_bytes FROM spool_items
                       ORDER BY CASE status WHEN 'UPLOADED' THEN 0 WHEN 'DEAD_LETTER' THEN 1 ELSE 2 END,
                                created_at ASC
                       LIMIT 1"""
                ).fetchone()
                if not row:
                    # Clean up orphan burst directories if DB has no rows left
                    removed_orphan = False
                    for child in sorted(self.bursts_dir.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0):
                        if child.is_dir():
                            freed = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
                            shutil.rmtree(child, ignore_errors=True)
                            cur_bytes = max(0, cur_bytes - freed)
                            evicted += 1
                            removed_orphan = True
                            break
                    if not removed_orphan:
                        break
                    continue
                freed = int(row["size_bytes"] or 0)
                if row["burst_rel_dir"]:
                    bdir = self.bursts_dir / row["burst_rel_dir"]
                    if bdir.exists():
                        try:
                            freed = max(freed, sum(f.stat().st_size for f in bdir.rglob("*") if f.is_file()))
                        except OSError:
                            pass
                        shutil.rmtree(bdir, ignore_errors=True)
                self._conn.execute("DELETE FROM spool_items WHERE id=?", (row["id"],))
                self._conn.commit()
                cur_bytes = max(0, cur_bytes - max(1, freed))
                evicted += 1
            if evicted > 0:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                except Exception:
                    pass
        return evicted

    def enqueue_violation_burst(
        self,
        event_id: str,
        idempotency_key: str,
        meta: Dict[str, Any],
        jpeg_frames: List[Tuple[str, bytes]],
    ) -> str:
        rel_dir = event_id.replace("/", "_").replace("..", "_")
        bdir = self.bursts_dir / rel_dir
        size_bytes = 0
        try:
            bdir.mkdir(parents=True, exist_ok=True)
            for fname, raw in jpeg_frames:
                fp = bdir / Path(fname).name
                fp.write_bytes(raw)
                size_bytes += len(raw)
        except OSError:
            # Disk full / I/O error: still enqueue metadata event without crashing safety loop!
            rel_dir = ""

        meta_str = json.dumps(meta, ensure_ascii=False)
        size_bytes += len(meta_str.encode("utf-8"))
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO spool_items
                   (event_id, idempotency_key, kind, meta_json, burst_rel_dir, size_bytes, status, attempts, next_retry_mono)
                   VALUES (?,?,?,?,?,?,'PENDING',0,0)""",
                (event_id, idempotency_key, "violation_burst", meta_str, rel_dir or None, size_bytes),
            )
            self._conn.commit()
        self.enforce_quota()
        return rel_dir

    def enqueue_json_event(self, event_id: str, idempotency_key: str, kind: str, meta: Dict[str, Any]) -> None:
        meta_str = json.dumps(meta, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO spool_items
                   (event_id, idempotency_key, kind, meta_json, burst_rel_dir, size_bytes, status, attempts, next_retry_mono)
                   VALUES (?,?,?,?,NULL,?,'PENDING',0,0)""",
                (event_id, idempotency_key, kind, meta_str, len(meta_str.encode("utf-8"))),
            )
            self._conn.commit()
        self.enforce_quota()

    def fetch_due(self, limit: int = 5, now_mono: Optional[float] = None) -> List[Dict[str, Any]]:
        now_m = now_mono if now_mono is not None else time.monotonic()
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM spool_items
                   WHERE status='PENDING' AND next_retry_mono <= ?
                   ORDER BY id ASC LIMIT ?""",
                (now_m, int(limit)),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["meta"] = json.loads(d["meta_json"])
                except Exception:
                    # Quarantine corrupt JSON rows instead of killing server-sync permanently (BUG-05)!
                    self._conn.execute(
                        "UPDATE spool_items SET status='DEAD_LETTER', last_error='corrupt_meta_json' WHERE id=?",
                        (d["id"],),
                    )
                    self._conn.commit()
                    continue
                out.append(d)
            return out

    def load_burst_files(self, rel_dir: Optional[str]) -> List[Tuple[str, bytes]]:
        if not rel_dir:
            return []
        bdir = self.bursts_dir / rel_dir
        if not bdir.exists():
            return []
        return [(p.name, p.read_bytes()) for p in sorted(bdir.glob("*.jpg"))]

    def mark_uploaded(self, event_id: str) -> None:
        with self._lock:
            row = self._conn.execute("SELECT burst_rel_dir FROM spool_items WHERE event_id=?", (event_id,)).fetchone()
            if row and row["burst_rel_dir"]:
                bdir = self.bursts_dir / row["burst_rel_dir"]
                if bdir.exists():
                    shutil.rmtree(bdir, ignore_errors=True)
            self._conn.execute(
                "UPDATE spool_items SET status='UPLOADED', burst_rel_dir=NULL, size_bytes=0, last_error='' WHERE event_id=?",
                (event_id,),
            )
            self._conn.commit()

    def mark_failed(
        self,
        event_id: str,
        error: str,
        permanent: bool = False,
        transient: bool = False,
        now_mono: Optional[float] = None,
    ) -> None:
        """Record upload failure.

        - permanent=True (e.g. HTTP 400/413/422): immediately DEAD_LETTER.
        - transient=True (network down / tunnel restart / HTTP 5xx / 408 / 429 / 401):
          NEVER dead-letter! Keep PENDING with exponential backoff up to 300s so 512MB offline
          spool survives multi-hour 4G outages (FIFO disk quota protects storage).
        """
        now_m = now_mono if now_mono is not None else time.monotonic()
        with self._lock:
            row = self._conn.execute("SELECT attempts FROM spool_items WHERE event_id=?", (event_id,)).fetchone()
            attempts = (int(row["attempts"]) + 1) if row else 1
            if permanent or (not transient and attempts >= self.max_attempts):
                status = "DEAD_LETTER"
                next_retry = 0.0
            else:
                status = "PENDING"
                backoff = min(300.0, 2.0 ** min(attempts, 9))
                next_retry = now_m + backoff
            self._conn.execute(
                "UPDATE spool_items SET status=?, attempts=?, next_retry_mono=?, last_error=? WHERE event_id=?",
                (status, attempts, next_retry, str(error)[:300], event_id),
            )
            self._conn.commit()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) c FROM spool_items GROUP BY status").fetchall()
            counts = {r["status"]: int(r["c"]) for r in rows}
            return {
                "pending": counts.get("PENDING", 0),
                "uploaded": counts.get("UPLOADED", 0),
                "dead_letter": counts.get("DEAD_LETTER", 0),
                "disk_bytes": self._dir_bytes(),
            }
