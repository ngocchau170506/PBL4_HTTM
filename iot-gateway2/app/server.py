"""Máy Chủ Trung Tâm Tiếp Nhận & Quản Lý Dữ Liệu Xe (Fleet Management Server).

Chịu trách nhiệm:
1. Tiếp nhận dòng nhịp tim Telemetry từ Raspberry Pi 4 (IOTv2/IOTv3/ESP32 Bridge).
2. Tiếp nhận sự kiện vi phạm an toàn kèm ảnh bằng chứng JPEG Base64.
3. Hàng đợi lệnh hai chiều (Downlink Commands) để phát âm thanh xuống còi xe.
4. Cung cấp Web Dashboard thời gian thực trực quan trên cổng chỉ định (mặc định 5000).
5. Tương thích 100% với các chuẩn gửi của:
   - IOTv2: cloud_sync.py (/api/v1/telemetry & /api/v1/violation)
   - IOTv3: telemetry_streamer.py & esp32_network_bridge.py
   - Trạm trung gian: /api/v1/pi/telemetry & /api/v1/esp/bridge_telemetry
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.parse
import urllib.request

from app.database import DatabaseManager

SERVER_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SERVER_DIR / "app" / "templates"
EVIDENCE_DIR = SERVER_DIR / "evidence"
DATA_DIR = SERVER_DIR / "data"

EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_local_ip() -> str:
    """Tự động phát hiện IP LAN của máy chủ để hướng dẫn Pi kết nối."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def normalize_incoming_telemetry(payload: Dict[str, Any], path: str) -> Dict[str, Any]:
    """Chuẩn hóa dữ liệu từ mọi nguồn (IOTv2, IOTv3, ESP32 Bridge, Direct Pi)."""
    norm: Dict[str, Any] = dict(payload)
    vid = payload.get("vehicle_id") or payload.get("device_id") or "XE-01"
    norm["vehicle_id"] = vid

    # 1. Trích xuất thông tin truy cập (Access Info)
    acc = payload.get("access_info", {})
    if isinstance(acc, dict):
        norm.setdefault("local_ip", acc.get("local_ip", ""))
        norm.setdefault("web_port", acc.get("web_port", 8080))
        norm.setdefault("dashboard_url", acc.get("dashboard_url", ""))
        norm.setdefault("stream_url", acc.get("stream_url", ""))
        norm.setdefault("public_tunnel_url", acc.get("public_tunnel_url", ""))

    # 2. Trích xuất GPS & Vận tốc
    gps = payload.get("gps", {})
    if isinstance(gps, dict):
        norm.setdefault("gps_lat", gps.get("lat") or gps.get("gps_lat", 0.0))
        norm.setdefault("gps_lon", gps.get("lon") or gps.get("gps_lon", 0.0))
        norm.setdefault("gps_alt", gps.get("alt_m") or gps.get("altitude_m") or gps.get("gps_alt", 0.0))
        norm.setdefault("speed_kmh", gps.get("speed_kmh", 0.0))
        norm.setdefault("gps_satellites", gps.get("satellites") or gps.get("gps_satellites", 0))
        norm.setdefault("gps_source", gps.get("source") or ("server_xyz" if gps.get("fix") == 2 else "satellite"))
    else:
        norm.setdefault("gps_lat", norm.get("gps_lat", 0.0))
        norm.setdefault("gps_lon", norm.get("gps_lon", 0.0))
        norm.setdefault("speed_kmh", norm.get("speed_kmh", 0.0))

    # 3. Trích xuất MQ-135 & ESP
    mq = payload.get("mq135", {}) or payload.get("esp_status", {})
    if isinstance(mq, dict):
        norm.setdefault("co2_ppm", mq.get("co2_ppm", 0.0))
        norm.setdefault("mq135_raw", mq.get("mq135_raw") or mq.get("raw_adc", 0))
        norm.setdefault("co2_level", mq.get("gas_level") or mq.get("co2_level", 0))
    else:
        norm.setdefault("co2_ppm", norm.get("co2_ppm", 0.0))

    # 4. Trích xuất Trạng thái Tài xế & Sinh học
    ds = payload.get("driver_state", {})
    if isinstance(ds, dict):
        norm["driver_state"] = ds.get("alert", "NORMAL")
        hp = ds.get("head_pose")
        if isinstance(hp, (list, tuple)) and len(hp) >= 3:
            norm["yaw"] = hp[0]
            norm["pitch"] = hp[1]
            norm["roll"] = hp[2]
    elif ds:
        norm["driver_state"] = str(ds)
    else:
        norm["driver_state"] = "NORMAL"

    pi_st = payload.get("pi_status", {})
    if isinstance(pi_st, dict):
        if pi_st.get("driver_state"):
            norm["driver_state"] = str(pi_st.get("driver_state"))
        norm["ear"] = pi_st.get("ear", norm.get("ear", 0.0))
        norm["mar"] = pi_st.get("mar", norm.get("mar", 0.0))
        norm["pitch"] = pi_st.get("pitch", norm.get("pitch", 0.0))
        norm["yaw"] = pi_st.get("yaw", norm.get("yaw", 0.0))

    # 5. Sức khỏe Pi & Camera
    hw = payload.get("hardware", {}) or payload.get("pi", {}) or payload.get("pi_status", {})
    if isinstance(hw, dict):
        norm.setdefault("soc_temp", hw.get("temp_c") or hw.get("cpu_temp_c") or hw.get("soc_temp", 0.0))
        norm.setdefault("cpu_usage_pct", hw.get("cpu_percent") or hw.get("cpu_usage_pct", 0.0))
        norm.setdefault("ram_used_mb", hw.get("ram_mb") or hw.get("ram_used_mb", 0.0))
        norm.setdefault("throttled", hw.get("throttled", "0x0"))

    cam = payload.get("camera", {})
    if isinstance(cam, dict):
        norm.setdefault("ai_fps", cam.get("fps") or norm.get("ai_fps", 0.0))
        norm.setdefault("camera_status", cam.get("status", "ONLINE"))

    return norm


