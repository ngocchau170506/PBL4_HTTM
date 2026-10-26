"""Luật ngáp kéo dài (FEAT-031).

- Ngáp liên tục >= trigger_ms (ngưỡng ở fd_config, R4) mới vi phạm.
- Nhóm nhãn ngáp/không ngáp và độ tin cậy tối thiểu do cấu hình cung cấp, không hard-code.
- Không đọc camera, không gọi server, không giữ ảnh.
"""
from __future__ import annotations

from typing import FrozenSet

from contracts.logic import DetectionResult
from fd_process.rules.util.label_vote import vote
from fd_process.rules.util.sustained import SustainedCondition, SustainedStatus


class MouthYawnRule:
    name: str = "mouth_yawn"

    def __init__(
        self,
        yawn_labels: FrozenSet[str],
        no_yawn_labels: FrozenSet[str],
        min_conf: float,
        trigger_ms: int,
        max_gap_ms: int,
    ) -> None:
        if yawn_labels & no_yawn_labels:
            raise ValueError("yawn_labels và no_yawn_labels không được trùng nhau")
        if not yawn_labels:
            raise ValueError("yawn_labels không được rỗng")
        self._yawn = frozenset(yawn_labels)
        self._no_yawn = frozenset(no_yawn_labels)
        self._min_conf = float(min_conf)
        self._cond = SustainedCondition(trigger_ms=trigger_ms, max_gap_ms=max_gap_ms)

    def observe(self, result: DetectionResult) -> SustainedStatus:
        state = vote(result.items, self._yawn, self._no_yawn, self._min_conf)
        return self._cond.observe(result.ts_ms, state)
