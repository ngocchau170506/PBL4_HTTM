"""Bộ phát lệnh cảnh báo hai chiều từ máy tính xuống ESP32 (FEAT-041 / G4).

- Quản lý kênh truyền lệnh xuống ESP32 kích hoạt còi/loa khi phát hiện tài xế vi phạm.
- Chuẩn hóa giao thức bản tin lệnh theo chuẩn: ALERT,<event_code>,<status>,<iso_timestamp>,<duration_ms>\\n
  và hỗ trợ cú pháp lệnh firmware: ALERT:<level>[:<duration_ms>]\\n, STOP, CONNECT, PING.
- Cơ chế bắt tay xác nhận ACK (EVT,ACK,...) xử lý triệt để BUG-001.
- Bảo vệ tương tranh đa luồng bằng Mutex (threading.Lock) khi dùng chung cổng UART với luồng đọc Telemetry.
- Chỉ dùng thư viện chuẩn của Python, không import chéo từ fd_* khác (Luật R2).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Callable, Deque, List, Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, ViolationEvent

EVENT_CODES = {
    "NORMAL": "00",
    "DROWSY": "01",
    "YAWNING": "02",
}

ALLOWED_CONTROL_COMMANDS = (
    "CONNECT", "STOP", "PING", "SLEEP", "MUTE",
    "VOICE:1", "VOICE:2", "VOICE:3", "VOICE:4", "VOICE:5",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_now() -> str:
    # Định dạng ISO không dấu cách theo chuẩn time_utils
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


@dataclass(frozen=True)
class AlertCommand:
    """Đặc tả lệnh cảnh báo gửi xuống vi điều khiển ESP32."""

    status: str  # NORMAL, DROWSY, YAWNING
    duration_ms: int = 1500
    level: int = 1  # 1: nhẹ/nhắc nhở, 2: còi khẩn cấp
    trace_id: str = "cmd-trace"


@dataclass(frozen=True)
class AckReceipt:
    """Phiếu biên nhận kết quả bắt tay từ ESP32."""

    success: bool
    raw_packet: str
    ack_payload: Optional[str] = None
    i2s_ready: bool = False
    duration_ms: float = 0.0
    error: Optional[str] = None


class Esp32Commander:
    """Thực thi kênh điều khiển còi/loa và gửi lệnh xuống ESP32."""

    node_id: NodeId = "connect.esp32"

    def __init__(
        self,
        enabled: bool = True,
        ack_timeout_ms: int = 300,
        max_queue: int = 32,
        require_i2s: bool = False,
        serial_mutex: Optional[threading.Lock] = None,
        transport_sender: Optional[Callable[[str], Optional[str]]] = None,
        reconnect_interval_sec: float = 3.0,
    ) -> None:
        self._enabled = bool(enabled)
        self._ack_timeout_ms = max(50, int(ack_timeout_ms))
        self._max_queue = max(1, int(max_queue))
        self._require_i2s = bool(require_i2s)
        self._mutex = serial_mutex or threading.Lock()
        self._transport_sender = transport_sender
        self._reconnect_interval_sec = max(0.1, float(reconnect_interval_sec))
        self._last_reconnect_attempt_s: float = 0.0
        self._queue: Deque[AlertCommand] = deque()

        self._health: Health = "OK" if self._enabled else "DOWN"
        self._total_sent: int = 0
        self._total_acked: int = 0
        self._total_timeout: int = 0
        self._last_command: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def serial_mutex(self) -> threading.Lock:
        return self._mutex

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def total_sent(self) -> int:
        return self._total_sent

    @property
    def total_acked(self) -> int:
        return self._total_acked

    @property
    def total_timeout(self) -> int:
        return self._total_timeout

    @property
    def reconnect_interval_sec(self) -> float:
        return self._reconnect_interval_sec

    def try_reconnect(self, trace_id: str = "esp-reconnect") -> bool:
        """Thử gửi lệnh PING để kiểm tra và phục hồi trạng thái từ DEGRADED về OK."""
        if not self._enabled:
            return False
        now_s = time.monotonic()
        if now_s - self._last_reconnect_attempt_s < self._reconnect_interval_sec:
            return False
        self._last_reconnect_attempt_s = now_s

        res = self.send_control("PING", trace_id=trace_id)
        if isinstance(res, AckReceipt) and res.success:
            self._health = "OK"
            return True
        return False

    def health(self) -> Health:
        if self._health == "DEGRADED" and self._enabled:
            self.try_reconnect(trace_id="esp-auto-health-probe")
        return self._health

    def _make_error(
        self,
        trace_id: str,
        code: ErrorCode,
        message: str,
        recoverable: bool = True,
    ) -> ErrorEvent:
        safe_trace = trace_id if trace_id else "esp-cmd-no-trace"
        return ErrorEvent(
            trace_id=safe_trace,
            ts_ms=_now_ms(),
            source="connect.esp32",
            code=code,
            node_path="connect.esp32",
            err_class="source",
            message=message,
            recoverable=recoverable,
        )

    def format_alert_packet(self, cmd: AlertCommand) -> str:
        """Định dạng bản tin lệnh chuẩn theo định dạng esp_communication.py."""
        status_clean = cmd.status.strip().upper()
        event_code = EVENT_CODES.get(status_clean, "01")
        return f"ALERT,{event_code},{status_clean},{_iso_now()},{cmd.duration_ms}\n"

    def format_firmware_legacy_packet(self, cmd: AlertCommand) -> str:
        """Định dạng bản tin lệnh tương thích trực tiếp firmware esp32_firmware.ino."""
        return f"ALERT:{cmd.level}:{cmd.duration_ms}\n"

    def format_voice_packet(self, cmd: AlertCommand) -> str:
        """Định dạng bản tin lệnh phát giọng nói tiếng Việt trong voice_clips.h trên ESP32."""
        status_clean = cmd.status.strip().upper()
        if status_clean == "DROWSY":
            return "VOICE:1\n"
        if status_clean == "YAWNING":
            return "VOICE:2\n"
        if status_clean == "NORMAL":
            return "STOP\n"
        return f"VOICE:{cmd.level}\n"

    def send_alert(
        self, cmd: AlertCommand, use_standard_format: bool = True
    ) -> Union[AckReceipt, ErrorEvent]:
        """Gửi lệnh cảnh báo xuống ESP32, bảo vệ bằng Mutex và đợi bắt tay ACK."""
        if not self._enabled:
            return AckReceipt(
                success=False,
                raw_packet="",
                error="ESP32 Commander đang ở trạng thái tắt (enabled=False)",
            )

        packet_str = self.format_alert_packet(cmd) if use_standard_format else self.format_firmware_legacy_packet(cmd)

        t_start = time.monotonic()
        with self._mutex:
            self._total_sent += 1
            self._last_command = packet_str

            if self._transport_sender is not None:
                # Gửi qua transport và nhận phản hồi ACK
                ack_response = self._transport_sender(packet_str)
            else:
                # Giả lập phản hồi mặc định thành công có cờ i2s=1 nếu không gắn transport
                ack_response = f"EVT,ACK,ALERT,{cmd.level},i2s=1"

            elapsed_ms = (time.monotonic() - t_start) * 1000.0

            # Kiểm tra timeout
            if ack_response is None or elapsed_ms > self._ack_timeout_ms:
                self._total_timeout += 1
                self._health = "DEGRADED"
                return self._make_error(
                    cmd.trace_id,
                    "E_RESP_NO_ACK",
                    f"Không nhận được ACK từ ESP32 sau {elapsed_ms:.1f}ms (ngưỡng {self._ack_timeout_ms}ms)",
                    recoverable=True,
                )

            # Phân tích phản hồi ACK (EVT,ACK,...)
            is_ack = "ACK" in ack_response or "PONG" in ack_response
            has_i2s = "i2s=1" in ack_response
            i2s_Explicitly_failed = "i2s=0" in ack_response

            if not is_ack:
                self._health = "DEGRADED"
                return self._make_error(
                    cmd.trace_id,
                    "E_RESP_NO_ACK",
                    f"Phản hồi từ ESP32 không chứa mã ACK hợp lệ: {ack_response.strip()}",
                    recoverable=True,
                )

            if i2s_Explicitly_failed or (self._require_i2s and not has_i2s):
                self._health = "DEGRADED"
                if self._require_i2s or i2s_Explicitly_failed:
                    return self._make_error(
                        cmd.trace_id,
                        "E_RESP_NO_ACK",
                        f"ESP32 phản hồi ACK nhưng mạch loa I2S không sẵn sàng (i2s=0): {ack_response.strip()}",
                        recoverable=True,
                    )

            self._total_acked += 1
            self._health = "OK"
            return AckReceipt(
                success=True,
                raw_packet=packet_str,
                ack_payload=ack_response.strip(),
                i2s_ready=has_i2s,
                duration_ms=round(elapsed_ms, 2),
            )

    def send_control(
        self, command: str, trace_id: str = "cmd-ctrl"
    ) -> Union[AckReceipt, ErrorEvent]:
        """Gửi các lệnh điều khiển phụ trợ (CONNECT, STOP, PING, SLEEP, MUTE) xuống ESP32."""
        cmd_clean = command.strip().upper()
        if cmd_clean not in ALLOWED_CONTROL_COMMANDS:
            self._health = "DEGRADED"
            return self._make_error(
                trace_id,
                "E_SIG_BAD_FORMAT",
                f"Lệnh điều khiển ESP32 không hợp lệ: {command}",
                recoverable=True,
            )

        if not self._enabled:
            return AckReceipt(
                success=False,
                raw_packet="",
                error="ESP32 Commander đang ở trạng thái tắt (enabled=False)",
            )

        packet_str = f"{cmd_clean}\n"
        t_start = time.monotonic()
        with self._mutex:
            self._total_sent += 1
            self._last_command = packet_str
            if self._transport_sender is not None:
                ack_response = self._transport_sender(packet_str)
            else:
                ack_response = "PONG" if cmd_clean == "PING" else f"EVT,ACK,{cmd_clean},i2s=1"

            elapsed_ms = (time.monotonic() - t_start) * 1000.0
            if ack_response is None or elapsed_ms > self._ack_timeout_ms:
                self._total_timeout += 1
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_RESP_NO_ACK",
                    f"Quá hạn chờ ACK cho lệnh {cmd_clean} sau {elapsed_ms:.1f}ms",
                    recoverable=True,
                )

            if "ACK" not in ack_response and "PONG" not in ack_response:
                self._health = "DEGRADED"
                return self._make_error(
                    trace_id,
                    "E_RESP_NO_ACK",
                    f"Phản hồi lệnh {cmd_clean} không hợp lệ: {ack_response.strip()}",
                    recoverable=True,
                )

            self._total_acked += 1
            self._health = "OK"
            return AckReceipt(
                success=True,
                raw_packet=packet_str,
                ack_payload=ack_response.strip(),
                i2s_ready="i2s=1" in ack_response,
                duration_ms=round(elapsed_ms, 2),
            )

    def enqueue_alert(self, cmd: AlertCommand) -> Optional[ErrorEvent]:
        """Đẩy lệnh cảnh báo vào hàng đợi bất đồng bộ (non-blocking CommandQueue)."""
        if len(self._queue) >= self._max_queue:
            self._queue.popleft()
            self._queue.append(cmd)
            self._health = "DEGRADED"
            return self._make_error(
                cmd.trace_id,
                "E_RESP_BUFFER_OVERRUN",
                f"Hàng đợi lệnh ESP32 đầy ({self._max_queue}), đã ghi đè lệnh cũ nhất",
                recoverable=True,
            )
        self._queue.append(cmd)
        return None

    def flush_one(self, use_standard_format: bool = True) -> Optional[Union[AckReceipt, ErrorEvent]]:
        """Lấy 1 lệnh từ hàng đợi và gửi xuống ESP32."""
        if not self._queue:
            return None
        cmd = self._queue.popleft()
        return self.send_alert(cmd, use_standard_format=use_standard_format)

    def flush_all(self, use_standard_format: bool = True) -> Tuple[Union[AckReceipt, ErrorEvent], ...]:
        """Gửi toàn bộ các lệnh đang chờ trong hàng đợi xuống ESP32."""
        results: List[Union[AckReceipt, ErrorEvent]] = []
        while self._queue:
            res = self.flush_one(use_standard_format=use_standard_format)
            if res is not None:
                results.append(res)
        return tuple(results)

    def handle_violation(
        self,
        event: ViolationEvent,
        min_interval_ms: int = 1000,
    ) -> Optional[Union[AckReceipt, ErrorEvent]]:
        """Tiếp nhận ViolationEvent từ RulesEngine và kích hoạt còi/loa ESP32 tương ứng."""
        if not self._enabled:
            return None

        now_ms = _now_ms()
        if (
            hasattr(self, "_last_alert_ts")
            and hasattr(self, "_last_alert_rule")
            and getattr(self, "_last_alert_rule") == event.rule
            and now_ms - getattr(self, "_last_alert_ts", 0) < min_interval_ms
        ):
            return None

        self._last_alert_ts = now_ms
        self._last_alert_rule = event.rule

        rule_name = event.rule.casefold()
        if "drowsy" in rule_name or "eye" in rule_name:
            status = "DROWSY"
            level = 2
            duration_ms = 2000
        elif "yawn" in rule_name or "mouth" in rule_name:
            status = "YAWNING"
            level = 1
            duration_ms = 1500
        else:
            status = "DROWSY"
            level = 1
            duration_ms = 1000

        cmd = AlertCommand(
            status=status,
            duration_ms=duration_ms,
            level=level,
            trace_id=event.trace_id,
        )
        return self.send_alert(cmd)

    def handle_recovery(
        self,
        trace_id: str = "esp-recovery",
    ) -> Optional[Union[AckReceipt, ErrorEvent]]:
        """Gửi lệnh NORMAL xuống ESP32 để tắt còi/đèn khi tài xế đã tỉnh lại (kết thúc vi phạm)."""
        if not self._enabled:
            return None
        if not getattr(self, "_last_alert_rule", None):
            return None
        self._last_alert_rule = None
        self._last_alert_ts = 0
        cmd = AlertCommand(
            status="NORMAL",
            duration_ms=0,
            level=0,
            trace_id=trace_id,
        )
        return self.send_alert(cmd)

    def self_test(self) -> Tuple[bool, str]:
        cmd = AlertCommand(status="DROWSY", duration_ms=1000, level=1, trace_id="cmd-self-test")
        receipt = self.send_alert(cmd)
        if isinstance(receipt, ErrorEvent):
            return False, f"{receipt.code}: {receipt.message}"
        if not receipt.success:
            return False, f"Thất bại khi gửi lệnh test: {receipt.error}"
        return True, f"Esp32Commander OK (acked={self._total_acked}, health={self.health()})"
