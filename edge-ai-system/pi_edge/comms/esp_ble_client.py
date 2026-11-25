"""Hardened ESP32 #2 UART Client (COMMS_BLE_WIFI_HUB — Phase 6 / LCK-05 v2).

Features:
  - Strictly decoupled from ESP32 #1 safety speaker path (<5ms never blocked)
  - Strict ACK/NACK & BLE event classifier (MUTE_ALERT, SOS, ACK_AWAKE, REST_BREAK_*)
  - Auto role discovery via WHOAMI (prevents /dev/ttyUSB0 vs /dev/ttyUSB1 swap bugs!)
  - Dynamic NVS config sync (CFG_VEHICLE / CFG_SERVER / CFG_TOKEN — never logs secrets)
  - Periodic Pi heartbeat (HB:{...}) to keep ESP32 #2 Wi-Fi OFF while Pi is healthy
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from contracts.geo_math import classify_gps_wiring, estimate_accuracy_m, is_valid_wgs84


@dataclass
class BleHubAckResult:
    ok: bool
    command: str
    ack_line: str
    latency_ms: float
    error: str = ""


def classify_ble_hub_line(line: str) -> Tuple[str, str]:
    """Classify one ASCII line from ESP32 #2 (COMMS_BLE_WIFI_HUB) without substring bugs."""
    s = (line or "").strip()
    if not s:
        return "OTHER", ""
    if s.startswith("FDBLE:"):
        return "STATUS", "FDBLE"
    if s == "PONG" or s.startswith("EVT,PONG"):
        return "ACK", "PING"
    if s.startswith("EVT,WHOAMI,") or s.startswith("EVT,ROLE,"):
        return "ACK", "WHOAMI"
    if s.startswith("EVT,NACK,"):
        parts = s.split(",")
        return "NACK", (parts[2].upper() if len(parts) >= 3 else "UNKNOWN")
    if s.startswith("EVT,ACK,"):
        parts = s.split(",")
        return "ACK", (parts[2].upper() if len(parts) >= 3 else "UNKNOWN")
    if s.startswith("EVT,BLE_ACTION,"):
        parts = s.split(",")
        return "BLE_ACTION", (parts[2].upper() if len(parts) >= 3 else "UNKNOWN")
    if s.startswith("EVT,BLE_STATE,"):
        parts = s.split(",")
        return "BLE_STATE", (parts[2].upper() if len(parts) >= 3 else "UNKNOWN")
    if s.startswith("EVT,SURVIVAL_WIFI,") or s.startswith("EVT,SURVIVAL_POST,"):
        parts = s.split(",")
        return "SURVIVAL", (",".join(parts[1:]).upper())
    if "BROWNOUT" in s.upper():
        return "HW_EVENT", "ESP_HUB_BROWNOUT"
    if s.startswith("EVT,RESET_REASON,") and "POWERON" not in s.upper():
        return "HW_EVENT", "ESP_HUB_RESET"
    return "OTHER", ""


def _disable_linux_hupcl(ser: Any) -> None:
    try:
        import termios
        fd = ser.fileno()
        attrs = termios.tcgetattr(fd)
        attrs[2] &= ~termios.HUPCL
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass


def _parse_role_from_whoami(line: str) -> str:
    s = (line or "").strip()
    if s.startswith("EVT,ROLE,"):
        parts = s.split(",")
        if len(parts) >= 3 and parts[2].strip():
            return parts[2].strip().upper()
    if "ROLE=" in s:
        for part in s.split(","):
            if part.strip().upper().startswith("ROLE="):
                return part.split("=", 1)[1].strip().upper()
    return "UNKNOWN"


def _cmd_key(cmd: str) -> str:
    c = (cmd or "").strip()
    up = c.upper()
    if up in ("PING", "WHOAMI", "STATUS", "TEST_SURVIVAL", "STOP_SURVIVAL"):
        return up
    if ":" in up:
        return up.split(":", 1)[0]
    return up


