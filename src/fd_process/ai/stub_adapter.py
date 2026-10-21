"""StubAdapter trả kết quả nhận dạng giả lập có kiểm soát cho G1 (FEAT-013).

- Tuân thủ giao diện AiAdapter và NodeContract trong src/contracts/logic.py.
- Cho phép điều khiển kết quả giả lập để kiểm thử luồng mà không cần model YOLO nặng.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from contracts.logic import (
    Detection,
    DetectionResult,
    ErrorEvent,
    Health,
    NodeId,
    SignalPacket,
)
from fd_process.ai.adapter_base import run_ai


def _now_ms() -> int:
    return int(time.time() * 1000)


class StubAdapter:
    """Thực thi AiAdapter (name='stub') và NodeContract (node_id='process.ai')."""

    name: str = "stub"
    node_id: NodeId = "process.ai"

    def __init__(
        self,
        default_items: Optional[Tuple[Detection, ...]] = None,
        simulated_delay_ms: int = 0,
    ) -> None:
        self._loaded: bool = False
        self._health: Health = "OK"
        self._simulated_delay_ms: int = max(0, int(simulated_delay_ms))
        if default_items is not None:
            self._items: Tuple[Detection, ...] = default_items
        else:
            self._items = (
                Detection(label="driver_normal", conf=0.95, box=(160, 80, 480, 400)),
            )

    def load(self) -> None:
        self._loaded = True
        self._health = "OK"

    def set_health(self, status: Health) -> None:
        self._health = status

    def set_items(self, items: Tuple[Detection, ...]) -> None:
        self._items = items

    def set_simulated_delay_ms(self, delay_ms: int) -> None:
        self._simulated_delay_ms = max(0, int(delay_ms))

    def health(self) -> Health:
        return self._health

    def infer(self, frame: SignalPacket) -> DetectionResult:
        if not self._loaded:
            self.load()
        if self._simulated_delay_ms > 0:
            time.sleep(self._simulated_delay_ms / 1000.0)
        return DetectionResult(
            trace_id=frame.trace_id,
            ts_ms=_now_ms(),
            source="process.ai",
            frame_id=frame.frame_id,
            items=self._items,
        )

    def self_test(self) -> Tuple[bool, str]:
        self.load()
        sample_sig = SignalPacket(
            trace_id="stub-selftest",
            ts_ms=_now_ms(),
            source="signal",
            frame_id=1,
            width=640,
            height=480,
        )
        res = run_ai(self, sample_sig, timeout_ms=500)
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, f"StubAdapter OK (items={len(res.items)}, frame_id={res.frame_id})"
