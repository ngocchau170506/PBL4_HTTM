"""Quản lý hàng đợi gói tin theo cạnh của cây luồng (src/fd_flow/queue_manager.py).

- Giới hạn dung lượng cố định cho mỗi cạnh để chống tràn RAM.
- Khi hàng đợi của cạnh đầy, phát ErrorEvent mã E_FLOW_QUEUE_FULL kèm node_path='<src>-><dst>'.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple, Union

from contracts.logic import ErrorEvent, NodeId


def _now_ms() -> int:
    return int(time.time() * 1000)


class EdgeQueueManager:
    """Quản lý các hàng đợi non-blocking theo từng cạnh (src, dst)."""

    def __init__(self, max_depth_per_edge: int = 32) -> None:
        self._max_depth: int = max(1, int(max_depth_per_edge))
        self._queues: Dict[Tuple[NodeId, NodeId], Deque[Any]] = {}

    @property
    def max_depth(self) -> int:
        return self._max_depth

    def depth(self, src: NodeId, dst: NodeId) -> int:
        q = self._queues.get((src, dst))
        return len(q) if q is not None else 0

    def enqueue(
        self,
        src: NodeId,
        dst: NodeId,
        packet: Any,
        trace_id: str,
    ) -> Optional[ErrorEvent]:
        """Đưa gói vào hàng đợi cạnh src->dst. Trả về ErrorEvent nếu hàng đợi đã đầy."""
        key = (src, dst)
        q = self._queues.get(key)
        if q is None:
            q = deque()
            self._queues[key] = q

        if len(q) >= self._max_depth:
            safe_trace = trace_id if trace_id else "flow-no-trace"
            return ErrorEvent(
                trace_id=safe_trace,
                ts_ms=_now_ms(),
                source="flow",
                code="E_FLOW_QUEUE_FULL",
                node_path=f"{src}->{dst}",
                err_class="performance",
                message=f"Hàng đợi cạnh {src}->{dst} đầy ({len(q)}/{self._max_depth})",
                recoverable=True,
            )

        q.append(packet)
        return None

    def dequeue(self, src: NodeId, dst: NodeId) -> Union[Any, None]:
        """Lấy 1 gói từ đầu hàng đợi cạnh src->dst (hoặc None nếu rỗng)."""
        q = self._queues.get((src, dst))
        if not q:
            return None
        return q.popleft()

    def clear_all(self) -> None:
        for q in self._queues.values():
            q.clear()