def probe_esp_role(port: str, baudrates: Sequence[int] = (115200, 921600), timeout_s: float = 2.6) -> Optional[Tuple[str, int]]:
    """Probe a serial port via WHOAMI/passive JSON across ESP32 cold-boot window (clears HUPCL & DTR/RTS)."""
    try:
        import serial
    except Exception:
        return None
    for baud in baudrates:
        ser = None
        try:
            ser = serial.Serial()
            ser.port = port
            ser.baudrate = int(baud)
            ser.timeout = 0.2
            ser.write_timeout = 0.4
            try:
                ser.exclusive = True
            except Exception:
                pass
            ser.dtr = False
            ser.rts = False
            ser.open()
            _disable_linux_hupcl(ser)
            try:
                ser.dtr = False
                ser.rts = False
                time.sleep(0.05)
                ser.reset_input_buffer()
            except Exception:
                pass
            deadline = time.monotonic() + float(timeout_s)
            next_probe = 0.0
            while time.monotonic() < deadline:
                now_m = time.monotonic()
                if now_m >= next_probe:
                    try:
                        ser.write(b"WHOAMI\n")
                    except Exception:
                        break
                    next_probe = now_m + 0.75
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if line.startswith("EVT,WHOAMI,") or line.startswith("EVT,ROLE,"):
                    role = _parse_role_from_whoami(line)
                    if role != "UNKNOWN":
                        return role, int(baud)
                elif line.startswith("FDESP:") or line.startswith("FDBLE:"):
                    try:
                        obj = json.loads(line.split(":", 1)[1].strip())
                        if isinstance(obj, dict) and obj.get("role"):
                            return str(obj["role"]).upper(), int(baud)
                    except Exception:
                        pass
        except Exception:
            continue
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
    return None


def discover_esp_ports_by_role(candidate_ports: Sequence[str]) -> Dict[str, Tuple[str, int]]:
    """Map discovered roles ('AUDIO_MQ135_NODE', 'COMMS_BLE_GPS_HUB', etc.) -> (port, baudrate)."""
    found: Dict[str, Tuple[str, int]] = {}
    for port in candidate_ports:
        res = probe_esp_role(port)
        if res is not None:
            role, baud = res
            found[role] = (port, baud)
    return found


