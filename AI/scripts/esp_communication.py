"""ESP alert transport with dedicated reader thread, voice_clips.h support, and simulation mode."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
import sys
import threading
import time
from typing import Optional

from AI.scripts.time_utils import iso_timestamp

VALID_STATUSES = {"DROWSY", "YAWNING", "DISTRACTED", "NORMAL"}
EVENT_CODES = {"NORMAL": "00", "DROWSY": "01", "YAWNING": "02", "DISTRACTED": "03"}


@dataclass
class SendResult:
    packet: str
    sent: bool
    acknowledged: bool
    error: Optional[str] = None


def auto_detect_serial_port(preferred: Optional[str] = None) -> Optional[str]:
    """Resolve a valid serial port across Windows (COMx) and Linux (/dev/ttyUSBx)."""
    if preferred:
        preferred = preferred.strip()
        if sys.platform.startswith("win"):
            if preferred.upper().startswith("COM"):
                return preferred
        else:
            if os.path.exists(preferred):
                return preferred
    try:
        list_ports = importlib.import_module("serial.tools.list_ports")
        ports = list(list_ports.comports())
        for p in ports:
            desc = f"{p.device} {p.description} {getattr(p, 'manufacturer', '')}".lower()
            if any(k in desc for k in ("cp210", "ch340", "usb-serial", "uart", "ttyusb", "ttyacm", "esp", "silicon labs")):
                return p.device
        if ports:
            return ports[0].device
    except Exception:
        pass
    if not sys.platform.startswith("win") and os.path.exists("/dev/ttyUSB0"):
        return "/dev/ttyUSB0"
    return preferred


class EspCommunicator:
    """Send newline-delimited alert states without coupling detection to serial I/O."""

    def __init__(
        self,
        mode: str = "MOCK",
        port: Optional[str] = None,
        baudrate: int = 115200,
        timeout: float = 0.2,
        require_ack: bool = False,
        heartbeat_seconds: float = 6.0,
    ):
        self.mode = mode.upper()
        if self.mode not in {"MOCK", "SERIAL"}:
            raise ValueError("ESP mode must be MOCK or SERIAL")
        self.port = auto_detect_serial_port(port)
        self.baudrate = baudrate
        self.timeout = timeout
        self.require_ack = require_ack
        self.heartbeat_seconds = heartbeat_seconds
        self._desired = None
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._ack_event = threading.Event()
        self._last_ack_line = ""
        self.latest_telemetry = ""
        self._stop = False
        self._sequence = 0
        self._serial = None
        self._needs_handshake = False
        self._last_status = None
        self._last_voice_sent_at = 0.0
        self._last_voice_status = None
        self.voice_cooldown_s = float(os.getenv("ESP_VOICE_COOLDOWN_S", "5.0"))
        self.reset_count = 0
        self.last_result: Optional[SendResult] = None

        self._reader_thread = None
        if self.mode == "SERIAL":
            self._open_serial()
            self._reader_thread = threading.Thread(target=self._reader_loop, name="esp-reader", daemon=True)
            self._reader_thread.start()

        self._thread = threading.Thread(target=self._run, name="esp-transport", daemon=True)
        self._thread.start()

    def _open_serial(self):
        resolved_port = auto_detect_serial_port(self.port)
        if resolved_port:
            self.port = resolved_port
        if not self.port:
            self.last_result = SendResult(
                packet="",
                sent=False,
                acknowledged=False,
                error="A serial port is required in SERIAL mode",
            )
            return
        try:
            serial = importlib.import_module("serial")
            ser = serial.Serial()
            ser.port = self.port
            ser.baudrate = self.baudrate
            ser.timeout = max(0.1, min(self.timeout, 0.3))
            ser.write_timeout = max(self.timeout, 1.0)
            ser.dtr = False
            ser.rts = False
            ser.open()
            self._serial = ser
            self._needs_handshake = True
            print(f"[ESP SERIAL] Da ket noi cong {self.port} ({self.baudrate} baud)")
        except Exception as exc:
            self._serial = None
            self.last_result = SendResult(
                packet="",
                sent=False,
                acknowledged=False,
                error=f"Could not open ESP serial port {self.port}: {exc}",
            )

    def _reader_loop(self):
        """Continuously drain telemetry (FDESP:/CO2:) and capture EVT,ACK/PONG lines."""
        while not self._stop:
            ser = self._serial
            if ser is None or not ser.is_open:
                time.sleep(0.1)
                continue
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if "ACK" in line or line == "PONG":
                    # Khớp linh hoạt: chịu được rớt vài byte UART (vd "EVT,ACKOICE,4")
                    self._last_ack_line = line
                    self._ack_event.set()
                elif line.startswith("FDESP:") or line.startswith("CO2:"):
                    self.latest_telemetry = line
                elif line.startswith("EVT,"):
                    if "BOOT" in line or "RESET_REASON" in line or "BROWNOUT" in line:
                        self.reset_count += 1
                        self._last_voice_sent_at = 0.0
                        print(f"[ESP WARN] ESP32 vua khoi dong lai ({line}) -> kiem tra nguon cap cho loa!")
                    else:
                        print(f"[ESP EVENT] {line}")
            except Exception:
                self._disconnect()
                time.sleep(0.2)

    @property
    def connected(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    @property
    def mode_label(self) -> str:
        return self.mode

    def send_raw_command(self, cmd: str, wait_ack: bool = True, timeout: float = 1.2) -> tuple[bool, str]:
        """Send a direct firmware command (e.g. VOICE:1, STOP, PING, MUTE) and wait for ACK."""
        if self.mode != "SERIAL":
            return False, "NOT_SERIAL_MODE"
        if not self.connected:
            self._open_serial()
        if not self.connected:
            return False, "DISCONNECTED"
        wire = cmd.strip() + "\n"
        self._ack_event.clear()
        ser = self._serial
        if ser is None:
            return False, "DISCONNECTED"
        try:
            with self._write_lock:
                ser.write(wire.encode("ascii"))
                ser.flush()
        except Exception as exc:
            self._disconnect()
            return False, f"WRITE_ERROR: {exc}"
        if not wait_ack:
            return True, ""
        ok = self._ack_event.wait(timeout=timeout)
        return ok, (self._last_ack_line if ok else "")

    def send_status(
        self,
        status: str,
        duration_seconds: float = 0.0,
        timestamp: Optional[str] = None,
    ) -> Optional[SendResult]:
        status = status.upper()
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported ESP status: {status}")
        with self._condition:
            if self._desired is None or self._desired[0] != status:
                self._sequence += 1
                self._desired = (status, duration_seconds, timestamp, self._sequence)
                self._condition.notify_all()
        return self.last_result

    def _transmit(self, status, duration_seconds, timestamp, sequence):
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported ESP status: {status}")

        duration_ms = max(0, round(float(duration_seconds) * 1000))
        event_code = EVENT_CODES[status]
        packet_timestamp = timestamp or iso_timestamp()
        packet = f"ALERT,{event_code},{status},{packet_timestamp},{duration_ms}\n"
        if self.require_ack:
            packet = packet.rstrip() + f",{sequence}\n"

        if self.mode == "MOCK":
            print(f"[ESP MOCK] {packet.rstrip()}")
            result = SendResult(packet, sent=False, acknowledged=False)
            self.last_result = result
            return result

        if not self.connected:
            result = SendResult(
                packet,
                sent=False,
                acknowledged=False,
                error=self.last_result.error if self.last_result else "Serial port is closed",
            )
            self.last_result = result
            return result

        try:
            use_voice = os.getenv("ESP_USE_VOICE", "1") != "0"
            interrupt_voice = os.getenv("ESP_INTERRUPT_VOICE", "0") == "1"
            if status == "NORMAL" and use_voice and self._last_status in {"DROWSY", "YAWNING", "DISTRACTED"} and not interrupt_voice:
                self._last_status = status
                print("[ESP SERIAL] State -> NORMAL (giu nguyen de ESP32 doc het cau thoai trong voice_clips.h)")
                result = SendResult(packet, sent=True, acknowledged=True)
                self.last_result = result
                return result

            # Chống phát chồng/ngắt quãng: trong thời gian cooldown (>= độ dài câu thoại ~4.3s)
            # không gửi lại VOICE, trừ khi nâng mức nguy hiểm (YAWNING/DISTRACTED -> DROWSY).
            if use_voice and status in {"DROWSY", "YAWNING", "DISTRACTED"}:
                since = time.monotonic() - self._last_voice_sent_at
                escalate = status == "DROWSY" and self._last_voice_status in {"YAWNING", "DISTRACTED"}
                if since < self.voice_cooldown_s and not escalate:
                    self._last_status = status
                    result = SendResult(packet, sent=True, acknowledged=True,
                                        error=f"cooldown {self.voice_cooldown_s - since:.1f}s")
                    self.last_result = result
                    return result

            if status == "DROWSY":
                wire_cmd = "VOICE:1\n" if use_voice else f"ALERT:2:{min(1500, max(500, duration_ms))}\n"
            elif status == "YAWNING":
                wire_cmd = "VOICE:2\n" if use_voice else f"ALERT:1:{min(1500, max(300, duration_ms))}\n"
            elif status == "DISTRACTED":
                wire_cmd = "VOICE:3\n" if use_voice else "ALERT:2:500\n"
            else:
                wire_cmd = "STOP\n"

            self._ack_event.clear()
            ser = self._serial
            if ser is None:
                raise OSError("Serial port closed during transmit")
            with self._write_lock:
                ser.write(wire_cmd.encode("ascii"))
                ser.flush()
            if status in {"DROWSY", "YAWNING", "DISTRACTED"}:
                self._last_voice_sent_at = time.monotonic()
                self._last_voice_status = status

            acknowledged = False
            ack_line = ""
            if self.require_ack:
                if self._ack_event.wait(timeout=max(self.timeout, 1.0)):
                    acknowledged = True
                    ack_line = self._last_ack_line
            self._last_status = status
            print(f"[ESP SERIAL] Sent {wire_cmd.strip()} ({status}) | ACK={acknowledged} {ack_line}".rstrip())
            result = SendResult(
                wire_cmd if self.mode == "SERIAL" else packet,
                sent=True,
                acknowledged=acknowledged,
                error="ACK timeout" if self.require_ack and not acknowledged else None,
            )
        except Exception as exc:  # gồm SerialException, AttributeError khi cổng bị đóng giữa chừng
            self._disconnect()
            result = SendResult(packet, sent=False, acknowledged=False, error=str(exc))
        self.last_result = result
        return result

    def _disconnect(self):
        ser = self._serial
        self._serial = None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def _handshake(self) -> bool:
        """Sau khi (mở lại) cổng: chờ ESP32 sẵn sàng (có thể vừa reset do DTR/brownout)."""
        for _ in range(4):
            try:
                ok, ack = self.send_raw_command("PING", wait_ack=True, timeout=0.8)
            except Exception:
                ok, ack = False, ""
            if ok and ack == "PONG":
                return True
        return False

    def _run(self):
        delivered = None
        retry = 1.0
        next_attempt = 0.0
        last_send = 0.0
        while True:
            with self._condition:
                if self._stop:
                    break
                desired = self._desired
                now = time.monotonic()
                is_active_alert = desired is not None and desired[0] in {"DROWSY", "YAWNING", "DISTRACTED"}
                due = desired is not None and (
                    desired != delivered or (is_active_alert and now - last_send >= self.heartbeat_seconds)
                )
                if not due or now < next_attempt:
                    self._condition.wait(0.1)
                    continue
            try:
                if self.mode == "SERIAL" and not self.connected:
                    self._open_serial()
                if self.mode == "SERIAL" and self.connected and self._needs_handshake:
                    self._needs_handshake = False
                    if not self._handshake():
                        print("[ESP WARN] ESP32 chua tra PONG sau khi mo cong (dang khoi dong?)")
                result = self._transmit(*desired)
            except Exception as exc:  # Không bao giờ để luồng gửi ESP chết
                result = SendResult("", sent=False, acknowledged=False, error=str(exc))
            # Lệnh VOICE đã ghi xuống cổng thì KHÔNG gửi lại khi thiếu ACK (tránh bão phát lại
            # làm loa lặp/giật). Chỉ STOP mới cần ACK để chắc chắn đã tắt.
            is_voice = desired[0] in {"DROWSY", "YAWNING", "DISTRACTED"}
            success = self.mode == "MOCK" or (
                result.sent and (not self.require_ack or result.acknowledged or is_voice)
            )
            if success:
                delivered = desired
                last_send = time.monotonic()
                retry = 1.0
                next_attempt = 0.0
            else:
                next_attempt = time.monotonic() + retry
                retry = min(retry * 2, 3.0)
        self._disconnect()

    def close(self):
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        self._thread.join(timeout=max(2.0, self.timeout * 3))
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        if self._thread.is_alive():
            raise RuntimeError("ESP worker did not stop")