def forward_to_main_backend(data: Dict[str, Any], violation_flag: Optional[str] = None) -> None:
    """Tự động chuyển tiếp bản sao Telemetry sang Backend Node.js cổng 3000 để cập nhật Web Dashboard chính."""
    def _send():
        try:
            d_state = violation_flag or data.get("driver_state")
            flag = None
            if d_state and str(d_state).upper() not in ("NORMAL", "OK", "0", "NONE"):
                raw_flag = str(d_state).upper()
                if "DROWS" in raw_flag or "SLEEP" in raw_flag or "SUSPECT" in raw_flag:
                    flag = "DROWSY"
                elif "YAWN" in raw_flag:
                    flag = "YAWNING"
                elif "PHONE" in raw_flag:
                    flag = "PHONE"
                else:
                    flag = raw_flag

            gps = data.get("gps", {}) if isinstance(data.get("gps"), dict) else {}
            lat = float(data.get("gps_lat") or gps.get("lat") or 16.0685)
            lng = float(data.get("gps_lon") or gps.get("lon") or 108.2215)
            speed = float(data.get("speed_kmh") or gps.get("speed_kmh") or 0.0)

            mq = data.get("mq135", {}) if isinstance(data.get("mq135"), dict) else {}
            co2 = float(data.get("co2_ppm") or mq.get("co2_ppm") or 450.0)

            fwd_body = {
                "deviceId": 3,
                "tripId": 2,
                "viDo": lat,
                "kinhDo": lng,
                "tocDo": speed,
                "co2Level": co2,
                "aiViolationFlag": flag,
            }
            body_bytes = json.dumps(fwd_body).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:3000/api/iot/telemetry",
                data=body_bytes,
                headers={"Content-Type": "application/json", "User-Agent": "IoT-Gateway-Forwarder/1.0"},
            )
            with urllib.request.urlopen(req, timeout=1.5):
                pass
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