class EspBleHubClient:
    """UART client for ESP32 #2 (COMMS_BLE_GPS_HUB — LCK-05 v3)."""

    def __init__(
        self,
        port: str = "/dev/esp_ble",
        baudrate: int = 115200,
        mode: str = "DISABLED",
        on_ble_action: Optional[Callable[[str], None]] = None,
        on_ble_state: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.port = port
        self.baudrate = int(baudrate)
        self.mode = (mode or "DISABLED").strip().upper()
        self.on_ble_action = on_ble_action
        self.on_ble_state = on_ble_state

        self._serial: Any = None
        self._serial_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

        self._waiter_lock = threading.Lock()
        self._waiters: Dict[str, Tuple[threading.Event, List[Tuple[str, str]]]] = {}

        self._state_lock = threading.Lock()
        self.connected = False
        self.device_role = "UNKNOWN"
        self.ble_phone_connected = False
        self.survival_wifi_on = False
        self.brownout_seen = False
        self.reset_count = 0
        self.latest_status: Dict[str, Any] = {}
        self.latest_gps: Dict[str, Any] = {
            "lat": None,
            "lon": None,
            "speed_kmh": 0.0,
            "altitude_m": None,
            "heading_deg": None,
            "hdop": None,
            "accuracy_m": None,
            "fix_quality": 0,
            "satellites": 0,
            "sats_in_view": None,
            "gps_rx_bytes": None,
            "nmea_ok": None,
            "nmea_bad": None,
            "wiring_status": "WAITING_SATELLITE_LOCK",
            "is_valid": False,
            "source": "esp32_hub_gps",
        }
        self._recent_actions: deque[str] = deque(maxlen=20)
        self._last_hb_sent_mono = 0.0
        self._last_status_req_mono = 0.0

    def _open_serial_port(self) -> Any:
        import serial
        ser = serial.Serial()
        ser.port = self.port
        ser.baudrate = self.baudrate
        ser.timeout = 0.2
        ser.write_timeout = 0.5
        try:
            ser.exclusive = True
        except Exception:
            pass
        ser.dtr = False
        ser.rts = False
        ser.open()
        _disable_linux_hupcl(ser)
        try:
            ser.dtr = False
            ser.rts = False
            time.sleep(0.05)
            ser.reset_input_buffer()
        except Exception:
            pass
        return ser

    def _drop_serial_unlocked(self) -> None:
        ser = self._serial
        self._serial = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        with self._state_lock:
            self.connected = False
            self.ble_phone_connected = False
            self.latest_gps["is_valid"] = False
            self.latest_gps["lat"] = None
            self.latest_gps["lon"] = None

    def start(self) -> bool:
        if self.mode == "DISABLED":
            return False
        if self.mode == "MOCK":
            with self._state_lock:
                self.connected = True
                self.device_role = "COMMS_BLE_GPS_HUB_MOCK"
            return True
        self._stop.clear()
        opened = False
        try:
            ser = self._open_serial_port()
            with self._serial_lock:
                self._serial = ser
            with self._state_lock:
                self.connected = True
            opened = True
        except Exception:
            with self._state_lock:
                self.connected = False
        if self._reader_thread is None or not self._reader_thread.is_alive():
            self._reader_thread = threading.Thread(target=self._reader_loop, name="esp-ble-reader", daemon=True)
            self._reader_thread.start()
        return opened

    def close(self) -> None:
        self._stop.set()
        with self._serial_lock:
            ser = self._serial
            self._serial = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        with self._state_lock:
            self.connected = False

    def handle_incoming_line(self, line: str) -> None:
        s = (line or "").strip()
        if not s:
            return
        kind, key = classify_ble_hub_line(s)

        if kind == "STATUS":
            try:
                data = json.loads(s[len("FDBLE:"):].strip())
                if isinstance(data, dict):
                    raw_valid = bool(data.get("gps_fix") or data.get("is_valid"))
                    raw_lat = data.get("lat")
                    raw_lon = data.get("lon")
                    gps_valid = raw_valid and is_valid_wgs84(raw_lat, raw_lon)
                    lat = float(raw_lat) if gps_valid else None
                    lon = float(raw_lon) if gps_valid else None
                    alt = data.get("alt_m", data.get("altitude_m"))
                    hdg = data.get("heading", data.get("heading_deg"))
                    raw_hdop = data.get("hdop")
                    hdop = None
                    if raw_hdop is not None:
                        try:
                            hv = float(raw_hdop)
                            if 0.3 <= hv < 99.0:
                                hdop = round(hv, 2)
                        except (TypeError, ValueError):
                            hdop = None
                    fix_q = int(data.get("fix_q") or data.get("fix_quality") or (1 if gps_valid else 0))
                    sats = int(data.get("sats") or data.get("satellites") or 0)
                    sats_view = int(data["sats_view"]) if data.get("sats_view") is not None else None
                    rx_bytes = int(data["gps_rx_bytes"]) if data.get("gps_rx_bytes") is not None else None
                    nmea_ok = int(data["nmea_ok"]) if data.get("nmea_ok") is not None else None
                    nmea_bad = int(data["nmea_bad"]) if data.get("nmea_bad") is not None else None
                    acc_m = estimate_accuracy_m(hdop=hdop, fix_quality=fix_q, satellites=sats) if gps_valid else None
                    wiring = classify_gps_wiring(
                        is_valid=gps_valid,
                        satellites=sats,
                        sats_in_view=sats_view,
                        gps_rx_bytes=rx_bytes,
                        nmea_ok=nmea_ok,
                        nmea_bad=nmea_bad,
                    )
                    with self._state_lock:
                        self.latest_status = data
                        self.ble_phone_connected = bool(data.get("ble_connected"))
                        self.survival_wifi_on = bool(data.get("wifi_survival_on"))
                        if data.get("role"):
                            self.device_role = str(data["role"]).upper()
                        if "gps_fix" in data or "sats" in data or "lat" in data:
                            self.latest_gps = {
                                "lat": lat,
                                "lon": lon,
                                "speed_kmh": float(data.get("speed_kmh") or 0.0) if gps_valid else 0.0,
                                "altitude_m": float(alt) if (gps_valid and alt is not None) else None,
                                "heading_deg": float(hdg) if (gps_valid and hdg is not None) else None,
                                "hdop": hdop,
                                "accuracy_m": acc_m,
                                "fix_quality": fix_q,
                                "satellites": sats,
                                "sats_in_view": sats_view,
                                "gps_rx_bytes": rx_bytes,
                                "nmea_ok": nmea_ok,
                                "nmea_bad": nmea_bad,
                                "wiring_status": wiring,
                                "is_valid": gps_valid,
                                "source": "esp32_hub_gps",
                            }
            except Exception:
                pass
            with self._waiter_lock:
                w = self._waiters.get("STATUS")
                if w is not None:
                    w[1].append(("ACK", s))
                    w[0].set()
            return

        if kind in ("ACK", "NACK"):
            if kind == "ACK" and key == "WHOAMI":
                role_val = _parse_role_from_whoami(s)
                with self._state_lock:
                    self.device_role = role_val
            with self._waiter_lock:
                w = self._waiters.get(key)
                if w is not None:
                    w[1].append((kind, s))
                    w[0].set()
            return

        if kind == "BLE_STATE":
            is_conn = key == "PHONE_CONNECTED"
            with self._state_lock:
                self.ble_phone_connected = is_conn
            if self.on_ble_state:
                try:
                    self.on_ble_state(is_conn)
                except Exception:
                    pass
            return

        if kind == "BLE_ACTION":
            with self._state_lock:
                self._recent_actions.append(key)
            if self.on_ble_action:
                try:
                    self.on_ble_action(key)
                except Exception:
                    pass
            return

        if kind == "SURVIVAL":
            if "SURVIVAL_WIFI,ON" in key:
                with self._state_lock:
                    self.survival_wifi_on = True
            elif "SURVIVAL_WIFI,OFF" in key:
                with self._state_lock:
                    self.survival_wifi_on = False
            return

        if kind == "HW_EVENT":
            with self._state_lock:
                if key == "ESP_HUB_BROWNOUT":
                    self.brownout_seen = True
                    self.reset_count += 1
                elif key == "ESP_HUB_RESET":
                    self.reset_count += 1

    def _reader_loop(self) -> None:
        backoff_s = 1.0
        while not self._stop.is_set():
            with self._serial_lock:
                ser = self._serial
            if ser is None:
                if self.mode != "SERIAL":
                    break
                self._stop.wait(backoff_s)
                if self._stop.is_set():
                    break
                try:
                    new_ser = self._open_serial_port()
                    with self._serial_lock:
                        self._serial = new_ser
                    with self._state_lock:
                        self.connected = True
                    backoff_s = 1.0
                    # Re-push cached survival config after reconnect (F-019)
                    cached = getattr(self, "_cached_runtime_cfg", None)
                    if cached:
                        threading.Thread(
                            target=self._push_cached_runtime_cfg,
                            name="esp-ble-resync",
                            daemon=True,
                        ).start()
                except Exception:
                    backoff_s = min(5.0, backoff_s * 1.5)
                continue
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="ignore").strip()
                if line:
                    self.handle_incoming_line(line)
            except Exception:
                with self._serial_lock:
                    self._drop_serial_unlocked()
                backoff_s = 1.0

    def send_command(self, cmd: str, wait_ack: bool = True, timeout_s: float = 0.8) -> BleHubAckResult:
        t0 = time.perf_counter()
        c = (cmd or "").strip()
        if self.mode == "DISABLED":
            return BleHubAckResult(False, c, "", 0.0, error="ble_hub_disabled")
        if self.mode == "MOCK":
            return BleHubAckResult(True, c, f"EVT,ACK,{_cmd_key(c)},MOCK", round((time.perf_counter() - t0) * 1000.0, 2))

        ack_key = _cmd_key(c)
        ev = threading.Event()
        box: List[Tuple[str, str]] = []
        if wait_ack:
            with self._waiter_lock:
                self._waiters[ack_key] = (ev, box)

        try:
            with self._write_lock:
                with self._serial_lock:
                    ser = self._serial
                if ser is None:
                    return BleHubAckResult(False, c, "", 0.0, error="serial_not_connected")
                try:
                    ser.write((c + "\n").encode("utf-8"))
                    if wait_ack:
                        ser.flush()
                except Exception as exc:
                    with self._serial_lock:
                        self._drop_serial_unlocked()
                    return BleHubAckResult(
                        False, c, "", round((time.perf_counter() - t0) * 1000.0, 2), error=f"serial_io_error:{exc}"
                    )
            if not wait_ack:
                return BleHubAckResult(True, c, "SENT_NO_ACK_WAIT", round((time.perf_counter() - t0) * 1000.0, 2))

            got = ev.wait(timeout=max(0.05, float(timeout_s)))
            lat_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if not got or not box:
                return BleHubAckResult(False, c, "", lat_ms, error="ACK_TIMEOUT")
            kind, reply = box[-1]
            if kind == "NACK":
                return BleHubAckResult(False, c, reply, lat_ms, error="ESP32_HUB_NACK")
            return BleHubAckResult(True, c, reply, lat_ms)
        finally:
            if wait_ack:
                with self._waiter_lock:
                    cur = self._waiters.get(ack_key)
                    if cur is not None and cur[0] is ev:
                        self._waiters.pop(ack_key, None)

    def _push_cached_runtime_cfg(self) -> None:
        cached = getattr(self, "_cached_runtime_cfg", None)
        if not cached:
            return
        try:
            self.sync_runtime_config(*cached)
        except Exception:
            pass

    def sync_runtime_config(
        self,
        vehicle_id: str,
        server_url: str,
        auth_token: str,
        wifi_ssid: str = "",
        wifi_pass: str = "",
    ) -> None:
        """Push dynamic vehicle/server/token/Wi-Fi into ESP32 #2 NVS Flash without logging secrets."""
        self._cached_runtime_cfg = (vehicle_id, server_url, auth_token, wifi_ssid, wifi_pass)
        if not self.connected or self.mode == "DISABLED":
            return
        if vehicle_id:
            self.send_command(f"CFG_VEHICLE:{vehicle_id}", wait_ack=True, timeout_s=0.6)
        if server_url and (server_url.startswith("http://") or server_url.startswith("https://")):
            self.send_command(f"CFG_SERVER:{server_url}", wait_ack=True, timeout_s=0.6)
        if auth_token and len(auth_token) >= 8:
            self.send_command(f"CFG_TOKEN:{auth_token}", wait_ack=True, timeout_s=0.6)
        if wifi_ssid:
            clean_ssid = wifi_ssid.replace("|", "").replace("\r", "").replace("\n", "").strip()
            clean_pass = (wifi_pass or "").replace("\r", "").replace("\n", "")
            if clean_ssid:
                self.send_command(f"CFG_WIFI:{clean_ssid}|{clean_pass}", wait_ack=True, timeout_s=0.6)

    def send_heartbeat_if_due(
        self,
        sensor_snap: Dict[str, Any],
        now_mono: Optional[float] = None,
        interval_s: float = 1.5,
    ) -> bool:
        """Keep ESP32 #2 Wi-Fi OFF while Pi is healthy by sending lightweight HB JSON every 1.5s."""
        if not self.connected or self.mode == "DISABLED":
            return False
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        if (now_m - self._last_hb_sent_mono) < interval_s:
            return False
        self._last_hb_sent_mono = now_m
        gps = sensor_snap.get("gps") or {}
        hb = {
            "co2_ppm": round(float(sensor_snap.get("co2_ppm") or 0.0), 1),
            "speed_kmh": round(float(gps.get("speed_kmh") or 0.0), 1),
            "gps_valid": bool(gps.get("is_valid", False)),
            "lat": gps.get("lat"),
            "lon": gps.get("lon"),
        }
        res = self.send_command("HB:" + json.dumps(hb, separators=(",", ":")), wait_ack=False)
        return res.ok

    def send_notify(self, text: str) -> bool:
        if not self.connected or self.mode == "DISABLED":
            return False
        clean = (text or "").replace("\r", " ").replace("\n", " ").strip()[:160]
        if not clean:
            return False
        return self.send_command(f"NOTIFY:{clean}", wait_ack=False).ok

    def request_status_if_due(self, now_mono: Optional[float] = None, interval_s: float = 10.0) -> bool:
        """Poll lightweight STATUS JSON periodically so Dashboard tracks ESP32 #2 heap & Wi-Fi power."""
        if not self.connected or self.mode == "DISABLED":
            return False
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        if (now_m - self._last_status_req_mono) < interval_s:
            return False
        self._last_status_req_mono = now_m
        return self.send_command("STATUS", wait_ack=False).ok

    def get_gps_snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            return dict(self.latest_gps)

    def get_bluetooth_block(self) -> Optional[Dict[str, Any]]:
        if self.mode == "DISABLED":
            return None
        with self._state_lock:
            return {
                "hub_connected": self.connected,
                "hub_role": self.device_role,
                "phone_connected": self.ble_phone_connected,
                "survival_wifi_on": self.survival_wifi_on,
                "brownout": self.brownout_seen,
                "reset_count": self.reset_count,
                "free_heap": self.latest_status.get("free_heap"),
                "gps_fix": bool(self.latest_gps.get("is_valid", False)),
                "gps_sats": int(self.latest_gps.get("satellites") or 0),
                "gps_sats_in_view": self.latest_gps.get("sats_in_view"),
                "gps_hdop": self.latest_gps.get("hdop"),
                "gps_accuracy_m": self.latest_gps.get("accuracy_m"),
                "gps_rx_bytes": self.latest_gps.get("gps_rx_bytes"),
                "gps_wiring_status": self.latest_gps.get("wiring_status"),
                "last_action": self._recent_actions[-1] if self._recent_actions else None,
                "transport": "BLE_NUS",
                "device_id": "ESP32_HUB_BLE",
            }
