"""Bộ ghi nhận phản hồi đầu ra cho G1 (src/fd_response/local_alert.py).

- Tiếp nhận DetectionResult hoặc ViolationEvent ở cuối luồng G1 và lưu vào bộ đệm vòng.
- Giới hạn cố định dung lượng deque để không rò bộ nhớ khi chạy liên tục.
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, List, Optional, Tuple, Union

from contracts.logic import (
    Detection,
    DetectionResult,
    ErrorEvent,
    Health,
    NodeId,
    ViolationEvent,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class ResponseLogger:
    """Thực thi NodeContract cho node response ở G1."""

    node_id: NodeId = "response"

    def __init__(self, max_logs: int = 50) -> None:
        safe_max = max(1, int(max_logs))
        self._max_logs: int = safe_max
        self._logs: Deque[str] = deque(maxlen=safe_max)
        self._processed_count: int = 0
        self._health: Health = "OK"

    @property
    def processed_count(self) -> int:
        return self._processed_count

    @property
    def buffer_size(self) -> int:
        return len(self._logs)

    def health(self) -> Health:
        return self._health

    def handle(
        self, packet: Union[DetectionResult, ViolationEvent]
    ) -> Union[str, ErrorEvent]:
        """Ghi nhận gói đầu ra từ luồng xử lý."""
        if isinstance(packet, DetectionResult):
            if not packet.trace_id:
                self._health = "DEGRADED"
                return ErrorEvent(
                    trace_id="resp-no-trace",
                    ts_ms=_now_ms(),
                    source="response",
                    code="E_RESP_SEND",
                    node_path="response",
                    err_class="contract",
                    message="DetectionResult thiếu trace_id",
                    recoverable=True,
                )
            labels = ",".join(f"{it.label}:{it.conf:.2f}" for it in packet.items)
            entry = (
                f"[RESP-DET] trace_id={packet.trace_id} | "
                f"frame_id={packet.frame_id} | items=[{labels}]"
            )
            self._logs.append(entry)
            self._processed_count += 1
            self._health = "OK"
            return entry

        if isinstance(packet, ViolationEvent):
            if not packet.trace_id:
                self._health = "DEGRADED"
                return ErrorEvent(
                    trace_id="resp-no-trace",
                    ts_ms=_now_ms(),
                    source="response",
                    code="E_RESP_SEND",
                    node_path="response",
                    err_class="contract",
                    message="ViolationEvent thiếu trace_id",
                    recoverable=True,
                )
            entry = (
                f"[RESP-VIO] trace_id={packet.trace_id} | "
                f"rule={packet.rule} | t0_ms={packet.t0_ms} | frames={len(packet.frame_ids)}"
            )
            self._logs.append(entry)
            self._processed_count += 1
            self._health = "OK"
            return entry

        self._health = "DEGRADED"
        return ErrorEvent(
            trace_id="resp-bad-type",
            ts_ms=_now_ms(),
            source="response",
            code="E_RESP_SEND",
            node_path="response",
            err_class="contract",
            message="Gói đưa vào response không hợp lệ",
            recoverable=True,
        )

    def latest_log(self) -> Optional[str]:
        if not self._logs:
            return None
        return self._logs[-1]

    def logs(self) -> List[str]:
        return list(self._logs)

    def self_test(self) -> Tuple[bool, str]:
        sample = DetectionResult(
            trace_id="resp-selftest",
            ts_ms=_now_ms(),
            source="process.ai",
            frame_id=1,
            items=(Detection(label="driver_normal", conf=0.9),),
        )
        res = self.handle(sample)
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, f"ResponseLogger OK (max_logs={self._max_logs})"
