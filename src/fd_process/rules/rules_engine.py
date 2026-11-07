"""Node process.rules: điều phối các luật, phát ViolationEvent (FEAT-031).

- Nhận DetectionResult (cạnh process.ai -> process.rules), trả RulesOutput hoặc ErrorEvent.
- ViolationEvent phát đúng 1 lần khi luật chuyển sang TRIGGERED; t0_ms là lúc điều kiện bắt đầu.
- frame_ids chỉ gồm các khung từ t0 tới lúc phát. Phần tiền sự kiện (-2s) do fd_response ghép
  từ ring buffer qua fd_flow, vì fd_process không được import fd_response (R2).
- Không nằm trong fd_flow; việc nối vào luồng là bước riêng (I-004).
"""
from __future__ import annotations

import copy
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Protocol, Sequence, Tuple, Union

from contracts.logic import DetectionResult, ErrorEvent, Health, NodeId, ViolationEvent
from fd_process.rules.util.sustained import SustainedStatus

_NODE_PATH = "process.ai->process.rules"


class Rule(Protocol):
    name: str

    def observe(self, result: DetectionResult) -> SustainedStatus: ...


@dataclass(frozen=True)
class RulesOutput:
    violations: Tuple[ViolationEvent, ...]
    suspects: Tuple[str, ...]      # luật đang SUSPECT/TRIGGERED/ACTIVE trong khung này
    ended: Tuple[str, ...]         # luật vừa kết thúc vi phạm


def _now_ms() -> int:
    return int(time.time() * 1000)


class RulesEngine:
    node_id: NodeId = "process.rules"

    def __init__(self, rules: Sequence[Rule], pre_event_ms: int, history_frames: int) -> None:
        if not rules:
            raise ValueError("rules không được rỗng")
        names = [r.name for r in rules]
        if len(set(names)) != len(names):
            raise ValueError("tên luật không được trùng nhau")
        if isinstance(pre_event_ms, bool) or not isinstance(pre_event_ms, int) or pre_event_ms < 0:
            raise ValueError("pre_event_ms phải là số nguyên >= 0")
        if isinstance(history_frames, bool) or not isinstance(history_frames, int) or history_frames < 1:
            raise ValueError("history_frames phải là số nguyên >= 1")
        self._rules = tuple(rules)
        self._pre_ms = pre_event_ms
        self._frames: Deque[Tuple[int, int]] = deque(maxlen=history_frames)
        self._health: Health = "OK"

    def health(self) -> Health:
        return self._health

    def _error(self, trace_id: str, message: str) -> ErrorEvent:
        self._health = "DEGRADED"
        return ErrorEvent(
            trace_id=trace_id or "NO_TRACE",
            ts_ms=_now_ms(),
            source="process.rules",
            code="E_PROC_BAD_OUTPUT",
            node_path=_NODE_PATH,
            err_class="contract",
            message=message,
            recoverable=True,
        )

    def process(self, result: DetectionResult) -> Union[RulesOutput, ErrorEvent]:
        if not isinstance(result, DetectionResult):
            return self._error("NO_TRACE", "Đầu vào rules không phải DetectionResult")
        if not result.trace_id:
            return self._error("NO_TRACE", "DetectionResult thiếu trace_id")
        if self._frames and result.ts_ms < self._frames[-1][1]:
            return self._error(
                result.trace_id,
                f"ts_ms lùi: {result.ts_ms} < {self._frames[-1][1]}",
            )

        self._frames.append((result.frame_id, result.ts_ms))
        violations = []
        suspects = []
        ended = []
        for rule in self._rules:
            st = rule.observe(result)
            if st.ended_previous or st.phase == "ENDED":
                ended.append(rule.name)
            if st.phase in ("SUSPECT", "TRIGGERED", "ACTIVE"):
                suspects.append(rule.name)
            if st.phase == "TRIGGERED" and st.onset_ms is not None:
                onset = st.onset_ms
                violations.append(
                    ViolationEvent(
                        trace_id=result.trace_id,
                        ts_ms=result.ts_ms,
                        source="process.rules",
                        rule=rule.name,
                        t0_ms=onset,
                        pre_ms=self._pre_ms,
                        frame_ids=tuple(fid for fid, ts in self._frames if ts >= onset),
                    )
                )
        self._health = "OK"
        return RulesOutput(tuple(violations), tuple(suspects), tuple(ended))

    def self_test(self) -> Tuple[bool, str]:
        probe = RulesEngine(copy.deepcopy(self._rules), self._pre_ms, 1)
        out = probe.process(
            DetectionResult(
                trace_id="rules-selftest", ts_ms=_now_ms(), source="process.ai",
                frame_id=1, items=(),
            )
        )
        if isinstance(out, ErrorEvent):
            return False, f"{out.code}: {out.message}"
        return True, f"RulesEngine OK (rules={[r.name for r in self._rules]})"
