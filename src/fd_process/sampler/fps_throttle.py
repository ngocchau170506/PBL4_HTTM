"""Bộ điều tiết nhịp khung hình theo timestamp thực (FEAT-020).

- Tuân thủ Luật R1: Mỗi file 1 vai trò (chỉ lọc lấy mẫu frame theo target_fps, không xử lý AI hay lưu ảnh).
- Tuân thủ Luật R2: Không import chéo từ các fd_* khác, chỉ dùng kiểu từ contracts.logic.
- Tuân thủ Luật R4: Nhận target_fps từ cấu hình, không hard-code tham số nghiệp vụ.
- Tuân thủ Luật R5: Mọi gói tin được kiểm tra trace_id; mọi lỗi phát ErrorEvent kèm node_path='process.sampler'.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple, Union

from contracts.logic import ErrorCode, ErrorEvent, Health, NodeId, SignalPacket


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class SamplerStats:
    """Số liệu thống kê hoạt động của bộ lấy mẫu nhịp khung hình."""

    target_fps: int
    total_in: int
    total_passed: int
    total_dropped: int
    pass_ratio: float
    measured_output_fps: float


class FpsThrottle:
    """Thực thi NodeContract cho node process.sampler.

    Sử dụng thuật toán khóa pha theo mốc thời gian thực (ts_ms) kèm bù sai số làm tròn 0.5ms
    để chọn đúng số lượng khung hình mục tiêu (target_fps) mà không dùng time.sleep().
    """

    node_id: NodeId = "process.sampler"

    def __init__(
        self,
        target_fps: int = 5,
        max_backward_drift_ms: int = 0,
    ) -> None:
        self._target_fps: int = target_fps
        self._max_backward_drift_ms: int = max(0, int(max_backward_drift_ms))
        self._interval_ms: float = 1000.0 / target_fps if target_fps > 0 else 0.0
        self._health: Health = "OK" if target_fps > 0 else "DOWN"

        self._last_input_ts_ms: Optional[int] = None
        self._first_accepted_ts_ms: Optional[int] = None
        self._last_accepted_ts_ms: Optional[int] = None
        self._next_sample_ts_ms: Optional[float] = None

        self._total_in: int = 0
        self._total_passed: int = 0
        self._total_dropped: int = 0

    @property
    def target_fps(self) -> int:
        return self._target_fps

    @property
    def interval_ms(self) -> float:
        return self._interval_ms

    @property
    def total_in(self) -> int:
        return self._total_in

    @property
    def total_passed(self) -> int:
        return self._total_passed

    @property
    def total_dropped(self) -> int:
        return self._total_dropped

    def health(self) -> Health:
        return self._health

    def reset(self) -> None:
        """Đặt lại bộ đếm và trạng thái khóa pha về ban đầu."""
        self._last_input_ts_ms = None
        self._first_accepted_ts_ms = None
        self._last_accepted_ts_ms = None
        self._next_sample_ts_ms = None
        self._total_in = 0
        self._total_passed = 0
        self._total_dropped = 0
        self._health = "OK" if self._target_fps > 0 else "DOWN"

    def stats(self) -> SamplerStats:
        """Trả về thông số đo đạc tỉ lệ qua cổng và FPS đầu ra thực tế."""
        ratio = (self._total_passed / self._total_in) if self._total_in > 0 else 0.0
        measured_fps = 0.0
        if (
            self._first_accepted_ts_ms is not None
            and self._last_accepted_ts_ms is not None
            and self._last_accepted_ts_ms > self._first_accepted_ts_ms
            and self._total_passed > 1
        ):
            span_s = (self._last_accepted_ts_ms - self._first_accepted_ts_ms) / 1000.0
            measured_fps = round((self._total_passed - 1) / span_s, 2)
        elif self._total_passed == 1:
            measured_fps = float(self._target_fps)

        return SamplerStats(
            target_fps=self._target_fps,
            total_in=self._total_in,
            total_passed=self._total_passed,
            total_dropped=self._total_dropped,
            pass_ratio=round(ratio, 4),
            measured_output_fps=measured_fps,
        )

    def _make_error(
        self,
        trace_id: str,
        code: ErrorCode,
        err_class: str,
        message: str,
        recoverable: bool = True,
    ) -> ErrorEvent:
        safe_trace = trace_id if trace_id else "sampler-missing-trace"
        return ErrorEvent(
            trace_id=safe_trace,
            ts_ms=_now_ms(),
            source="process.sampler",
            code=code,
            node_path="process.sampler",
            err_class="data" if err_class == "data" else "config",
            message=message,
            recoverable=recoverable,
        )

    def filter_packet(
        self, packet: SignalPacket
    ) -> Union[Optional[SignalPacket], ErrorEvent]:
        """Lọc gói SignalPacket theo nhịp target_fps.

        - Trả về SignalPacket (source='process.sampler') nếu khung hình được chọn đưa vào xử lý.
        - Trả về None nếu khung hình bị lược bỏ để hạ nhiệt.
        - Trả về ErrorEvent nếu cấu hình sai, gói sai kiểu, thiếu trace_id hoặc lùi timestamp.
        """
        if self._target_fps <= 0 or isinstance(self._target_fps, bool):
            self._health = "DOWN"
            trace_fallback = getattr(packet, "trace_id", "") or "sampler-bad-cfg"
            return self._make_error(
                trace_fallback,
                "E_CFG_INVALID",
                "config",
                f"target_fps không hợp lệ ({self._target_fps}), phải là số nguyên dương",
                recoverable=False,
            )

        if not isinstance(packet, SignalPacket):
            self._health = "DEGRADED"
            return self._make_error(
                "sampler-bad-type",
                "E_SIG_BAD_FORMAT",
                "data",
                f"Đầu vào của FpsThrottle phải là SignalPacket, nhận {type(packet).__name__}",
                recoverable=True,
            )

        if not packet.trace_id or not packet.trace_id.strip():
            self._health = "DEGRADED"
            return self._make_error(
                "sampler-empty-trace",
                "E_SIG_BAD_FORMAT",
                "data",
                "SignalPacket đi vào process.sampler thiếu trace_id",
                recoverable=True,
            )

        if packet.width <= 0 or packet.height <= 0 or packet.frame_id <= 0:
            self._health = "DEGRADED"
            return self._make_error(
                packet.trace_id,
                "E_SIG_BAD_FORMAT",
                "data",
                f"Thông số frame không hợp lệ (id={packet.frame_id}, {packet.width}x{packet.height})",
                recoverable=True,
            )

        ts_ms = packet.ts_ms
        if self._last_input_ts_ms is not None:
            if ts_ms < self._last_input_ts_ms - self._max_backward_drift_ms:
                self._health = "DEGRADED"
                return self._make_error(
                    packet.trace_id,
                    "E_SIG_TS_DRIFT",
                    "data",
                    f"Timestamp lùi tại process.sampler: nhận {ts_ms}ms < trước đó {self._last_input_ts_ms}ms",
                    recoverable=True,
                )

        self._last_input_ts_ms = ts_ms
        self._total_in += 1

        if self._next_sample_ts_ms is None or (float(ts_ms) + 0.5) >= self._next_sample_ts_ms:
            self._total_passed += 1
            if self._first_accepted_ts_ms is None:
                self._first_accepted_ts_ms = ts_ms
            self._last_accepted_ts_ms = ts_ms

            if self._next_sample_ts_ms is None:
                self._next_sample_ts_ms = float(ts_ms) + self._interval_ms
            else:
                self._next_sample_ts_ms += self._interval_ms
                if self._next_sample_ts_ms <= float(ts_ms) - self._interval_ms:
                    self._next_sample_ts_ms = float(ts_ms) + self._interval_ms

            self._health = "OK"
            return SignalPacket(
                trace_id=packet.trace_id,
                ts_ms=packet.ts_ms,
                source="process.sampler",
                frame_id=packet.frame_id,
                width=packet.width,
                height=packet.height,
                driver_id=packet.driver_id,
                sensor=packet.sensor,
            )

        self._total_dropped += 1
        self._health = "OK"
        return None

    def self_test(self) -> Tuple[bool, str]:
        if self._target_fps <= 0:
            return False, f"E_CFG_INVALID: target_fps={self._target_fps}"

        probe = FpsThrottle(target_fps=5)
        passed = 0
        for i in range(25):
            pkt = SignalPacket(
                trace_id=f"sampler-self-{i + 1}",
                ts_ms=1000 + i * 40,
                source="signal",
                frame_id=i + 1,
                width=640,
                height=480,
                driver_id="drv-selftest",
            )
            res = probe.filter_packet(pkt)
            if isinstance(res, ErrorEvent):
                return False, f"{res.code}: {res.message}"
            if res is not None:
                passed += 1

        if passed != 5:
            return False, f"Sai lệch tỉ lệ lấy mẫu: kỳ vọng 5 frame/s, nhận {passed}"
        return True, f"FpsThrottle OK (target_fps={self._target_fps}, 25fps->5fps chuẩn xác)"
