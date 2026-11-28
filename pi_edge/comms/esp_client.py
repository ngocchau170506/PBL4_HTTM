"""Hardened ESP32 #1 UART Client (LCK-05 v3: Safety Audio MAX98357A + MQ-135 + Software MUTE/VOL + WHOAMI).

Fixes all P0/P1 bugs found in IOTv5 esp_communication.py:
  1. STRICT EVT,ACK vs EVT,NACK parsing (NEVER matches 'ACK' inside 'NACK'!)
  2. Per-command correlated pending waiter (telemetry ACK can NEVER satisfy VOICE:1!)
  3. Clean <5ms alert write path decoupled from slow startup handshake
  4. Canonical HW event names (AUTO_CO2_WARN, AUTO_CO2_CRIT, ESP_BROWNOUT, MUTE_ACTIVE)
     forwarded uniformly regardless of local/server mode (fixes daemon drop bug!)
  5. Safe close() that never throws RuntimeError to abort main.py finally block
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class CommandAckResult:
    ok: bool
    command: str
    ack_line: str
    latency_ms: float
    error: str = ""


def classify_esp_ack_line(line: str) -> Tuple[str, str]:
    """Classify an ESP32 ASCII reply line strictly without substring collisions.

    Returns (kind, key):
      - ('ACK', 'VOICE' | 'STOP' | 'MUTE' | 'ALERT' | 'PING' | 'WHOAMI' | 'FDFRAME' | ...)
      - ('NACK', '<cmd_or_reason>')
      - ('MUTE_PRESSED', 'MUTE')
      - ('HW_EVENT', 'AUTO_CO2_WARN' | 'AUTO_CO2_CRIT' | 'ESP_BROWNOUT' | 'ESP_RESET')
      - ('TELEMETRY', 'FDESP')
      - ('OTHER', '')
    """
    s = (line or "").strip()
    if not s:
        return "OTHER", ""
    if s.startswith("FDESP:"):
        return "TELEMETRY", "FDESP"
    if s == "PONG" or s.startswith("EVT,PONG"):
        return "ACK", "PING"
    if s.startswith("EVT,WHOAMI,") or s.startswith("EVT,ROLE,"):
        return "ACK", "WHOAMI"
    # STRICT NACK CHECK BEFORE ACK!
    if s.startswith("EVT,NACK,"):
        parts = s.split(",")
        key = parts[2].upper() if len(parts) >= 3 else "UNKNOWN"
        return "NACK", key
    if s.startswith("EVT,ACK,"):
        parts = s.split(",")
        key = parts[2].upper() if len(parts) >= 3 else "UNKNOWN"
        if key == "STOP_INTERRUPT":
            key = "STOP"
        elif key == "MUTE_INTERRUPT":
            key = "MUTE"
        return "ACK", key
    if s in ("EVT,MUTE", "EVT,MUTE_PRESSED") or s.startswith("EVT,MUTE_PRESSED,"):
        return "MUTE_PRESSED", "MUTE"
    if "BROWNOUT" in s.upper():
        return "HW_EVENT", "ESP_BROWNOUT"
    if "AUTO_ALERT,CO2_CRIT" in s.upper() or "CO2_CRIT" in s.upper():
        return "HW_EVENT", "AUTO_CO2_CRIT"
    if "AUTO_ALERT,CO2_WARN" in s.upper() or "CO2_WARN" in s.upper():
        return "HW_EVENT", "AUTO_CO2_WARN"
    if s.startswith("EVT,RESET_REASON,") and "POWERON" not in s.upper():
        return "HW_EVENT", "ESP_RESET"
    return "OTHER", ""


def _command_ack_key(cmd: str) -> str:
    c = (cmd or "").strip().upper()
    if c == "PING":
        return "PING"
    if c == "WHOAMI":
        return "WHOAMI"
    if ":" in c:
        return c.split(":", 1)[0]
    return c


def _disable_linux_hupcl(ser: Any) -> None:
    """Clear termios HUPCL on Linux so closing the port avoids triggering CP2102 DTR/RTS reset."""
    try:
        import termios
        fd = ser.fileno()
        attrs = termios.tcgetattr(fd)
        attrs[2] &= ~termios.HUPCL
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass


class EspClient:
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 115200,
        mode: str = "SERIAL",
        require_ack: bool = True,
        on_sensor_telemetry: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_hw_event: Optional[Callable[[str, str], None]] = None,
        on_mute_pressed: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.port = port
        self.baudrate = int(baudrate)
        self.mode = mode.upper()
        self.require_ack = bool(require_ack)
        self.on_sensor_telemetry = on_sensor_telemetry
        self.on_hw_event = on_hw_event
        self.on_mute_pressed = on_mute_pressed

        self._serial: Any = None
        self._serial_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

        # Per-command-key correlated waiters (eliminates shared _ack_event cross-talk!)
        self._waiter_lock = threading.Lock()
        self._waiters: Dict[str, Tuple[threading.Event, List[Tuple[str, str]]]] = {}

        self._state_lock = threading.Lock()
        self.connected = False
        self.reset_count = 0
        self.brownout_seen = False
        self._last_brownout_mono = -999.0
        self.device_role = "UNKNOWN"
        self.latest_health: Dict[str, Any] = {}
        self.latest_sensor: Dict[str, Any] = {
            "co2_ppm": 0.0,
            "raw_adc": 0,
            "gps": {"lat": None, "lon": None, "speed_kmh": 0.0, "satellites": 0, "is_valid": False, "source": "esp32"},
        }
        self._hw_events: deque[str] = deque(maxlen=30)
        self._mute_until_mono = 0.0
        self._mute_newly_triggered = False

    def _open_serial_port(self) -> Any:
        import serial
        # Prevent Linux CP2102/CH340 auto-reset / bootloader strap & concurrent port hijacking (TIOCEXCL)!
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

    def start(self) -> bool:
        if self.mode == "MOCK":
            with self._state_lock:
                self.connected = True
                self.device_role = "AUDIO_MQ135_NODE_MOCK"
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
            self._reader_thread = threading.Thread(target=self._reader_loop, name="esp-reader", daemon=True)
            self._reader_thread.start()
        return opened

    def close(self) -> None:
        """Safe idempotent shutdown (NEVER throws RuntimeError to abort caller finally block)."""
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
        """Process one decoded ASCII line from ESP32 (public for deterministic unit testing)."""
        s = (line or "").strip()
        if not s:
            return
        kind, key = classify_esp_ack_line(s)

        if kind == "TELEMETRY":
            try:
                data = json.loads(s[len("FDESP:"):].strip())
                if isinstance(data, dict):
                    self._update_sensor_from_fdesp(data)
            except Exception:
                pass
            return

        if kind in ("ACK", "NACK"):
            if kind == "ACK" and key == "WHOAMI":
                role_val = s
                if s.startswith("EVT,ROLE,"):
                    parts = s.split(",")
                    if len(parts) >= 3 and parts[2].strip():
                        role_val = parts[2].strip().upper()
                elif "ROLE=" in s:
                    for part in s.split(","):
                        if part.strip().upper().startswith("ROLE="):
                            role_val = part.split("=", 1)[1].strip().upper()
                            break
                with self._state_lock:
                    self.device_role = role_val
            if kind == "ACK" and key == "MUTE":
                # Software MUTE ACK updates local mute timer ONLY (does NOT trigger fake PHYSICAL_MUTE_BUTTON!)
                self._record_mute_state(is_physical=False)
            with self._waiter_lock:
                waiter = self._waiters.get(key)
                if waiter is not None:
                    ev, box = waiter
                    box.append((kind, s))
                    ev.set()
            return

        if kind == "MUTE_PRESSED":
            self._record_mute_state(is_physical=True)
            if self.on_mute_pressed:
                try:
                    self.on_mute_pressed(self.get_sensor_snapshot())
                except Exception:
                    pass
            return

        if kind == "HW_EVENT":
            now_m = time.monotonic()
            emit_cb = True
            with self._state_lock:
                if key == "ESP_BROWNOUT":
                    # Deduplicate RESET_REASON,BROWNOUT + WARN,BROWNOUT_* burst within 5s
                    if (now_m - self._last_brownout_mono) < 5.0:
                        emit_cb = False
                    else:
                        self._last_brownout_mono = now_m
                        self._hw_events.append(key)
                        self.brownout_seen = True
                        self.reset_count += 1
                else:
                    self._hw_events.append(key)
                    if key == "ESP_RESET":
                        self.reset_count += 1
            if emit_cb and self.on_hw_event:
                try:
                    self.on_hw_event(key, s)
                except Exception:
                    pass

    def _record_mute_state(self, is_physical: bool = False) -> None:
        with self._state_lock:
            self._mute_until_mono = time.monotonic() + 30.0
            if is_physical:
                self._mute_newly_triggered = True
                self._hw_events.append("MUTE_ACTIVE")

    def _update_sensor_from_fdesp(self, data: Dict[str, Any]) -> None:
        co2 = data.get("co2_ppm")
        if co2 is None:
            co2 = data.get("ppm", 0.0)
        adc = data.get("raw_adc", data.get("adc", 0))
        gps_valid = bool(data.get("gps_fix") or data.get("is_valid"))
        lat = data.get("lat") if gps_valid else None
        lon = data.get("lon") if gps_valid else None
        spd = float(data.get("speed_kmh") or 0.0)
        sats = int(data.get("sats") or data.get("satellites") or 0)
        alt = data.get("alt_m", data.get("altitude_m"))
        snap = {
            "co2_ppm": float(co2 or 0.0),
            "raw_adc": int(adc or 0),
            "gps": {
                "lat": float(lat) if lat is not None else None,
                "lon": float(lon) if lon is not None else None,
                "speed_kmh": spd,
                "altitude_m": float(alt) if alt is not None else None,
                "satellites": sats,
                "is_valid": gps_valid,
                "source": "esp32_gps",
            },
        }
        with self._state_lock:
            self.latest_sensor = snap
            if data.get("role"):
                self.device_role = str(data["role"]).upper()
            self.latest_health = {
                "warmup": bool(data.get("warmup", False)),
                "muted": bool(data.get("muted", False)),
                "vol": data.get("vol"),
                "alarm_mode": int(data.get("alarm_mode") or 0),
                "free_heap": data.get("free_heap"),
                "co2_level": data.get("co2_level"),
            }
        if self.on_sensor_telemetry:
            try:
                self.on_sensor_telemetry(snap)
            except Exception:
                pass

    def _reader_loop(self) -> None:
        backoff_s = 1.0
        while not self._stop.is_set():
            with self._serial_lock:
                ser = self._serial
            if ser is None:
                if self.mode != "SERIAL":
                    break
                # Auto-reconnect with bounded backoff after USB unplug/glitch (fixes BUG-03!)
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

    def send_command(self, cmd: str, wait_ack: Optional[bool] = None, timeout_s: float = 0.8) -> CommandAckResult:
        t0 = time.perf_counter()
        c = (cmd or "").strip()
        if self.mode == "MOCK":
            return CommandAckResult(True, c, f"EVT,ACK,{c},MOCK", round((time.perf_counter() - t0) * 1000.0, 2))

        need_ack = self.require_ack if wait_ack is None else bool(wait_ack)
        ack_key = _command_ack_key(c)
        ev = threading.Event()
        box: List[Tuple[str, str]] = []

        if need_ack:
            with self._waiter_lock:
                self._waiters[ack_key] = (ev, box)

        try:
            with self._write_lock:
                with self._serial_lock:
                    ser = self._serial
                if ser is None:
                    return CommandAckResult(False, c, "", 0.0, error="serial_not_connected")
                try:
                    ser.write((c + "\n").encode("utf-8"))
                    # Avoid blocking tcdrain() on <5ms fire-and-forget safety audio alerts (BUG-04)!
                    if need_ack:
                        ser.flush()
                except Exception as exc:
                    with self._serial_lock:
                        self._drop_serial_unlocked()
                    return CommandAckResult(
                        False, c, "", round((time.perf_counter() - t0) * 1000.0, 2), error=f"serial_io_error:{exc}"
                    )
            if not need_ack:
                return CommandAckResult(True, c, "SENT_NO_ACK_WAIT", round((time.perf_counter() - t0) * 1000.0, 2))

            got = ev.wait(timeout=max(0.05, float(timeout_s)))
            lat_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if not got or not box:
                return CommandAckResult(False, c, "", lat_ms, error="ACK_TIMEOUT")
            kind, reply = box[-1]
            if kind == "NACK":
                return CommandAckResult(False, c, reply, lat_ms, error="ESP32_NACK")
            return CommandAckResult(True, c, reply, lat_ms)
        finally:
            if need_ack:
                with self._waiter_lock:
                    cur = self._waiters.get(ack_key)
                    if cur is not None and cur[0] is ev:
                        self._waiters.pop(ack_key, None)

    def get_sensor_snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            return json.loads(json.dumps(self.latest_sensor))

    def pop_hw_events(self) -> List[str]:
        with self._state_lock:
            evts = list(self._hw_events)
            self._hw_events.clear()
            return evts

    def get_esp_status_block(self) -> Dict[str, Any]:
        evts = self.pop_hw_events()
        with self._state_lock:
            block = {
                "connected": self.connected,
                "reset_count": self.reset_count,
                "brownout": self.brownout_seen or ("ESP_BROWNOUT" in evts),
                "hw_events": evts,
                "role": self.device_role,
            }
            if self.latest_health:
                block.update(self.latest_health)
            return block

    def pop_mute_state_block(self) -> Dict[str, Any]:
        now_m = time.monotonic()
        with self._state_lock:
            active = now_m < self._mute_until_mono
            newly = self._mute_newly_triggered
            self._mute_newly_triggered = False
            return {"active": active, "newly_triggered": newly, "mute_duration_s": 30}
