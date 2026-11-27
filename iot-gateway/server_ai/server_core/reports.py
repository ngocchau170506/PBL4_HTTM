"""CSV/JSON report exports + scheduled retention cleanup (telemetry_history + evidence directory)."""
from __future__ import annotations

import csv
import io
import shutil
import time
from typing import Optional

from server_ai.server_core import config, database


def _apply_filters(
    base_query: str,
    vehicle_id: Optional[str],
    driver_id: Optional[str],
    start_ts: Optional[float],
    end_ts: Optional[float],
    ts_col: str,
) -> tuple[str, list]:
    q = base_query
    params: list = []
    if vehicle_id:
        q += " AND vehicle_id=?"
        params.append(vehicle_id)
    if driver_id:
        q += " AND driver_id=?"
        params.append(driver_id)
    if start_ts is not None:
        q += f" AND {ts_col}>=?"
        params.append(start_ts)
    if end_ts is not None:
        q += f" AND {ts_col}<=?"
        params.append(end_ts)
    return q, params


def export_violations(fmt: str, vehicle_id=None, driver_id=None, start_ts=None, end_ts=None) -> tuple[bytes, str]:
    conn = database.get_conn()
    q, params = _apply_filters("SELECT * FROM violations WHERE 1=1", vehicle_id, driver_id, start_ts, end_ts, "created_at")
    rows = database.rows_to_list(conn.execute(q + " ORDER BY created_at DESC", params).fetchall())
    return _serialize(rows, fmt), "violations"


def export_dismissals(fmt: str, vehicle_id=None, driver_id=None, start_ts=None, end_ts=None) -> tuple[bytes, str]:
    conn = database.get_conn()
    q, params = _apply_filters("SELECT * FROM alert_dismissals WHERE 1=1", vehicle_id, driver_id, start_ts, end_ts, "created_at")
    rows = database.rows_to_list(conn.execute(q + " ORDER BY created_at DESC", params).fetchall())
    return _serialize(rows, fmt), "alert_dismissals"


def export_trips(fmt: str, vehicle_id=None, driver_id=None, start_ts=None, end_ts=None) -> tuple[bytes, str]:
    conn = database.get_conn()
    q, params = _apply_filters("SELECT * FROM trips WHERE 1=1", vehicle_id, driver_id, start_ts, end_ts, "started_at")
    rows = database.rows_to_list(conn.execute(q + " ORDER BY started_at DESC", params).fetchall())
    return _serialize(rows, fmt), "trips"


def _serialize(rows: list, fmt: str) -> bytes:
    if fmt == "json":
        return database.dumps(rows).encode("utf-8")
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _entry_size(p) -> int:
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


def cleanup_old_data() -> dict:
    cutoff = time.time() - config.TELEMETRY_HISTORY_RETENTION_DAYS * 86400
    with database.tx() as conn:
        cur = conn.execute("DELETE FROM telemetry_history WHERE ts < ?", (cutoff,))
        deleted_rows = cur.rowcount

    deleted_items = 0
    freed_bytes = 0
    max_bytes = int(config.EVIDENCE_MAX_SIZE_GB * (1024 ** 3))
    entries = sorted(config.EVIDENCE_DIR.iterdir(), key=lambda p: p.stat().st_mtime) if config.EVIDENCE_DIR.exists() else []
    sizes = [(p, _entry_size(p)) for p in entries]
    total_size = sum(sz for _, sz in sizes)
    for p, sz in sizes:
        if total_size <= max_bytes:
            break
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
            total_size -= sz
            freed_bytes += sz
            deleted_items += 1
        except Exception:
            pass

    return {
        "telemetry_rows_deleted": deleted_rows,
        "evidence_items_deleted": deleted_items,
        "evidence_bytes_freed": freed_bytes,
    }
