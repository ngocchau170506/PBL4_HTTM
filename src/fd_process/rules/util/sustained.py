"""Đếm thời gian một điều kiện kéo dài liên tục (hàm hỗ trợ, không phải luật).

- Điều kiện chỉ tính liên tục nếu khoảng trống giữa hai lần quan sát dương <= max_gap_ms.
  Mất khung quá lâu thì đếm lại từ đầu, để thiếu dữ liệu không bị coi là vi phạm.
- Điều kiện kéo dài >= trigger_ms mới thành TRIGGERED, nên chớp mắt ngắn không báo.
- onset_ms là lúc điều kiện bắt đầu, dùng làm t0 (0s) của sự kiện.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

SustainedPhase = Literal["IDLE", "SUSPECT", "TRIGGERED", "ACTIVE", "ENDED"]


@dataclass(frozen=True)
class SustainedStatus:
    phase: SustainedPhase
    onset_ms: Optional[int]
    elapsed_ms: int
    ended_previous: bool = False   # True: một đợt đang ACTIVE vừa bị cắt do mất dữ liệu quá max_gap_ms


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} phải là số nguyên dương")
    return value


class SustainedCondition:
    def __init__(self, trigger_ms: int, max_gap_ms: int) -> None:
        self._trigger_ms = _positive_int("trigger_ms", trigger_ms)
        self._max_gap_ms = _positive_int("max_gap_ms", max_gap_ms)
        self._onset: Optional[int] = None
        self._last_true: Optional[int] = None
        self._triggered: bool = False

    def _reset(self) -> None:
        self._onset = None
        self._last_true = None
        self._triggered = False

    def observe(self, ts_ms: int, state: Optional[bool]) -> SustainedStatus:
        """state: True = điều kiện đang xảy ra, False = chắc chắn không, None = không xác định."""
        ended_previous = False
        if self._onset is not None and self._last_true is not None:
            if ts_ms - self._last_true > self._max_gap_ms:
                ended_previous = self._triggered
                self._reset()

        if state is True:
            if self._onset is None:
                self._onset = ts_ms
            self._last_true = ts_ms
            elapsed = ts_ms - self._onset
            if self._triggered:
                return SustainedStatus("ACTIVE", self._onset, elapsed, ended_previous)
            if elapsed >= self._trigger_ms:
                self._triggered = True
                return SustainedStatus("TRIGGERED", self._onset, elapsed, ended_previous)
            return SustainedStatus("SUSPECT", self._onset, elapsed, ended_previous)

        if state is False:
            was_triggered = self._triggered
            onset = self._onset
            elapsed = 0 if onset is None else ts_ms - onset
            self._reset()
            if was_triggered:
                return SustainedStatus("ENDED", onset, elapsed, ended_previous)
            return SustainedStatus("IDLE", None, 0, ended_previous)

        if self._onset is None:
            if ended_previous:
                return SustainedStatus("ENDED", None, 0, False)
            return SustainedStatus("IDLE", None, 0, False)
        elapsed = ts_ms - self._onset
        phase: SustainedPhase = "ACTIVE" if self._triggered else "SUSPECT"
        return SustainedStatus(phase, self._onset, elapsed, False)
