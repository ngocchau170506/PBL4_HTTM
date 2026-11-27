"""SQLite WAL data layer with per-connection PRAGMAs and clean shutdown (0 leaked FDs)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, List, Optional

from server_ai.server_core import config

_local = threading.local()
_conn_registry_lock = threading.Lock()
_conn_registry: List[sqlite3.Connection] = []

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id TEXT PRIMARY KEY,
    vehicle_name TEXT,
    current_driver_id TEXT,
    current_driver_name TEXT,
    current_trip_id TEXT,
    local_ip TEXT,
    web_port INTEGER,
    stream_url TEXT,
    status TEXT DEFAULT 'OFFLINE',
    last_seen REAL,
    last_moving_at REAL,
    is_muted INTEGER DEFAULT 0,
    mute_until REAL,
    bt_phone_connected INTEGER DEFAULT 0,
    last_telemetry_json TEXT,
    gps_lat REAL,
    gps_lon REAL,
    gps_valid INTEGER DEFAULT 0,
    xyz_lat REAL,
    xyz_lon REAL,
    xyz_is_sample_anchor INTEGER DEFAULT 1,
    thermal_level INTEGER DEFAULT 0,
    last_face_seen_at REAL,
    created_at REAL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id TEXT PRIMARY KEY,
    vehicle_id TEXT REFERENCES vehicles(vehicle_id),
    driver_id TEXT,
    driver_name TEXT,
    started_at REAL,
    ended_at REAL,
    distance_km REAL DEFAULT 0,
    safety_score REAL DEFAULT 100,
    violation_count INTEGER DEFAULT 0,
    mute_count INTEGER DEFAULT 0,
    mute_abuse_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ACTIVE'
);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id);

CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    idempotency_key TEXT UNIQUE,
    vehicle_id TEXT,
    trip_id TEXT,
    driver_id TEXT,
    driver_name TEXT,
    edge_alert_state TEXT DEFAULT 'EDGE_TRIGGERED',
    verification_mode TEXT DEFAULT 'SINGLE_MODEL_YOLO',
    alert_type TEXT,
    behavior_detail TEXT,
    started_at TEXT,
    confirmed_at TEXT,
    duration_seconds REAL,
    segment_index INTEGER,
    gps_lat REAL,
    gps_lon REAL,
    speed_kmh REAL,
    co2_ppm REAL,
    image_url TEXT,
    video_url TEXT,
    burst_dir TEXT,
    burst_frame_count INTEGER DEFAULT 0,
    cnn_status TEXT DEFAULT 'PENDING',
    cnn_confidence REAL,
    cnn_model_version TEXT,
    cnn_latency_ms REAL,
    cnn_detail TEXT,
    penalty_applied INTEGER DEFAULT 0,
    human_review_status TEXT DEFAULT 'PENDING_REVIEW',
    human_reviewed_by TEXT,
    human_review_note TEXT,
    human_reviewed_at REAL,
    raw_json TEXT,
    created_at REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_violations_vehicle ON violations(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_violations_trip ON violations(trip_id);
CREATE INDEX IF NOT EXISTS idx_violations_cnn ON violations(cnn_status);

CREATE TABLE IF NOT EXISTS alert_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE,
    vehicle_id TEXT,
    trip_id TEXT,
    driver_id TEXT,
    dismiss_source TEXT,
    dismissed_alert_type TEXT,
    related_violation_id TEXT,
    reaction_time_ms INTEGER,
    reaction_grade TEXT,
    mute_duration_s INTEGER,
    still_risky_after INTEGER DEFAULT 0,
    gps_lat REAL,
    gps_lon REAL,
    speed_kmh REAL,
    created_at REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_dismissals_vehicle ON alert_dismissals(vehicle_id);

CREATE TABLE IF NOT EXISTS bluetooth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT,
    driver_id TEXT,
    driver_name TEXT,
    phone_device_id TEXT,
    phone_name TEXT,
    transport TEXT,
    last_rssi_dbm INTEGER,
    battery_pct INTEGER,
    heart_rate_bpm INTEGER,
    connected_at REAL,
    disconnected_at REAL,
    status TEXT DEFAULT 'CONNECTED'
);
CREATE INDEX IF NOT EXISTS idx_bt_vehicle ON bluetooth_sessions(vehicle_id);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT UNIQUE,
    vehicle_id TEXT,
    severity TEXT,
    category TEXT,
    title TEXT,
    detail_json TEXT,
    status TEXT DEFAULT 'OPEN',
    ack_by TEXT,
    note TEXT,
    created_at REAL DEFAULT (strftime('%s','now')),
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_incidents_vehicle ON incidents(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);

CREATE TABLE IF NOT EXISTS incident_cooldowns (
    vehicle_id TEXT,
    category TEXT,
    last_raised_at REAL,
    PRIMARY KEY (vehicle_id, category)
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cmd_uid TEXT UNIQUE,
    vehicle_id TEXT,
    command TEXT,
    source TEXT,
    actor_role TEXT,
    status TEXT DEFAULT 'PENDING',
    ack_detail TEXT,
    created_at REAL DEFAULT (strftime('%s','now')),
    delivered_at REAL,
    acked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_commands_vehicle ON commands(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_commands_status ON commands(status);

CREATE TABLE IF NOT EXISTS remote_configs (
    vehicle_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    updated_at REAL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS telemetry_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT,
    ts REAL,
    co2_ppm REAL,
    speed_kmh REAL,
    pi_temp_c REAL,
    heart_rate_bpm INTEGER,
    lat REAL,
    lon REAL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_vehicle_ts ON telemetry_history(vehicle_id, ts);

CREATE TABLE IF NOT EXISTS cabin_sensor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT,
    ts REAL,
    sensor_type TEXT,
    payload_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_cabin_vehicle ON cabin_sensor_events(vehicle_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL DEFAULT (strftime('%s','now')),
    actor_role TEXT,
    token_suffix TEXT,
    method TEXT,
    path TEXT,
    vehicle_id TEXT,
    status_code INTEGER,
    remote_ip TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS phone_pairings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id TEXT,
    pair_code TEXT,
    token TEXT UNIQUE,
    driver_id TEXT,
    created_at REAL DEFAULT (strftime('%s','now')),
    status TEXT DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS phone_pair_otps (
    pair_code TEXT PRIMARY KEY,
    vehicle_id TEXT NOT NULL,
    driver_id TEXT,
    issued_by TEXT,
    expires_at REAL NOT NULL,
    used INTEGER DEFAULT 0
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    with _conn_registry_lock:
        _conn_registry.append(conn)
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "db_path", None) != str(config.DB_PATH):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _local.conn = _connect()
        _local.db_path = str(config.DB_PATH)
    return _local.conn


def close_all_connections() -> None:
    with _conn_registry_lock:
        conns = list(_conn_registry)
        _conn_registry.clear()
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    if hasattr(_local, "conn"):
        _local.conn = None


def init_db() -> None:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30.0)
    try:
        conn.executescript(SCHEMA)
        # Non-destructive additive migrations for existing DB files
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(violations)").fetchall()}
        additions = (
            ("human_review_status", "TEXT DEFAULT 'PENDING_REVIEW'"),
            ("human_reviewed_by", "TEXT"),
            ("human_review_note", "TEXT"),
            ("human_reviewed_at", "REAL"),
        )
        for col, col_def in additions:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE violations ADD COLUMN {col} {col_def}")
        conn.commit()
    finally:
        conn.close()


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]


def now() -> float:
    return time.time()


def dumps(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def loads(s: Optional[str]) -> Any:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


_VEHICLE_ALLOWED_COLUMNS = {
    "vehicle_id", "vehicle_name", "current_driver_id", "current_driver_name",
    "current_trip_id", "local_ip", "web_port", "stream_url", "status",
    "last_seen", "last_moving_at", "is_muted", "mute_until",
    "bt_phone_connected", "last_telemetry_json", "gps_lat", "gps_lon",
    "gps_valid", "xyz_lat", "xyz_lon", "xyz_is_sample_anchor",
    "thermal_level", "last_face_seen_at",
}


def upsert_vehicle(vehicle_id: str, **fields) -> None:
    clean = {k: v for k, v in fields.items() if k in _VEHICLE_ALLOWED_COLUMNS and v is not None}
    clean["vehicle_id"] = vehicle_id
    clean.setdefault("last_seen", now())
    with tx() as conn:
        existing = conn.execute(
            "SELECT vehicle_id FROM vehicles WHERE vehicle_id=?", (vehicle_id,)
        ).fetchone()
        if existing:
            cols = [k for k in clean if k != "vehicle_id"]
            if cols:
                set_clause = ", ".join(f"{k}=?" for k in cols)
                values = [clean[k] for k in cols] + [vehicle_id]
                conn.execute(f"UPDATE vehicles SET {set_clause} WHERE vehicle_id=?", values)
        else:
            cols = list(clean.keys())
            placeholders = ", ".join(["?"] * len(cols))
            conn.execute(
                f"INSERT INTO vehicles ({', '.join(cols)}) VALUES ({placeholders})",
                [clean[k] for k in cols],
            )


def get_vehicle(vehicle_id: str) -> Optional[dict]:
    conn = get_conn()
    return row_to_dict(conn.execute("SELECT * FROM vehicles WHERE vehicle_id=?", (vehicle_id,)).fetchone())


def list_vehicles() -> list:
    conn = get_conn()
    return rows_to_list(conn.execute("SELECT * FROM vehicles ORDER BY vehicle_id").fetchall())
