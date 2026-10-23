"""Atomic on-disk event storage and durable upload queue."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterable

import cv2


class EventStorage:
    def __init__(self, root_dir: str | Path, max_storage_mb: int = 512):
        self.root_dir = Path(root_dir)
        self.pending_dir = self.root_dir / "pending_events"
        self.db_path = self.root_dir / "event_queue.db"
        self.max_storage_bytes = int(max_storage_mb * 1024 * 1024)
        self._lock = threading.RLock()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS event_queue (
                event_id TEXT PRIMARY KEY,
                event_dir TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )"""
        )
        self._connection.commit()

    def save_event(self, event: dict[str, Any], frames: Iterable[Any]) -> Path:
        event_id = str(event["event_id"])
        target = self.pending_dir / event_id
        temporary = self.pending_dir / f".{event_id}.tmp"
        with self._lock:
            if target.exists():
                return target
            temporary.mkdir(parents=True, exist_ok=False)
            frames_dir = temporary / "frames"
            frames_dir.mkdir()
            frame_metadata = []
            try:
                for index, frame in enumerate(frames):
                    filename = f"{index:04d}.jpg"
                    path = frames_dir / filename
                    if not cv2.imwrite(str(path), frame.image):
                        raise OSError(f"Could not write event frame: {path}")
                    frame_metadata.append({
                        "filename": filename,
                        "jpeg_path": str(Path("frames") / filename),
                        "frame_id": frame.frame_id,
                        "capture_timestamp_ns": frame.capture_timestamp_ns,
                        "capture_time_utc": frame.capture_time_utc,
                        "face_valid": frame.face_valid,
                        "metadata": frame.metadata,
                    })
                payload = dict(event)
                payload["frames"] = frame_metadata
                metadata_path = temporary / "metadata.json"
                metadata_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                with metadata_path.open("r+b") as metadata_file:
                    os.fsync(metadata_file.fileno())
                temporary.replace(target)
                try:
                    self._connection.execute(
                        "INSERT INTO event_queue(event_id, event_dir, created_at) VALUES (?, ?, ?)",
                        (event_id, str(target), str(event["recording_end_time"])),
                    )
                    self._connection.commit()
                except Exception:
                    self._remove_tree(target)
                    raise
            except Exception:
                if temporary.exists():
                    self._remove_tree(temporary)
                raise
        self.enforce_limit()
        return target

    def pending(self) -> list[tuple[str, Path, int]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_id, event_dir, attempts FROM event_queue "
                "WHERE status = 'PENDING' AND next_attempt_at <= strftime('%s','now') "
                "ORDER BY created_at"
            ).fetchall()
            return [(event_id, Path(event_dir), attempts) for event_id, event_dir, attempts in rows]

    def mark_retry(self, event_id: str, attempts: int, next_attempt_at: float) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE event_queue SET attempts = ?, next_attempt_at = ? WHERE event_id = ?",
                (attempts, next_attempt_at, event_id),
            )
            self._connection.commit()

    def mark_uploaded(self, event_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE event_queue SET status = 'UPLOADED' WHERE event_id = ?",
                (event_id,),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def enforce_limit(self) -> None:
        with self._lock:
            size = sum(path.stat().st_size for path in self.root_dir.rglob("*") if path.is_file())
            if size <= self.max_storage_bytes:
                return
            rows = self._connection.execute(
                "SELECT event_id, event_dir FROM event_queue WHERE status = 'UPLOADED' ORDER BY created_at"
            ).fetchall()
            for event_id, event_dir in rows:
                self._remove_tree(Path(event_dir))
                self._connection.execute("DELETE FROM event_queue WHERE event_id = ?", (event_id,))
                size = sum(path.stat().st_size for path in self.root_dir.rglob("*") if path.is_file())
                if size <= self.max_storage_bytes:
                    break
            self._connection.commit()

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()
