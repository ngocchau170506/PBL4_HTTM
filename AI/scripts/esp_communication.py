"""ESP alert transport with a safe simulation mode for laptop testing."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
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
    ):
        self.mode = mode.upper()
        if self.mode not in {"MOCK", "SERIAL"}:
            raise ValueError("ESP mode must be MOCK or SERIAL")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._last_status = None
        self.last_result: Optional[SendResult] = None

        if self.mode == "SERIAL":
            self._open_serial()

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
        if status == self._last_status:
            return None

        duration_ms = max(0, round(float(duration_seconds) * 1000))
        event_code = EVENT_CODES[status]
        packet_timestamp = timestamp or iso_timestamp()
        packet = f"ALERT,{event_code},{status},{packet_timestamp},{duration_ms}\n"
        self._last_status = status

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
            result = SendResult(packet, sent=True, acknowledged=False)
        except (OSError, ValueError) as exc:
            self._serial = None
            result = SendResult(packet, sent=False, acknowledged=False, error=str(exc))
        self.last_result = result
        return result

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            except OSError:
                pass
            finally:
                self._serial = None
