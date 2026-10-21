"""Bộ ngắt mạch cô lập node/cạnh lỗi và quản lý trạng thái hệ thống (src/fd_flow/circuit_breaker.py).

- Theo dõi NodeStatus từng node trên cây luồng.
- Kiểm soát chuyển trạng thái SystemState theo đúng bảng SYSTEM_TRANSITIONS trong contracts/state.py.
- Cho phép hệ thống chạy ở chế độ suy giảm (DEGRADED) nếu lỗi thuộc cạnh optional (như connect.esp32).
"""
from __future__ import annotations

from typing import Dict, Optional, Set

from contracts.logic import FLOW_TREE, ErrorEvent, Health, NodeId
from contracts.state import SYSTEM_TRANSITIONS, NodeStatus, SystemState


class CircuitBreaker:
    """Theo dõi sức khỏe các node và điều khiển chuyển trạng thái toàn hệ thống."""

    def __init__(self) -> None:
        self._system_state: SystemState = "BOOT"
        self._node_status: Dict[NodeId, NodeStatus] = {}
        self._optional_nodes: Set[NodeId] = {
            edge.src for edge in FLOW_TREE if edge.optional
        }

    @property
    def system_state(self) -> SystemState:
        return self._system_state

    def can_transition(self, target: SystemState) -> bool:
        allowed = SYSTEM_TRANSITIONS.get(self._system_state, [])
        return target in allowed

    def transition_to(self, target: SystemState) -> bool:
        """Chuyển trạng thái hệ thống nếu hợp lệ theo SYSTEM_TRANSITIONS."""
        if target == self._system_state:
            return True
        if not self.can_transition(target):
            return False
        self._system_state = target
        return True

    def update_node(
        self,
        node_id: NodeId,
        health: Health,
        last_packet_ts_ms: Optional[int] = None,
        queue_depth: Optional[int] = None,
        last_error_code: Optional[str] = None,
    ) -> NodeStatus:
        prev = self._node_status.get(node_id)
        ts_val = (
            last_packet_ts_ms
            if last_packet_ts_ms is not None
            else (prev.last_packet_ts_ms if prev else None)
        )
        q_val = (
            queue_depth
            if queue_depth is not None
            else (prev.queue_depth if prev else 0)
        )
        err_val = (
            last_error_code
            if last_error_code is not None
            else (prev.last_error_code if prev else None)
        )
        status = NodeStatus(
            node_id=node_id,
            health=health,
            last_packet_ts_ms=ts_val,
            queue_depth=q_val,
            last_error_code=err_val,
        )
        self._node_status[node_id] = status
        return status

    def get_node_status(self, node_id: NodeId) -> Optional[NodeStatus]:
        return self._node_status.get(node_id)

    def all_statuses(self) -> Dict[NodeId, NodeStatus]:
        return dict(self._node_status)

    def is_optional_source(self, node_id: NodeId) -> bool:
        return node_id in self._optional_nodes

    def on_error(self, event: ErrorEvent) -> SystemState:
        """Cập nhật trạng thái node và hệ thống khi phát sinh ErrorEvent."""
        src_node = event.source
        new_health: Health = "DEGRADED" if event.recoverable else "DOWN"
        if event.code in ("E_CONN_LOST", "E_CONN_OPEN", "E_FLOW_NODE_DOWN"):
            new_health = "DOWN"

        self.update_node(
            node_id=src_node,
            health=new_health,
            last_packet_ts_ms=event.ts_ms,
            last_error_code=event.code,
        )

        if self.is_optional_source(src_node):
            if self.can_transition("DEGRADED"):
                self.transition_to("DEGRADED")
            return self._system_state

        if new_health == "DOWN" or not event.recoverable:
            if self.can_transition("FAULT"):
                self.transition_to("FAULT")
        elif self.can_transition("DEGRADED"):
            self.transition_to("DEGRADED")

        return self._system_state
