"""Luật nhắm mắt / ngủ gật (FEAT-031).

- Mắt nhắm liên tục >= trigger_ms (ngưỡng ở fd_config, R4) mới vi phạm; chớp mắt ngắn không báo.
- Nhóm nhãn nhắm/mở và độ tin cậy tối thiểu do cấu hình cung cấp, không hard-code.
- Không đọc camera, không gọi server, không giữ ảnh.
"""
from __future__ import annotations

from typing import FrozenSet

from contracts.logic import DetectionResult
from fd_process.rules.util.label_vote import vote
from fd_process.rules.util.sustained import SustainedCondition, SustainedStatus


class EyeDrowsinessRule:
    name: str = "eye_drowsiness"

    def __init__(
        self,
        closed_labels: FrozenSet[str],
        open_labels: FrozenSet[str],
        min_conf: float,
        trigger_ms: int,
        max_gap_ms: int,
    ) -> None:
        if closed_labels & open_labels:
            raise ValueError("closed_labels và open_labels không được trùng nhau")
        if not closed_labels:
            raise ValueError("closed_labels không được rỗng")
        self._closed = frozenset(closed_labels)
        self._open = frozenset(open_labels)
        self._min_conf = float(min_conf)
        self._cond = SustainedCondition(trigger_ms=trigger_ms, max_gap_ms=max_gap_ms)

    def observe(self, result: DetectionResult) -> SustainedStatus:
        state = vote(result.items, self._closed, self._open, self._min_conf)
        return self._cond.observe(result.ts_ms, state)
