"""Chọn trạng thái của một khung từ danh sách Detection (hàm hỗ trợ, không phải luật).

- Chỉ xét Detection có conf >= min_conf.
- Lấy nhãn có conf cao nhất trong nhóm positive hoặc negative.
- Hai nhóm hòa conf, hoặc không có nhãn liên quan -> None (không xác định), không đoán.
"""
from __future__ import annotations

from typing import FrozenSet, Optional, Sequence

from contracts.logic import Detection


def vote(
    items: Sequence[Detection],
    positive: FrozenSet[str],
    negative: FrozenSet[str],
    min_conf: float,
) -> Optional[bool]:
    best_pos = -1.0
    best_neg = -1.0
    for it in items:
        if it.conf < min_conf:
            continue
        if it.label in positive and it.conf > best_pos:
            best_pos = it.conf
        elif it.label in negative and it.conf > best_neg:
            best_neg = it.conf
    if best_pos < 0.0 and best_neg < 0.0:
        return None
    if best_pos == best_neg:
        return None
    return best_pos > best_neg
