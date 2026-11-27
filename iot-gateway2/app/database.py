"""Module Quản lý Cơ sở Dữ liệu SQLite cho Máy Chủ Trung Tâm (Server Ingestion & Fleet Store).

Lưu trữ bền vững:
- vehicles: Quản lý danh sách các xe, địa chỉ IP, link xem camera, trạng thái kết nối.
- telemetry_history: Lịch sử các chỉ số sinh học, khí CO2, GPS 3D, phần cứng Pi theo thời gian.
- violations: Nhật ký vi phạm (ngủ gật, ngáp, mất tập trung), ảnh chụp bằng chứng JPEG.
- pending_commands: Hàng đợi lệnh điều khiển ngược xuống xe (Còi VOICE, Dừng STOP, Bắt tay PING).
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


class DatabaseManager:
    """Quản lý kết nối SQLite đa luồng an toàn (Thread-safe SQLite Manager)."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """Khởi tạo và di trú (migrate) cấu trúc bảng dữ liệu."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()

            # 1. Bảng quản lý hạm đội xe
            cur.execute(
                """CREATE TABLE IF NOT EXISTS vehicles (
                    vehicle_id TEXT PRIMARY KEY,
                    vehicle_name TEXT,
                    driver_name TEXT,
                    local_ip TEXT,
                    web_port INTEGER,
                    dashboard_url TEXT,
                    stream_url TEXT,
                    public_tunnel_url TEXT,
                    last_seen REAL,
                    status TEXT,
                    last_telemetry_json TEXT
                )"""
            )

            # 2. Bảng lịch sử chuỗi nhịp tim Telemetry
            cur.execute(
                """CREATE TABLE IF NOT EXISTS telemetry_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_id TEXT,
                    timestamp REAL,
                    received_at REAL,
                    co2_ppm REAL,
                    speed_kmh REAL,
                    gps_lat REAL,
                    gps_lon REAL,
                    gps_alt REAL,
                    gps_source TEXT,
                    driver_state TEXT,
                    soc_temp REAL,
                    ai_fps REAL,
                    raw_json TEXT
                )"""
            )

            # 3. Bảng nhật ký sự kiện vi phạm & bằng chứng
            cur.execute(
                """CREATE TABLE IF NOT EXISTS violations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE,
                    vehicle_id TEXT,
                    driver_name TEXT,
                    alert_type TEXT,
                    started_at TEXT,
                    confirmed_at TEXT,
                    duration_seconds REAL,
                    gps_lat REAL,
                    gps_lon REAL,
                    speed_kmh REAL,
                    co2_ppm REAL,
                    speaker_alert TEXT,
                    image_path TEXT,
                    received_at REAL,
                    raw_json TEXT
                )"""
            )

            # 4. Bảng hàng đợi lệnh điều khiển xe
            cur.execute(
                """CREATE TABLE IF NOT EXISTS pending_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_id TEXT,
                    command TEXT,
                    created_at REAL,
                    status TEXT DEFAULT 'PENDING'
                )"""
            )

            conn.commit()
            conn.close()

    def record_telemetry(self, norm_data: Dict[str, Any]) -> None:
        """Ghi nhận bản tin Telemetry vào danh sách xe và lịch sử."""
        vid = norm_data.get("vehicle_id", "XE-01")
        now = time.time()

        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()

            # Cập nhật danh bạ xe (Upsert)
            cur.execute(
                """INSERT INTO vehicles (
                    vehicle_id, vehicle_name, driver_name, local_ip, web_port,
                    dashboard_url, stream_url, public_tunnel_url, last_seen, status, last_telemetry_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vehicle_id) DO UPDATE SET
                    vehicle_name=excluded.vehicle_name,
                    driver_name=excluded.driver_name,
                    local_ip=excluded.local_ip,
                    web_port=excluded.web_port,
                    dashboard_url=excluded.dashboard_url,
                    stream_url=excluded.stream_url,
                    public_tunnel_url=excluded.public_tunnel_url,
                    last_seen=excluded.last_seen,
                    status=excluded.status,
                    last_telemetry_json=excluded.last_telemetry_json
                """,
                (
                    vid,
                    norm_data.get("vehicle_name", vid),
                    norm_data.get("driver_name", "Tài xế"),
                    norm_data.get("local_ip", ""),
                    norm_data.get("web_port", 8080),
                    norm_data.get("dashboard_url", ""),
                    norm_data.get("stream_url", ""),
                    norm_data.get("public_tunnel_url", ""),
                    now,
                    "ONLINE",
                    json.dumps(norm_data),
                ),
            )

            # Ghi vào lịch sử telemetry
            d_state = norm_data.get("driver_state", "NORMAL")
            if isinstance(d_state, dict):
                d_state = d_state.get("alert", "NORMAL")
            d_state = str(d_state)

            cur.execute(
                """INSERT INTO telemetry_history (
                    vehicle_id, timestamp, received_at, co2_ppm, speed_kmh,
                    gps_lat, gps_lon, gps_alt, gps_source, driver_state, soc_temp, ai_fps, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    vid,
                    float(norm_data.get("timestamp_ms", now * 1000) or 0) / 1000.0,
                    now,
                    float(norm_data.get("co2_ppm") or 0.0),
                    float(norm_data.get("speed_kmh") or 0.0),
                    float(norm_data.get("gps_lat") or 0.0),
                    float(norm_data.get("gps_lon") or 0.0),
                    float(norm_data.get("gps_alt") or 0.0),
                    str(norm_data.get("gps_source") or "unknown"),
                    d_state,
                    float(norm_data.get("soc_temp") or 0.0),
                    float(norm_data.get("ai_fps") or 0.0),
                    json.dumps(norm_data),
                ),
            )

            conn.commit()
            conn.close()

    def record_violation(self, v_data: Dict[str, Any], saved_image_path: str = "") -> bool:
        """Ghi nhận sự kiện vi phạm và ảnh bằng chứng."""
        event_id = v_data.get("event_id") or f"EVT_{int(time.time()*1000)}"
        vid = v_data.get("vehicle_id", "XE-01")
        now = time.time()

        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            try:
                cur.execute(
                    """INSERT INTO violations (
                        event_id, vehicle_id, driver_name, alert_type, started_at, confirmed_at,
                        duration_seconds, gps_lat, gps_lon, speed_kmh, co2_ppm, speaker_alert,
                        image_path, received_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING""",
                    (
                        event_id,
                        vid,
                        v_data.get("driver_name", "Tài xế"),
                        v_data.get("alert_type") or v_data.get("reason", "DROWSINESS"),
                        v_data.get("started_at", ""),
                        v_data.get("confirmed_at") or v_data.get("timestamp", ""),
                        v_data.get("duration_seconds") or v_data.get("duration_s", 0.0),
                        v_data.get("gps_lat", 0.0),
                        v_data.get("gps_lon", 0.0),
                        v_data.get("speed_kmh", 0.0),
                        v_data.get("co2_ppm", 0.0),
                        v_data.get("speaker_alert", "VOICE:1"),
                        saved_image_path,
                        now,
                        json.dumps(v_data),
                    ),
                )
                conn.commit()
                return True
            except Exception:
                return False
            finally:
                conn.close()

    def add_command(self, vehicle_id: str, command: str) -> int:
        """Thêm lệnh xuống hàng đợi xe (Downlink Command)."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO pending_commands (vehicle_id, command, created_at, status) VALUES (?, ?, ?, 'PENDING')",
                (vehicle_id, command.strip().upper(), time.time()),
            )
            cmd_id = cur.lastrowid
            conn.commit()
            conn.close()
            return cmd_id

    def pop_commands(self, vehicle_id: str) -> List[Dict[str, Any]]:
        """Lấy danh sách lệnh đang chờ và đánh dấu EXECUTED."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, command, created_at FROM pending_commands WHERE vehicle_id=? AND status='PENDING' ORDER BY id ASC",
                (vehicle_id,),
            )
            rows = cur.fetchall()
            cmds = [{"id": r["id"], "command": r["command"], "created_at": r["created_at"]} for r in rows]

            if cmds:
                cur.execute(
                    "UPDATE pending_commands SET status='EXECUTED' WHERE vehicle_id=? AND status='PENDING'",
                    (vehicle_id,),
                )
                conn.commit()
            conn.close()
            return cmds

    def get_fleet_summary(self) -> List[Dict[str, Any]]:
        """Lấy danh sách trạng thái tổng hợp các xe trong hạm đội."""
        now = time.time()
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM vehicles ORDER BY last_seen DESC")
            rows = cur.fetchall()
            fleet = []
            for r in rows:
                v = dict(r)
                is_online = (now - float(v.get("last_seen") or 0)) < 15.0
                v["is_online"] = is_online
                v["status"] = "ONLINE" if is_online else "OFFLINE"
                try:
                    v["latest_telemetry"] = json.loads(v.get("last_telemetry_json") or "{}")
                except Exception:
                    v["latest_telemetry"] = {}
                fleet.append(v)
            conn.close()
            return fleet

    def get_recent_violations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Lấy danh sách vi phạm gần nhất."""
        with self._lock:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM violations ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            res = [dict(r) for r in rows]
            conn.close()
            return res
