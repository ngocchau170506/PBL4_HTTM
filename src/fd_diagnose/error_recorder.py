"""Bộ thu nhận ErrorEvent và kết xuất hồ sơ chẩn đoán (FEAT-014).

- Nằm ngoài đường chạy chính; tiếp nhận ErrorEvent từ fd_flow.
- Lưu trữ bằng deque giới hạn dung lượng để không rò bộ nhớ khi chạy dài hạn.
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, List, Optional, Tuple

from contracts.logic import ErrorEvent, Health, NodeId


def _now_ms() -> int:
    return int(time.time() * 1000)


class ErrorRecorder:
    """Thực thi NodeContract cho node diagnose."""

    node_id: NodeId = "diagnose"

    def __init__(self, max_events: int = 200) -> None:
        safe_max = max(1, int(max_events))
        self._max_events: int = safe_max
        self._events: Deque[ErrorEvent] = deque(maxlen=safe_max)
        self._health: Health = "OK"

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def max_capacity(self) -> int:
        return self._max_events

    def health(self) -> Health:
        return self._health

    def record(self, event: ErrorEvent) -> str:
        """Ghi nhận 1 ErrorEvent và trả về chuỗi tóm tắt chuẩn có node_path + trace_id."""
        if not isinstance(event, ErrorEvent):
            self._health = "DEGRADED"
            return "[DIAGNOSE-WARN] Gói đưa vào không phải ErrorEvent"

        self._events.append(event)
        return (
            f"[{event.code}] nodePath={event.node_path} | "
            f"trace_id={event.trace_id} | class={event.err_class} | "
            f"recoverable={event.recoverable} | msg={event.message}"
        )

    def latest(self) -> Optional[ErrorEvent]:
        if not self._events:
            return None
        return self._events[-1]

    def history(self) -> List[ErrorEvent]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()

    def format_bug_draft(self, bug_id: str, event: ErrorEvent) -> str:
        """Tạo nội dung nháp hồ sơ BUG-xxx từ ErrorEvent để phục vụ Diagnoser."""
        return (
            f"# {bug_id} — Hồ sơ lỗi tự động trích xuất\n\n"
            f"- Mã lỗi (`code`): `{event.code}`\n"
            f"- Vị trí (`nodePath`): `{event.node_path}`\n"
            f"- Mã vết (`trace_id`): `{event.trace_id}`\n"
            f"- Phân loại (`err_class`): `{event.err_class}`\n"
            f"- Thời điểm (`ts_ms`): `{event.ts_ms}`\n"
            f"- Khả năng tự hồi phục (`recoverable`): `{event.recoverable}`\n"
            f"- Chi tiết: {event.message}\n"
        )

    def self_test(self) -> Tuple[bool, str]:
        sample = ErrorEvent(
            trace_id="diag-selftest",
            ts_ms=_now_ms(),
            source="diagnose",
            code="E_CONN_LOST",
            node_path="connect.camera",
            err_class="source",
            message="Kiểm thử tự thân ErrorRecorder",
            recoverable=True,
        )
        line = self.record(sample)
        self._events.pop()
        if "nodePath=connect.camera" not in line:
            return False, "Định dạng dòng chẩn đoán thiếu nodePath"
        return True, f"ErrorRecorder OK (max_events={self._max_events})"
