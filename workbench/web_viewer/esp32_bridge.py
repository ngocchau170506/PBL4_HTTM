"""Cầu nối giao tiếp hai chiều với bo mạch ESP32 (workbench/web_viewer, thành phần E).

Chỉ chạy trên PC trong môi trường workbench. Nhận telemetry (CO2, GPS, vận tốc) và gửi
các lệnh điều khiển còi/loa (ALERT:1, ALERT:2, SLEEP, STOP, MUTE, PING) tới ESP32 qua Serial (COM3).
Tuân thủ hợp đồng G2.5: khi chưa có GPS, đặt trạng thái connect.esp32 là DEGRADED.
Mọi test tự động chỉ dùng chế độ mock hoặc packet_provider; không tự mở cổng serial thật.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

try:
    import serial
except ImportError:
    serial = None

ALLOWED_COMMAND_REGEX = re.compile(r"^(CONNECT|ALERT:[12](?::\d+)?|SLEEP|STOP|MUTE|PING|VOICE:[1-5])$")
PREFIX_FDESP = "FDESP:"
PREFIX_CO2 = "CO2:"


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


def _safe_float(val: str, default: float = 0.0) -> float:
    cleaned = val.strip()
    if not cleaned:
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default


def _safe_int(val: str, default: int = 0) -> int:
    cleaned = val.strip()
    if not cleaned:
        return default
    try:
        return int(cleaned)
    except ValueError:
        return default


class Esp32Bridge:
    """Quản lý kết nối Serial với ESP32 và đồng bộ trạng thái vào StateHub."""

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = 115200,
        hub: Optional[Any] = None,
        packet_provider: Optional[Callable[[int], Optional[str]]] = None,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._hub = hub
        self._packet_provider = packet_provider
        self._clock = clock_ms

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._serial_obj: Any = None
        self._write_lock = threading.Lock()

        self._health: str = "DOWN"
        self._connected: bool = False
        self._seq: int = 0
        self._last_seen_ms: Optional[int] = None
        self._last_ack: str = ""
        self._co2_ppm: float = 0.0
        self._co2_level: int = 0
        self._vibration_g: float = 0.0
        self._gps_ready: bool = False
        self._gps_lat: Optional[float] = None
        self._gps_lon: Optional[float] = None
        self._speed_kmh: float = 0.0
        self._adc_raw: int = 0
        self._sent_commands: List[str] = []

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def health(self) -> str:
        return self._health

    @property
    def last_ack(self) -> str:
        return self._last_ack

    @property
    def sent_commands(self) -> List[str]:
        with self._write_lock:
            return list(self._sent_commands)

    def is_mock(self) -> bool:
        return self._port is None or self._port == "mock" or self._packet_provider is not None

    def start(self) -> None:
        if self._running:
            return
        self._running = True

        if not self.is_mock():
            if serial is None:
                self._health = "DOWN"
                self._record_error("E_CONN_OPEN", "Thư viện pyserial chưa được cài đặt")
                return
            try:
                self._serial_obj = serial.Serial(
                    port=self._port,
                    baudrate=self._baudrate,
                    timeout=0.2,
                    write_timeout=1.0,
                )
                self._connected = True
                self._health = "DEGRADED"
            except Exception as exc:
                self._connected = False
                self._health = "DOWN"
                self._record_error("E_CONN_OPEN", f"Không mở được cổng {self._port}: {exc}")
                return
        else:
            self._connected = True
            self._health = "DEGRADED"

        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._serial_obj is not None:
            try:
                self._serial_obj.close()
            except Exception as exc:
                self._record_error("E_CONN_OPEN", f"Lỗi đóng cổng: {exc}")
            self._serial_obj = None

        self._connected = False
        self._health = "DOWN"
        if self._hub is not None:
            self._hub.set_node_status("connect.esp32", "DOWN")

    def process_line(self, raw_line: str) -> Optional[Dict[str, Any]]:
        line = raw_line.strip()
        if not line:
            return None

        now = self._clock()
        self._last_seen_ms = now

        if line.startswith(PREFIX_FDESP):
            return self._parse_fdesp_line(line[len(PREFIX_FDESP):], now)
        elif line.startswith(PREFIX_CO2):
            return self._parse_co2_line(line, now)
        elif line.startswith("EVT,") or line == "PONG":
            self._last_ack = line
            if self._hub is not None:
                self._hub.set_meta("esp32_last_ack", line)
            return {"kind": "ack", "ack": line, "ts_ms": now}

        return None

    def _parse_fdesp_line(self, json_part: str, now: int) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(json_part)
        except Exception as exc:
            self._record_error("E_SIGNAL_CORRUPT", f"Lỗi phân tích JSON FDESP: {exc}")
            return None

        self._seq = int(payload.get("seq", self._seq + 1))
        self._co2_ppm = round(float(payload.get("co2_ppm", 0.0)), 1)
        if "co2_level" in payload:
            self._co2_level = int(payload["co2_level"])
        else:
            self._co2_level = 2 if self._co2_ppm >= 1500.0 else (1 if self._co2_ppm >= 1000.0 else 0)
        self._vibration_g = round(float(payload.get("vibration_g", 0.0)), 4)

        gps_ready_val = payload.get("gps_ready", 0.0)
        self._gps_ready = bool(float(gps_ready_val) > 0.5)
        self._gps_lat = payload.get("gps_lat")
        self._gps_lon = payload.get("gps_lon")
        self._speed_kmh = round(float(payload.get("speed_kmh", 0.0)), 1)

        self._health = "OK" if self._gps_ready else "DEGRADED"
        self._sync_to_hub()

        return {
            "kind": "fdesp",
            "seq": self._seq,
            "co2_ppm": self._co2_ppm,
            "co2_level": self._co2_level,
            "vibration_g": self._vibration_g,
            "gps_ready": self._gps_ready,
            "gps_lat": self._gps_lat,
            "gps_lon": self._gps_lon,
            "speed_kmh": self._speed_kmh,
            "health": self._health,
            "ts_ms": now,
        }

    def _parse_co2_line(self, line: str, now: int) -> Optional[Dict[str, Any]]:
        parts = line.split(",")
        data: Dict[str, str] = {}
        for p in parts:
            if ":" in p:
                k, v = p.split(":", 1)
                data[k.strip()] = v.strip()

        if "CO2" in data:
            self._co2_ppm = _safe_float(data["CO2"], self._co2_ppm)
        if "LV" in data:
            self._co2_level = _safe_int(data["LV"], 0)
        else:
            self._co2_level = 2 if self._co2_ppm >= 1500.0 else (1 if self._co2_ppm >= 1000.0 else 0)
        if "ADC" in data:
            self._adc_raw = _safe_int(data["ADC"], self._adc_raw)
        if "GPS_FIX" in data:
            self._gps_ready = data["GPS_FIX"] == "1"
        if "SPD" in data:
            self._speed_kmh = _safe_float(data["SPD"], self._speed_kmh)

        self._health = "OK" if self._gps_ready else "DEGRADED"
        self._sync_to_hub()

        return {
            "kind": "co2",
            "co2_ppm": self._co2_ppm,
            "co2_level": self._co2_level,
            "adc_raw": self._adc_raw,
            "gps_ready": self._gps_ready,
            "speed_kmh": self._speed_kmh,
            "health": self._health,
            "ts_ms": now,
        }

    def _sync_to_hub(self) -> None:
        if self._hub is None:
            return

        status = self.get_status()
        self._hub.set_meta("esp32", status)
        self._hub.set_node_status("connect.esp32", self._health)

    def _record_error(self, code: str, msg: str) -> None:
        if self._hub is not None:
            self._hub.add_error(
                code=code,
                node_path="connect.esp32",
                trace_id=f"esp32-err-{self._clock()}",
                message=msg,
            )

    def send_command(self, cmd: str) -> Dict[str, Any]:
        cleaned = cmd.strip()
        if not ALLOWED_COMMAND_REGEX.match(cleaned):
            raise ValueError(f"Lệnh ESP32 không hợp lệ: {cleaned!r}. Các lệnh cho phép: CONNECT, ALERT:1, ALERT:2, SLEEP, STOP, MUTE, PING")

        with self._write_lock:
            self._sent_commands.append(cleaned)

            if not self.is_mock() and self._serial_obj is not None:
                encoded = (cleaned + "\n").encode("utf-8")
                self._serial_obj.write(encoded)
                self._serial_obj.flush()
                return {
                    "ok": True,
                    "command": cleaned,
                    "port": self._port,
                    "ack": self._last_ack,
                }
            else:
                simulated_ack = self._simulate_ack(cleaned)
                self._last_ack = simulated_ack
                if self._hub is not None:
                    self._hub.set_meta("esp32_last_ack", simulated_ack)
                return {
                    "ok": True,
                    "command": cleaned,
                    "port": "mock",
                    "ack": simulated_ack,
                }

    @staticmethod
    def _simulate_ack(cmd: str) -> str:
        if cmd == "CONNECT":
            return "EVT,ACK,CONNECT"
        elif cmd.startswith("ALERT:1"):
            dur = cmd.split(":")[2] if cmd.count(":") >= 2 else "1500"
            return f"EVT,ACK,ALERT:1,dur={dur},i2s=1"
        elif cmd.startswith("ALERT:2"):
            dur = cmd.split(":")[2] if cmd.count(":") >= 2 else "1500"
            return f"EVT,ACK,ALERT:2,dur={dur},i2s=1"
        elif cmd == "SLEEP":
            return "EVT,ACK,SLEEP,i2s=1"
        elif cmd == "STOP":
            return "EVT,ACK,STOP"
        elif cmd == "MUTE":
            return "EVT,MUTE"
        elif cmd == "PING":
            return "PONG"
        elif cmd.startswith("VOICE:"):
            cid = cmd.split(":")[1] if ":" in cmd else "1"
            return f"EVT,ACK,VOICE,{cid},i2s=1"
        return "EVT,ACK"

    def _reader_loop(self) -> None:
        while self._running:
            if not self.is_mock() and self._serial_obj is not None:
                try:
                    raw_bytes = self._serial_obj.readline()
                    if raw_bytes:
                        line = raw_bytes.decode("utf-8", errors="ignore")
                        self.process_line(line)
                except Exception as exc:
                    self._health = "DOWN"
                    self._connected = False
                    self._record_error("E_CONN_LOST", f"Mất kết nối serial: {exc}")
                    break
            else:
                self._seq += 1
                if self._packet_provider is not None:
                    sim_line = self._packet_provider(self._seq)
                    if sim_line is not None:
                        self.process_line(sim_line)
                else:
                    mock_json = json.dumps({
                        "seq": self._seq,
                        "co2_ppm": 420.0,
                        "vibration_g": 0.02,
                        "gps_ready": 0.0,
                        "gps_lat": 0.0,
                        "gps_lon": 0.0,
                        "speed_kmh": 0.0,
                    })
                    self.process_line(PREFIX_FDESP + mock_json)
                time.sleep(0.5)

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "connected": self._connected,
            "port": self._port if self._port else "mock",
            "baudrate": self._baudrate,
            "health": self._health,
            "seq": self._seq,
            "co2_ppm": self._co2_ppm,
            "vibration_g": self._vibration_g,
            "gps_ready": self._gps_ready,
            "gps_lat": self._gps_lat,
            "gps_lon": self._gps_lon,
            "speed_kmh": self._speed_kmh,
            "adc_raw": self._adc_raw,
            "last_ack": self._last_ack,
            "last_seen_ms": self._last_seen_ms,
        }
