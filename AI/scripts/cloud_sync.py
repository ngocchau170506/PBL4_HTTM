"""Đồng bộ dữ liệu thời gian thực (Telemetry & Vi phạm) từ thiết bị biên về máy chủ trung tâm.

Hỗ trợ gửi bất đồng bộ qua HTTP REST API và lưu đệm SQLite (Offline Store-and-Forward)
để tự động gửi bù khi khôi phục kết nối mạng.
"""
from __future__ import annotations

import base64
import contextlib
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import socket
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.error
import urllib.request
import cv2

from AI.scripts.diagnostic_log import emit_error_event

try:
    from AI.scripts.gps_service import GpsManager
except ImportError:
    try:
        from gps_service import GpsManager
    except ImportError:
        GpsManager = None


_LOCAL_IP_CACHE = {"ip": "", "ts": 0.0}


def get_local_ip() -> str:
    """Tự động phát hiện địa chỉ IP mạng nội bộ (LAN/WiFi); kết quả được cache 10s
    (trước đây tạo socket mới 5 lần/giây trong vòng lặp đồng bộ)."""
    now = time.monotonic()
    if _LOCAL_IP_CACHE["ip"] and (now - _LOCAL_IP_CACHE["ts"]) < 10.0:
        return _LOCAL_IP_CACHE["ip"]
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        # Sử dụng địa chỉ DNS công cộng để xác định interface outgoing chính
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = ""
    if not ip:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    _LOCAL_IP_CACHE["ip"] = ip
    _LOCAL_IP_CACHE["ts"] = now
    return ip


