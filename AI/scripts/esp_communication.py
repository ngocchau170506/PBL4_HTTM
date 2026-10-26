"""ESP alert transport with a safe simulation mode for laptop testing."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import threading
import time
from typing import Optional

from AI.scripts.time_utils import iso_timestamp

VALID_STATUSES = {"DROWSY", "YAWNING", "NORMAL"}
EVENT_CODES = {"NORMAL": "00", "DROWSY": "01", "YAWNING": "02"}


@dataclass
class SendResult:
    packet: str
    sent: bool
    acknowledged: bool
    error: Optional[str] = None


class EspCommunicator:
    """Send newline-delimited alert states without coupling detection to serial I/O."""

    def __init__(
        self,
        mode: str = "MOCK",
        port: Optional[str] = None,
        baudrate: int = 115200,
        timeout: float = 0.2,
        require_ack: bool = False,
        heartbeat_seconds: float = 5.0,
    ):
        self.mode = mode.upper()
        if self.mode not in {"MOCK", "SERIAL"}:
            raise ValueError("ESP mode must be MOCK or SERIAL")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.require_ack = require_ack
        self.heartbeat_seconds = heartbeat_seconds
        self._desired = None
        self._condition = threading.Condition()
        self._stop = False
        self._sequence = 0
        self._serial = None
        self._last_status = None
        self.last_result: Optional[SendResult] = None

        self._thread = threading.Thread(target=self._run, name="esp-transport", daemon=True)
        self._thread.start()

    def _open_serial(self):
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
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout,
            )
        except (ImportError, OSError, ValueError) as exc:
            self._serial = None
            self.last_result = SendResult(
                packet="",
                sent=False,
                acknowledged=False,
                error=f"Could not open ESP serial port {self.port}: {exc}",
            )

    @property
    def connected(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    @property
    def mode_label(self) -> str:
        return self.mode

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
            self._serial.write(packet.encode("ascii"))
            self._serial.flush()
            acknowledged = False
            if self.require_ack:
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    reply = self._serial.readline().decode("ascii", errors="replace").strip()
                    if reply == f"ACK,{sequence}":
                        acknowledged = True
                        break
            result = SendResult(packet, sent=True, acknowledged=acknowledged,
                                error="ACK timeout" if self.require_ack and not acknowledged else None)
        except (OSError, ValueError) as exc:
            self._disconnect()
            result = SendResult(packet, sent=False, acknowledged=False, error=str(exc))
        self.last_result = result
        return result

    def _disconnect(self):
        if self._serial is not None:
            try:
                self._serial.close()
            except OSError:
                pass
            finally:
                self._serial = None

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
                due = desired is not None and (desired != delivered or now - last_send >= self.heartbeat_seconds)
                if not due or now < next_attempt:
                    self._condition.wait(0.1)
                    continue
            if self.mode == "SERIAL" and not self.connected:
                self._open_serial()
            result = self._transmit(*desired)
            success = self.mode == "MOCK" or (result.sent and (not self.require_ack or result.acknowledged))
            if success:
                delivered = desired
                last_send = time.monotonic()
                retry = 1.0
                next_attempt = 0.0
            else:
                next_attempt = time.monotonic() + retry
                retry = min(retry * 2, 10.0)
        self._disconnect()

    def close(self):
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        self._thread.join(timeout=max(2.0, self.timeout * 3))
        if self._thread.is_alive():
            raise RuntimeError("ESP worker did not stop")