def create_handler_class(db: DatabaseManager):
    """Factory tạo HTTP Request Handler liên kết với DatabaseManager."""

    class FleetRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Tắt in log rác HTTP access để giữ terminal sạch đẹp
            pass

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        def _send_json(self, status: int, data: Any):
            try:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            clean_path = parsed.path.rstrip("/") or "/"
            params = urllib.parse.parse_qs(parsed.query)

            # 1. Giao diện Web Dashboard chính
            if clean_path in ("/", "/dashboard", "/index.html"):
                html_path = TEMPLATES_DIR / "dashboard.html"
                if html_path.exists():
                    body = html_path.read_bytes()
                else:
                    body = b"<h1>Fleet Management Server Ready</h1>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # 2. Xem ảnh bằng chứng vi phạm (/evidence/<filename>)
            if clean_path.startswith("/evidence/"):
                fname = clean_path.replace("/evidence/", "").strip()
                fpath = EVIDENCE_DIR / fname
                if fpath.exists() and fpath.is_file():
                    mime, _ = mimetypes.guess_type(str(fpath))
                    body = fpath.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mime or "image/jpeg")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                else:
                    self.send_response(404)
                    self.end_headers()
                    return

            # 3. API danh sách xe (Fleet Summary & Telemetry)
            if clean_path in ("/api/v1/telemetry", "/api/v1/server/fleet", "/api/v1/fleet"):
                fleet = db.get_fleet_summary()
                self._send_json(200, fleet)
                return

            # 3b. API tọa độ Downlink XYZ
            if clean_path in ("/api/v1/geo/xyz", "/api/v1/geo"):
                self._send_json(200, {
                    "ok": True,
                    "downlink_xyz": {
                        "lat": 16.068500,
                        "lon": 108.221500,
                        "alt_m": 8.5,
                        "speed_kmh": 0.0,
                        "location_name": "Đà Nẵng",
                        "source": "server_xyz"
                    }
                })
                return

            # 4. API danh sách vi phạm gần nhất
            if clean_path in ("/api/v1/server/violations", "/api/v1/violations"):
                violations = db.get_recent_violations(limit=30)
                self._send_json(200, violations)
                return

            # 5. Pi kéo lệnh điều khiển (Pull Commands)
            if clean_path in ("/api/v1/pi/pull_commands", "/api/v1/commands/pull"):
                vid = params.get("vehicle_id", ["XE-01"])[0]
                cmds = db.pop_commands(vid)
                self._send_json(200, {"status": "ok", "vehicle_id": vid, "commands": cmds})
                return

            # 6. Health check
            if clean_path in ("/health", "/ping"):
                self._send_json(200, {"status": "UP", "timestamp": time.time()})
                return

            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            clean_path = parsed.path.rstrip("/") or "/"

            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length)

            payload: Dict[str, Any] = {}
            if raw_body:
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except Exception:
                    self._send_json(400, {"error": "INVALID_JSON"})
                    return

            # 1. TIẾP NHẬN TELEMETRY TỪ XE (IOTv2/IOTv3/ESP)
            if clean_path in (
                "/api/v1/telemetry",
                "/api/v1/pi/telemetry",
                "/api/v1/esp/bridge_telemetry",
                "/api/v1/esp/telemetry",
            ):
                norm = normalize_incoming_telemetry(payload, clean_path)
                db.record_telemetry(norm)
                forward_to_main_backend(norm)

                vid = norm.get("vehicle_id", "XE-01")
                # Trả về các lệnh đang chờ (Downlink Commands) ngay trong body ACK
                pending = db.pop_commands(vid)

                resp = {
                    "status": "ACK",
                    "server_time": time.time(),
                    "vehicle_id": vid,
                    "commands": pending,
                    "downlink_xyz": {
                        "lat": norm.get("gps_lat", 10.776889),
                        "lon": norm.get("gps_lon", 106.700806),
                        "alt_m": norm.get("gps_alt", 15.0),
                        "speed_kmh": norm.get("speed_kmh", 0.0),
                        "note": "server_ack_ok",
                    },
                }
                self._send_json(200, resp)
                return

            # 2. TIẾP NHẬN SỰ KIỆN VI PHẠM KÈM ẢNH BẰNG CHỨNG
            if clean_path in (
                "/api/v1/violation",
                "/api/v1/pi/violation",
                "/api/v1/esp/violation",
            ):
                img_data_raw = payload.get("evidence_image") or payload.get("image_base64") or ""
                saved_path = ""

                if img_data_raw:
                    try:
                        # Bóc tách tiền tố data:image/jpeg;base64, nếu có
                        if "," in img_data_raw:
                            img_data_raw = img_data_raw.split(",", 1)[1]
                        img_bytes = base64.b64decode(img_data_raw)
                        fname = f"VIOLATION_{int(time.time()*1000)}.jpg"
                        fpath = EVIDENCE_DIR / fname
                        fpath.write_bytes(img_bytes)
                        saved_path = str(fpath)
                    except Exception as exc:
                        print(f"[SERVER] Loi luu anh bang chung: {exc}")

                db.record_violation(payload, saved_image_path=saved_path)
                v_alert = payload.get("alert_type") or payload.get("reason", "DROWSINESS")
                forward_to_main_backend(payload, violation_flag=v_alert)
                self._send_json(200, {
                    "status": "ACK",
                    "event_id": payload.get("event_id"),
                    "saved": True,
                    "image_url": f"/evidence/{Path(saved_path).name}" if saved_path else "",
                })
                return

            # 3. PHÁT LỆNH ĐIỀU KHIỂN TỪ WEB DASHBOARD XUỐNG XE
            if clean_path in ("/api/v1/server/command", "/api/v1/command"):
                vid = payload.get("vehicle_id", "XE-01")
                cmd = payload.get("command", "").strip()
                if not cmd:
                    self._send_json(400, {"error": "EMPTY_COMMAND"})
                    return
                cmd_id = db.add_command(vid, cmd)
                self._send_json(200, {
                    "status": "QUEUED",
                    "command_id": cmd_id,
                    "vehicle_id": vid,
                    "command": cmd,
                    "msg": f"Đã đưa lệnh [{cmd}] vào hàng đợi xe {vid}",
                })
                return

            self.send_response(404)
            self.end_headers()

    return FleetRequestHandler


class FleetServer:
    """Máy chủ trung tâm độc lập quản lý hạm đội xe."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5000, db_path: Optional[Path] = None):
        self.host = host
        self.port = port
        self.db = DatabaseManager(db_path or (DATA_DIR / "fleet_server.db"))
        handler_cls = create_handler_class(self.db)
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler_cls)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Chạy server trong luồng nền (non-blocking)."""
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def run_forever(self) -> None:
        """Chạy server đồng bộ (blocking loop)."""
        self.httpd.serve_forever()

    def stop(self) -> None:
        """Dừng server và giải phóng cổng mạng."""
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