def _verify_server_url(url: str, timeout_s: float = 1.0) -> bool:
    """Kiểm tra xem URL server bất kỳ (LAN IP, Ngrok, Cloudflare Tunnel, Domain...) có phản hồi không."""
    try:
        clean = url.rstrip("/")
        req = urllib.request.Request(f"{clean}/api/v1/health", method="GET")
        req.add_header("User-Agent", "DriverMonitorEdge/HealthCheck")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def _verify_iotv3_server(host: str, port: int = 5000, timeout_s: float = 0.35) -> bool:
    """Kiểm tra nhanh xem một IP trên mạng LAN có đang mở cổng 5000 và phản hồi /api/v1/health không."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            pass
        return _verify_server_url(f"http://{host}:{port}", timeout_s=0.6)
    except Exception:
        return False


def auto_discover_pc_server(current_url: str = "", default_port: int = 5000) -> str:
    """Tự động dò tìm IP của Máy Chủ PC (E:\\AI\\Server) khi chạy trên mạng Wi-Fi LAN.
    
    NGUYÊN TẮC AN TOÀN TRUYỀN TIN (CHỐNG MẤT KẾT NỐI KHI CHẠY 2 MÁY):
    1. Nếu người dùng cấu hình URL tĩnh (Domain, Ngrok, Cloudflare Tunnel, hoặc IP riêng):
       -> LUÔN GIỮ NGUYÊN URL này, tuyệt đối không tự ý ghi đè về IP mặc định 192.168.1.6.
    2. Nếu URL là HTTPS hoặc chứa domain công cộng: bỏ qua quét LAN, giữ nguyên URL từ xa.
    3. Chỉ tự động tìm kiếm trên LAN nội bộ khi URL chưa được cấu hình hoặc là localhost.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from urllib.parse import urlparse

    if current_url:
        cur_clean = current_url.strip()
        if not (cur_clean.startswith("http://") or cur_clean.startswith("https://")):
            cur_clean = f"http://{cur_clean}"
        parsed = urlparse(cur_clean)
        # Nếu là URL HTTPS từ xa hoặc có domain (không phải IP nội bộ): giữ nguyên 100%
        if parsed.scheme == "https" or (parsed.hostname and not parsed.hostname.replace(".", "").isdigit()):
            return cur_clean
        # Nếu là IP người dùng đã chỉ định cụ thể (khác localhost và 127.0.0.1)
        if parsed.hostname and parsed.hostname not in ("127.0.0.1", "localhost", "0.0.0.0"):
            # Nếu đang phản hồi thì dùng ngay
            if _verify_server_url(cur_clean, timeout_s=0.5):
                return cur_clean
            # Nếu tạm thời chưa bật server, vẫn giữ nguyên URL này chờ server bật, không tự nhảy sang IP khác
            if parsed.hostname != "192.168.1.6":
                return cur_clean

    local_ip = get_local_ip()
    parsed = urlparse(current_url) if current_url else None
    cur_host = (parsed.hostname or "") if parsed else ""
    cur_port = (parsed.port or default_port) if parsed else default_port

    # 1. Thử URL hiện tại nếu đang mở
    if cur_host and cur_host not in ("127.0.0.1", "localhost"):
        if _verify_server_url(f"http://{cur_host}:{cur_port}", timeout_s=0.4):
            return f"http://{cur_host}:{cur_port}"

    # 2. Thử phát tín hiệu UDP Broadcast trên cổng 5005 (nếu cùng mạng Wi-Fi LAN)
    try:
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_sock.settimeout(0.35)
        discover_msg = json.dumps({
            "magic": "IOTV3_DISCOVER_SERVER",
            "pi_ip": local_ip,
            "web_port": int(os.getenv("WEB_PORT", "8080")),
        }).encode("utf-8")
        targets = ["255.255.255.255"]
        parts = local_ip.split(".")
        if len(parts) == 4 and parts[0] != "127":
            targets.append(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
        for bcast in targets:
            try:
                udp_sock.sendto(discover_msg, (bcast, 5005))
            except Exception:
                pass
        t0 = time.time()
        while time.time() - t0 < 0.35:
            try:
                data, addr = udp_sock.recvfrom(1024)
                info = json.loads(data.decode("utf-8", errors="replace"))
                if info.get("magic") == "IOTV3_SERVER_BEACON":
                    srv_url = info.get("server_url") or f"http://{addr[0]}:{info.get('port', cur_port)}"
                    udp_sock.close()
                    return srv_url.rstrip("/")
            except Exception:
                break
        udp_sock.close()
    except Exception:
        pass

    # 3. Quét dải /24: MẶC ĐỊNH TẮT (chỉ bật khi REMOTE_SCAN_SUBNET=1)
    parts = local_ip.split(".")
    if os.getenv("REMOTE_SCAN_SUBNET", "0") == "1" and len(parts) == 4 and parts[0] != "127":
        prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
        candidates = [f"{prefix}{i}" for i in range(1, 255) if f"{prefix}{i}" != local_ip and f"{prefix}{i}" != "192.168.1.210"]
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                future_map = {pool.submit(_verify_iotv3_server, ip, cur_port, 0.25): ip for ip in candidates}
                for fut in as_completed(future_map):
                    try:
                        if fut.result():
                            found_ip = future_map[fut]
                            return f"http://{found_ip}:{cur_port}"
                    except Exception:
                        pass
        except Exception:
            pass

    return current_url or f"http://{local_ip}:{cur_port}"


@dataclass
class VehicleAccessInfo:
    vehicle_id: str = "XE-01"
    vehicle_name: str = "Xe Giám Sát Thử Nghiệm"
    driver_name: str = "Tài xế 01"
    local_ip: str = ""
    web_port: int = 8080
    dashboard_url: str = ""
    stream_url: str = ""
    public_tunnel_url: str = ""

    @classmethod
    def create_default(cls) -> VehicleAccessInfo:
        vid = os.getenv("VEHICLE_ID", "XE-01")
        vname = os.getenv("VEHICLE_NAME", "Xe Giám Sát Thử Nghiệm")
        dname = os.getenv("DRIVER_NAME", "Tài xế 01")
        port = int(os.getenv("WEB_PORT", "8080"))
        ip = get_local_ip()
        tunnel = os.getenv("PUBLIC_TUNNEL_URL", "").strip()
        dash = tunnel if tunnel else f"http://{ip}:{port}"
        stream = f"{tunnel}/stream" if tunnel else f"http://{ip}:{port}/stream"
        return cls(
            vehicle_id=vid,
            vehicle_name=vname,
            driver_name=dname,
            local_ip=ip,
            web_port=port,
            dashboard_url=dash,
            stream_url=stream,
            public_tunnel_url=tunnel,
        )


class CloudSyncManager:
    """Quản lý kết nối đồng bộ telemetry, sự kiện vi phạm và hàng đợi gửi bù SQLite."""

    def __init__(
        self,
        server_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        enabled: Optional[bool] = None,
        db_path: Optional[str] = None,
        telemetry_interval: float = 3.0,
        gps_manager: Optional[Any] = None,
        esp_communicator: Optional[Any] = None,
    ):
        if enabled is None:
            self.enabled = os.getenv("REMOTE_SERVER_ENABLED", "1").strip().lower() in ("1", "true", "yes")
        else:
            self.enabled = bool(enabled)

        raw_url = (server_url or os.getenv("REMOTE_SERVER_URL", "http://127.0.0.1:5000")).strip().rstrip("/")
        if raw_url and not (raw_url.startswith("http://") or raw_url.startswith("https://")):
            raw_url = f"http://{raw_url}"
        self.server_url = raw_url
        self.auth_token = auth_token or os.getenv("REMOTE_AUTH_TOKEN", "").strip()
        self.telemetry_interval = float(os.getenv("TELEMETRY_INTERVAL_S", str(telemetry_interval)))

        self.access_info = VehicleAccessInfo.create_default()

        if gps_manager is not None:
            self.gps_manager = gps_manager
        elif GpsManager is not None:
            self.gps_manager = GpsManager(exclude_ports=[getattr(esp_communicator, "port", None)])
        else:
            self.gps_manager = None

        if db_path is None:
            data_dir = Path(__file__).resolve().parents[1] / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = data_dir / "remote_sync.db"
        else:
            self.db_path = Path(db_path)

        self._lock = threading.RLock()
        self.last_lock_wait_ms: float = 0.0
        self.max_lock_wait_ms: float = 0.0
        self.last_lock_hold_ms: float = 0.0
        self.max_lock_hold_ms: float = 0.0
        self._init_sqlite()

        self._violation_queue: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=8)
        self._discover_backoff_s = 30.0
        self.uplink_mode = os.getenv("UPLINK_MODE", "direct_wifi").strip().lower()
        self.telemetry_via_esp = 0
        self.last_telemetry_route = "NONE"

        self.latest_state: Dict[str, Any] = {}
        self.esp_context: Any = esp_communicator
        self.pipeline_context: Any = None
        self.camera_context: Any = None

        self.server_online: bool = False
        self.last_sync_time: float = 0.0
        self.last_sync_status: str = "CHƯA_KẾT_NỐI"
        self.total_packets_sent: int = 0
        self.total_violations_sent: int = 0
        self.pending_spool_count: int = 0

        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        if self.enabled and self.server_url:
            self._start_worker()

    @contextlib.contextmanager
    def _acquire_lock(self):
        """Đo thời gian chờ (wait_ms) và thời gian giữ (hold_ms) của RLock thực tế (PROPOSE-CR-001 Bước 3)."""
        t0 = time.perf_counter()
        self._lock.acquire()
        t_acq = time.perf_counter()
        wait_ms = (t_acq - t0) * 1000.0
        self.last_lock_wait_ms = round(wait_ms, 3)
        if wait_ms > self.max_lock_wait_ms:
            self.max_lock_wait_ms = round(wait_ms, 3)
        try:
            yield
        finally:
            hold_ms = (time.perf_counter() - t_acq) * 1000.0
            self.last_lock_hold_ms = round(hold_ms, 3)
            if hold_ms > self.max_lock_hold_ms:
                self.max_lock_hold_ms = round(hold_ms, 3)
            self._lock.release()

    def _init_sqlite(self):
        with self._acquire_lock():
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute(
                """CREATE TABLE IF NOT EXISTS pending_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0
                )"""
            )
            conn.commit()
            conn.close()

    def set_context(self, esp=None, pipeline=None, camera_source=None, gps=None):
        with self._acquire_lock():
            if esp is not None:
                self.esp_context = esp
            if pipeline is not None:
                self.pipeline_context = pipeline
            if camera_source is not None:
                self.camera_context = camera_source
            if gps is not None:
                self.gps_manager = gps

    def update_state(self, state: Dict[str, Any]):
        with self._acquire_lock():
            self.latest_state = dict(state)

    def _start_worker(self):
        self._worker_thread = threading.Thread(
            target=self._run_loop,
            name="cloud-sync-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def close(self):
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        if self.gps_manager is not None:
            try:
                self.gps_manager.close()
            except Exception:
                pass

    def build_telemetry_payload(self) -> Dict[str, Any]:
        with self._acquire_lock():
            st = dict(self.latest_state)
            esp = self.esp_context
            cam = self.camera_context

        esp_connected = getattr(esp, "connected", False) if esp else False
        esp_port = getattr(esp, "port", "") if esp else ""
        esp_baudrate = getattr(esp, "baudrate", 115200) if esp else 115200
        esp_reset_count = getattr(esp, "reset_count", 0) if esp else 0
        raw_telemetry = getattr(esp, "latest_telemetry", "") if esp else ""

        co2_ppm = 450.0
        gas_level = 0
        gas_status = "An toàn"
        raw_adc = 0
        vibration_g = 0.02
        gps_lat = gps_lon = gps_speed_kmh = gps_alt = gps_heading = 0.0
        gps_sats = 0
        gps_fix = "NO_FIX"

        if self.gps_manager is not None:
            if raw_telemetry:
                if "{" in raw_telemetry and "}" in raw_telemetry:
                    try:
                        json_str = raw_telemetry[raw_telemetry.find("{") : raw_telemetry.rfind("}") + 1]
                        jdata = json.loads(json_str)
                        self.gps_manager.update_from_telemetry_dict(jdata)
                    except Exception:
                        pass
                elif raw_telemetry.startswith("$"):
                    self.gps_manager.parse_nmea_sentence(raw_telemetry)
                elif "GPS_FIX:" in raw_telemetry or "SPD:" in raw_telemetry:
                    try:
                        tdict = {}
                        for tok in raw_telemetry.replace("FDESP:", "").split(","):
                            if ":" in tok:
                                k, v = tok.strip().split(":", 1)
                                tdict[k.upper().strip()] = v.strip()
                        if "LAT" in tdict and "LON" in tdict:
                            self.gps_manager.update_from_telemetry_dict({
                                "lat": float(tdict["LAT"]),
                                "lon": float(tdict["LON"]),
                                "spd": float(tdict.get("SPD", 0.0)),
                                "is_valid": int(tdict.get("GPS_FIX", 1)),
                                "sats": int(tdict.get("SAT", 0)),
                                "altitude": float(tdict.get("ALT", 10.0)),
                            })
                    except Exception:
                        pass
            gps_data = self.gps_manager.get_snapshot()
        else:
            gps_data = {
                "lat": round(gps_lat, 6),
                "lon": round(gps_lon, 6),
                "speed_kmh": round(gps_speed_kmh, 1),
                "altitude_m": round(gps_alt, 1),
                "heading_deg": round(gps_heading, 1),
                "satellites": gps_sats,
                "status": gps_fix,
                "trip_km": 0.0,
                "is_valid": gps_fix != "NO_FIX",
                "google_maps_url": f"https://www.google.com/maps?q={gps_lat},{gps_lon}",
            }

        if raw_telemetry:
            # Format A: JSON từ FDESP:{"seq":...,"co2_ppm":...,"gps_lat":...}
            if "{" in raw_telemetry and "}" in raw_telemetry:
                try:
                    json_str = raw_telemetry[raw_telemetry.find("{") : raw_telemetry.rfind("}") + 1]
                    jdata = json.loads(json_str)
                    if "co2_ppm" in jdata:
                        co2_ppm = float(jdata["co2_ppm"])
                    if "co2_level" in jdata:
                        gas_level = int(jdata["co2_level"])
                    if "vibration_g" in jdata:
                        vibration_g = float(jdata["vibration_g"])
                    if "gps_lat" in jdata and float(jdata["gps_lat"]) != 0.0:
                        gps_lat = float(jdata["gps_lat"])
                        gps_fix = "FIX_3D"
                    if "gps_lon" in jdata and float(jdata["gps_lon"]) != 0.0:
                        gps_lon = float(jdata["gps_lon"])
                        gps_fix = "FIX_3D"
                    if "speed_kmh" in jdata:
                        gps_speed_kmh = float(jdata["speed_kmh"])
                except Exception:
                    pass
            # Format B: Chuỗi CSV CO2:...,ADC:...,GPS_FIX:...,SPD:...,LV:...
            else:
                try:
                    tokens = raw_telemetry.replace("FDESP:", "").replace("CO2:", "CO2=").replace("LV:", "LV=").replace("SPD:", "SPD=").split(",")
                    for tok in tokens:
                        if "=" in tok:
                            k, v = tok.strip().split("=", 1)
                            k = k.upper().strip()
                            v = v.strip()
                            if k in ("CO2", "MQ135", "GAS"):
                                co2_ppm = float(v)
                            elif k in ("LV", "LEVEL"):
                                gas_level = int(v)
                            elif k in ("ADC", "RAW"):
                                raw_adc = int(v)
                            elif k in ("LAT", "LATITUDE"):
                                gps_lat = float(v)
                                gps_fix = "FIX_3D"
                            elif k in ("LON", "LONGITUDE"):
                                gps_lon = float(v)
                                gps_fix = "FIX_3D"
                            elif k in ("SPD", "SPEED"):
                                gps_speed_kmh = float(v)
                            elif k in ("ALT", "ALTITUDE"):
                                gps_alt = float(v)
                            elif k in ("HEAD", "HEADING", "COURSE"):
                                gps_heading = float(v)
                            elif k in ("SAT", "SATS"):
                                gps_sats = int(v)
                except Exception:
                    pass

        if gas_level == 2:
            gas_status = "Nguy hiểm (Nồng độ khí độc/khói cao)"
        elif gas_level == 1:
            gas_status = "Cảnh báo (Bí khí trong khoang xe)"
        else:
            gas_status = "An toàn"

        last_voice_cmd = getattr(esp, "_last_voice_status", "NORMAL") if esp else "NORMAL"
        voice_playing = last_voice_cmd in ("DROWSY", "YAWNING", "DISTRACTED")
        current_clip = (
            "VOICE:1 (Cảnh báo ngủ gật nguy hiểm)" if last_voice_cmd == "DROWSY"
            else "VOICE:2 (Cảnh báo tài xế ngáp buồn ngủ)" if last_voice_cmd == "YAWNING"
            else "VOICE:3 (Cảnh báo mất tập trung/lệch góc nhìn)" if last_voice_cmd == "DISTRACTED"
            else "STOP (Yên lặng)"
        )

        cam_fps = float(st.get("ai_fps", 0.0))
        cam_latency = float(st.get("pipeline_latency_ms", 0.0))
        cam_error = getattr(cam, "error", "") if cam else ""
        cam_status = "ONLINE" if (cam_fps > 0 or cam_error == "") else "OFFLINE"

        access = {
            "local_ip": self.access_info.local_ip,
            "web_port": self.access_info.web_port,
            "dashboard_url": self.access_info.dashboard_url,
            "stream_url": self.access_info.stream_url,
            "public_tunnel_url": self.access_info.public_tunnel_url,
        }

        hw = {
            "cpu_percent": 0.0,
            "ram_mb": 0.0,
            "temp_c": None,
            "throttled": "0x0",
        }
        try:
            from AI.scripts.telemetry import get_system_metrics
            hw = get_system_metrics()
        except Exception:
            pass

        payload = {
            "vehicle_id": self.access_info.vehicle_id,
            "vehicle_name": self.access_info.vehicle_name,
            "driver_name": self.access_info.driver_name,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "access_info": access,
            "camera": {
                "status": cam_status,
                "fps": round(cam_fps, 1),
                "latency_ms": round(cam_latency, 1),
                "resolution": f"{os.getenv('CAMERA_WIDTH', '640')}x{os.getenv('CAMERA_HEIGHT', '480')}",
                "error": cam_error,
            },
            "esp": {
                "connected": esp_connected,
                "is_connected": esp_connected,
                "port": esp_port,
                "baudrate": esp_baudrate,
                "reset_count": esp_reset_count,
                "latest_telemetry": raw_telemetry,
                "adc_raw": raw_adc,
                "vibration_g": round(vibration_g, 3),
            },
            "gps": gps_data,
            "mq135": {
                "co2_ppm": round(co2_ppm, 1),
                "raw_adc": raw_adc,
                "mq135_raw": raw_adc,
                "mq135_ppm": round(co2_ppm, 1),
                "gas_level": gas_level,
                "air_quality_level": "GOOD" if gas_level == 0 else "WARNING" if gas_level == 1 else "DANGER",
                "air_quality_warn": gas_level > 0,
                "status_label": gas_status,
            },
            "speaker": {
                "current_voice": current_clip,
                "is_playing": voice_playing,
                "last_command": getattr(esp, "_last_status", "NORMAL") if esp else "NORMAL",
                "volume": 100,
            },
            "loa": {
                "alert_level": st.get("confirmed_alert", "NORMAL"),
                "alert_code": getattr(esp, "_last_status", "NORMAL") if esp else "NORMAL",
                "buzzer_active": voice_playing,
                "current_voice": current_clip,
                "volume_pct": 100,
            },
            "driver_state": {
                "alert": st.get("confirmed_alert", "NORMAL"),
                "eye_duration": round(float(st.get("eye_duration", 0.0)), 2),
                "mouth_duration": round(float(st.get("mouth_duration", 0.0)), 2),
                "distracted_duration": round(float(st.get("distracted_duration", 0.0)), 2),
                "head_pose": st.get("head_pose"),
                "detected_classes": st.get("detected_classes", []),
            },
            "hardware": hw,
            "pi": {
                "cpu_temp_c": hw.get("temp_c"),
                "ram_used_mb": hw.get("ram_mb", 0.0),
                "cpu_usage_pct": hw.get("cpu_percent", 0.0),
                "camera_fps": round(cam_fps, 1),
                "throttled": hw.get("throttled", "0x0"),
                "ai_status": "MONITORING_ACTIVE" if cam_fps > 0 else "STANDBY",
            },
            "device_id": self.access_info.vehicle_id,
            "timestamp_ms": int(time.time() * 1000),
        }
        return payload

    @staticmethod
    def _to_epoch_seconds(val: Any, fallback: float) -> float:
        """Chuẩn hóa mốc thời gian (float/int epoch, datetime.datetime, hoặc chuỗi ISO) về epoch seconds."""
        if val is None:
            return fallback
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        if hasattr(val, "timestamp"):
            try:
                return float(val.timestamp())
            except Exception:
                return fallback
        if isinstance(val, str):
            try:
                from datetime import datetime
                return float(datetime.fromisoformat(val).timestamp())
            except Exception:
                return fallback
        return fallback

    def dispatch_violation(
        self,
        alert_type: str,
        frame: Optional[Any],
        started_at: Optional[Any] = None,
        confirmed_at: Optional[Any] = None,
        duration_seconds: float = 0.0,
        confidence: Optional[float] = 1.0,
        meta: Optional[Dict[str, Any]] = None,
    ):
        """Đóng gói và đẩy sự kiện vi phạm kèm ảnh bằng chứng Base64 vào hàng đợi gửi."""
        if not self.enabled:
            return

        now = time.time()
        dur_s = float(duration_seconds or 0.0)
        conf_t = self._to_epoch_seconds(confirmed_at, now)
        start_t = self._to_epoch_seconds(started_at, conf_t - dur_s)
        conf_score = round(float(confidence), 2) if confidence is not None else 1.0

        event_id = f"EVT_{int(conf_t * 1000)}"

        # Luồng suy luận KHÔNG encode JPEG/base64 nữa (≈20-60ms CPU + ~100KB cấp phát mỗi sự kiện):
        # chỉ đẩy tham chiếu khung hình (bất biến) sang luồng cloud-sync xử lý.
        job = {
            "event_id": event_id,
            "alert_type": alert_type,
            "frame": frame,
            "start_t": start_t,
            "conf_t": conf_t,
            "dur_s": dur_s,
            "conf_score": conf_score,
            "meta": meta or {},
        }
        try:
            self._violation_queue.put_nowait(job)
        except queue.Full:
            # Hàng đợi đầy (mất mạng kéo dài): lưu thẳng xuống SQLite theo bản không ảnh để không mất sự kiện
            emit_error_event(
                code="E_RESP_BUFFER_OVERRUN",
                source="response",
                node_path="response.cloud_sync.violation_queue",
                err_class="performance",
                message=f"Hàng đợi vi phạm RAM đầy (maxsize=8) cho {alert_type}; chuyển sang SQLite spool",
                recoverable=True,
                trace_id=event_id,
            )
            job["frame"] = None
            self._spool_to_db("/api/v1/violation", self._build_violation_payload(job))

    def _build_violation_payload(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Dựng payload vi phạm (encode ảnh bằng chứng) - chạy ở luồng nền cloud-sync."""
        evidence_b64 = ""
        frame = job.get("frame")
        if frame is not None:
            try:
                quality = int(os.getenv("EVIDENCE_JPEG_QUALITY", "70"))
                ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
                if ok:
                    evidence_b64 = "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode("ascii")
            except Exception as e:
                print(f"[CLOUD SYNC] Khong the encode anh bang chung: {e}")

        telemetry = self.build_telemetry_payload()
        return {
            "event_id": job["event_id"],
            "vehicle_id": self.access_info.vehicle_id,
            "driver_name": self.access_info.driver_name,
            "alert_type": job["alert_type"],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(job["start_t"])),
            "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(job["conf_t"])),
            "duration_seconds": round(max(0.0, job["dur_s"]), 2),
            "confidence": job["conf_score"],
            "gps": telemetry["gps"],
            "mq135": telemetry["mq135"],
            "speaker_alert": telemetry["speaker"]["current_voice"],
            "access_info": telemetry["access_info"],
            "evidence_image": evidence_b64,
            "meta": job["meta"],
        }

    def _send_telemetry(self, telemetry: Dict[str, Any]) -> Tuple[bool, str]:
        """Ưu tiên đi qua ESP32 Gateway (Pi không mở HTTP); tự dự phòng HTTP trực tiếp."""
        esp = self.esp_context
        if (
            self.uplink_mode == "esp32_gateway"
            and esp is not None
            and getattr(esp, "gateway_ready", False)
            and esp.send_telemetry_frame(telemetry)
        ):
            self.telemetry_via_esp += 1
            self.last_telemetry_route = "ESP32_GATEWAY"
            return True, "VIA_ESP32"
        self.last_telemetry_route = "PI_DIRECT_HTTP"
        return self._http_post("/api/v1/telemetry", telemetry)

    def _send_violation(self, v_item: Dict[str, Any]) -> Tuple[bool, str]:
        """Gửi sự kiện vi phạm: thử HTTP trực tiếp trước (để giữ nguyên ảnh Base64 nếu Pi có mạng),
        nếu thất bại hoặc đang ở chế độ esp32_gateway không có HTTP trực tiếp thì đẩy qua ESP32 Gateway."""
        esp = self.esp_context
        ok, resp = self._http_post("/api/v1/violation", v_item, timeout_s=4.0)
        if ok:
            return True, resp
        if (
            self.uplink_mode == "esp32_gateway"
            and esp is not None
            and getattr(esp, "gateway_ready", False)
            and hasattr(esp, "send_violation_frame")
            and esp.send_violation_frame(v_item)
        ):
            self.telemetry_via_esp += 1
            self.last_telemetry_route = "ESP32_GATEWAY"
            return True, "VIA_ESP32"
        return ok, resp

    def _run_loop(self):
        last_telemetry_time = 0.0
        last_discover_time = 0.0

        while not self._stop_event.is_set():
            now = time.monotonic()

            cur_ip = get_local_ip()  # có cache 10s
            if cur_ip and cur_ip != "127.0.0.1" and cur_ip != self.access_info.local_ip:
                self.access_info = VehicleAccessInfo.create_default()

            while not self._violation_queue.empty():
                try:
                    job = self._violation_queue.get_nowait()
                except queue.Empty:
                    break
                v_item = self._build_violation_payload(job)
                ok, resp = self._send_violation(v_item)
                if ok:
                    self.total_violations_sent += 1
                else:
                    emit_error_event(
                        code="E_RESP_SEND",
                        source="response",
                        node_path="response.cloud_sync.violation",
                        err_class="source",
                        message=f"Gửi vi phạm {v_item.get('alert_type')} thất bại ({resp}) -> lưu SQLite spool",
                        recoverable=True,
                        trace_id=str(v_item.get("event_id", "vio")),
                    )
                    self._spool_to_db("/api/v1/violation", v_item)

            if now - last_telemetry_time >= self.telemetry_interval:
                telemetry = self.build_telemetry_payload()
                ok, resp = self._send_telemetry(telemetry)
                if ok:
                    self.server_online = True
                    self.last_sync_time = time.time()
                    self.last_sync_status = "ĐANG_KẾT_NỐI" if resp != "VIA_ESP32" else "ĐANG_KẾT_NỐI (QUA ESP32)"
                    self.total_packets_sent += 1
                    self._discover_backoff_s = 30.0
                    if resp != "VIA_ESP32":
                        self._drain_spool()
                else:
                    was_online = self.server_online
                    self.server_online = False
                    self.last_sync_status = f"MẤT_KẾT_NỐI ({resp})"
                    if was_online:
                        emit_error_event(
                            code="E_RESP_SEND",
                            source="response",
                            node_path="response.cloud_sync.telemetry",
                            err_class="source",
                            message=f"Mất kết nối đồng bộ máy chủ ({self.server_url}): {resp}",
                            recoverable=True,
                        )
                    # Dò lại IP Server bằng UDP broadcast (nhẹ), lùi dần 30s -> 300s để không tạo
                    # các đợt tải mạng/CPU định kỳ khi Server tắt lâu.
                    if now - last_discover_time >= self._discover_backoff_s:
                        last_discover_time = now
                        self._discover_backoff_s = min(300.0, self._discover_backoff_s * 2.0)
                        discovered_url = auto_discover_pc_server(self.server_url)
                        if discovered_url and discovered_url != self.server_url:
                            print(f"[CLOUD SYNC] Đã tự động dò thấy IP mới của Server PC trên Wi-Fi: {discovered_url}")
                            self.server_url = discovered_url
                            os.environ["REMOTE_SERVER_URL"] = discovered_url

                last_telemetry_time = now

            self._stop_event.wait(0.5)

    def _http_post(self, endpoint: str, data: Dict[str, Any], timeout_s: float = 2.0) -> Tuple[bool, str]:
        """Gửi HTTP POST chuẩn bằng thư viện chuẩn urllib."""
        if not self.server_url:
            return False, "SERVER_URL_CHUA_CAU_HINH"

        url = f"{self.server_url}{endpoint}"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": f"DriverMonitorEdge/{self.access_info.vehicle_id}",
                **({"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                status = getattr(resp, "status", 200)
                if 200 <= int(status) < 300:
                    raw_resp = resp.read().decode("utf-8", errors="replace").strip()
                    if raw_resp and "downlink_xyz" in raw_resp:
                        try:
                            parsed_resp = json.loads(raw_resp)
                            if isinstance(parsed_resp, dict) and "downlink_xyz" in parsed_resp and self.gps_manager:
                                dl = parsed_resp["downlink_xyz"]
                                if hasattr(self.gps_manager, "inject_server_xyz"):
                                    self.gps_manager.inject_server_xyz(
                                        lat=float(dl.get("lat", 0.0)),
                                        lon=float(dl.get("lon", 0.0)),
                                        alt_m=float(dl.get("alt_m", dl.get("altitude_m", dl.get("alt", 0.0)))),
                                        speed_kmh=float(dl.get("speed_kmh", dl.get("speed", 0.0))),
                                    )
                        except Exception:
                            pass
                    return True, f"HTTP_{status}"
                return False, f"HTTP_{status}"
        except urllib.error.HTTPError as he:
            return False, f"HTTP_ERR_{he.code}"
        except urllib.error.URLError as ue:
            return False, f"CONN_ERR"
        except Exception as ex:
            return False, str(ex)

    def _spool_to_db(self, endpoint: str, payload: Dict[str, Any]):
        """Lưu gói tin vào SQLite khi mất kết nối mạng."""
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
            now_ts = time.time()
            with self._acquire_lock():
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.execute(
                    "INSERT INTO pending_queue (endpoint, payload, created_at, attempts) VALUES (?, ?, ?, 0)",
                    (endpoint, payload_json, now_ts),
                )
                conn.commit()
                # Đếm số lượng chờ gửi
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM pending_queue")
                self.pending_spool_count = cur.fetchone()[0]
                conn.close()
        except Exception as e:
            print(f"[CLOUD SYNC DB ERROR] {e}")

    def _drain_spool(self, max_items: int = 20):
        """Gửi bù các gói tin vi phạm/telemetry đã bị nghẽn trong lúc mất mạng.

        PROPOSE-CR-001 Bước 3: Tuyệt đối KHÔNG giữ self._lock trong lúc gọi mạng _http_post()
        để không bao giờ chặn luồng suy luận AI khi update_state().
        """
        try:
            with self._acquire_lock():
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                cur = conn.cursor()
                rows = cur.execute(
                    "SELECT id, endpoint, payload, attempts FROM pending_queue WHERE next_attempt_at <= ? ORDER BY id LIMIT ?",
                    (time.time(), max_items),
                ).fetchall()
                if not rows:
                    cur.execute("SELECT COUNT(*) FROM pending_queue")
                    self.pending_spool_count = cur.fetchone()[0]
                    conn.close()
                    return
                conn.close()

            # Thực hiện truyền HTTP hoàn toàn BÊN NGOÀI khóa RLock
            delivered_ids: List[Tuple[int, str]] = []
            failed_update: Optional[Tuple[float, int]] = None
            for row_id, endpoint, payload_str, attempts in rows:
                try:
                    payload = json.loads(payload_str)
                    ok, _ = self._http_post(endpoint, payload, timeout_s=2.5)
                    if ok:
                        delivered_ids.append((row_id, endpoint))
                    else:
                        next_at = time.time() + min(60.0, 2.0 ** min(attempts, 6))
                        failed_update = (next_at, row_id)
                        break  # Tạm ngừng xả queue nếu server lại nghẽn
                except Exception:
                    pass

            with self._acquire_lock():
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                cur = conn.cursor()
                for row_id, endpoint in delivered_ids:
                    conn.execute("DELETE FROM pending_queue WHERE id = ?", (row_id,))
                    if endpoint == "/api/v1/violation":
                        self.total_violations_sent += 1
                    else:
                        self.total_packets_sent += 1
                if failed_update is not None:
                    conn.execute(
                        "UPDATE pending_queue SET attempts = attempts + 1, next_attempt_at = ? WHERE id = ?",
                        failed_update,
                    )
                conn.commit()
                cur.execute("SELECT COUNT(*) FROM pending_queue")
                self.pending_spool_count = cur.fetchone()[0]
                conn.close()
        except Exception as e:
            print(f"[CLOUD SYNC DRAIN ERROR] {e}")

    def get_sync_stats(self) -> Dict[str, Any]:
        """Trả về thông tin kết nối đồng bộ cho Web Dashboard hiển thị."""
        spool_count = self.pending_spool_count
        now_m = time.monotonic()
        # Dashboard poll liên tục: chỉ mở SQLite để đếm tối đa mỗi 5s (còn lại dùng giá trị đệm)
        if now_m - getattr(self, "_spool_count_ts", 0.0) >= 5.0:
            self._spool_count_ts = now_m
            try:
                with self._acquire_lock():
                    conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM pending_queue")
                    spool_count = cur.fetchone()[0]
                    self.pending_spool_count = spool_count
                    conn.close()
            except Exception:
                pass
        return {
            "server_url": self.server_url,
            "enabled": self.enabled,
            "online": self.server_online,
            "last_sync_status": self.last_sync_status,
            "last_sync_time": (
                time.strftime("%H:%M:%S", time.localtime(self.last_sync_time))
                if self.last_sync_time > 0
                else "--"
            ),
            "packets_sent": self.total_packets_sent,
            "violations_sent": self.total_violations_sent,
            "pending_spool": spool_count,
            "uplink_mode": self.uplink_mode,
            "telemetry_route": self.last_telemetry_route,
            "telemetry_via_esp": self.telemetry_via_esp,
            "lock_metrics": {
                "last_wait_ms": self.last_lock_wait_ms,
                "max_wait_ms": self.max_lock_wait_ms,
                "last_hold_ms": self.last_lock_hold_ms,
                "max_hold_ms": self.max_lock_hold_ms,
            },
            "access_info": {
                "local_ip": self.access_info.local_ip,
                "dashboard_url": self.access_info.dashboard_url,
                "stream_url": self.access_info.stream_url,
                "public_tunnel_url": self.access_info.public_tunnel_url,
            },
        }
