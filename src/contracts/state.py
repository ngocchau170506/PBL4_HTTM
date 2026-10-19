"""MÁY TRẠNG THÁI — chỉ KHAI BÁO KIỂU và BẢNG CHUYỂN, không có logic.

- Đổi file này = Change Request (CR-xxx), MAIN duyệt.
- Chỉ import từ contracts.logic. Không import fd_* khác.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from contracts.logic import Health, NodeId

# ---------- Trạng thái toàn hệ thống ----------
SystemState = Literal[
    "BOOT",       # nạp config, tự kiểm tra node
    "IDLE",       # chưa có tài xế
    "BASELINE",   # học khung chuẩn 5–10s cho 1 tài xế
    "MONITOR",    # AI giám sát liên tục
    "DEGRADED",   # nhánh optional hỏng (vd ESP32), phần còn lại chạy tiếp
    "FAULT",      # node lõi hỏng: dừng, chờ diagnose + MAIN
    "SHUTDOWN",
]

# Chuyển ngoài bảng này = lỗi
SYSTEM_TRANSITIONS: Dict[str, List[str]] = {
    "BOOT": ["IDLE", "FAULT"],
    "IDLE": ["BASELINE", "SHUTDOWN", "FAULT"],
    "BASELINE": ["MONITOR", "IDLE", "FAULT"],
    "MONITOR": ["BASELINE", "IDLE", "DEGRADED", "FAULT"],
    "DEGRADED": ["MONITOR", "FAULT", "IDLE"],
    "FAULT": ["IDLE", "SHUTDOWN"],         # chỉ ra khỏi FAULT khi đã có hồ sơ BUG
    "SHUTDOWN": [],
}


# ---------- Trạng thái node ----------
@dataclass(frozen=True)
class NodeStatus:
    node_id: NodeId
    health: Health
    last_packet_ts_ms: Optional[int] = None
    queue_depth: Optional[int] = None
    last_error_code: Optional[str] = None


# ---------- Baseline theo tài xế ----------
BaselineState = Literal["NONE", "COLLECTING", "LOCKED", "EXPIRED"]


@dataclass(frozen=True)
class BaselineProfile:
    driver_id: str                         # mỗi driver_id 1 profile riêng, không ghi đè
    state: BaselineState
    started_ms: int
    duration_ms: int                       # nằm trong khoảng baseline_seconds của fd_config
    frame_count: int


# ---------- Timeline vi phạm (-2s -> 0s -> hết) ----------
ViolationPhase = Literal["WATCHING", "SUSPECT", "ACTIVE", "CLOSED"]

VIOLATION_TRANSITIONS: Dict[str, List[str]] = {
    "WATCHING": ["SUSPECT"],
    "SUSPECT": ["ACTIVE", "WATCHING"],     # dấu hiệu giả -> quay lại
    "ACTIVE": ["CLOSED"],
    "CLOSED": ["WATCHING"],
}


# ---------- Quy trình làm việc của AI (khớp CLAUDE.md mục 1) ----------
WorkState = Literal[
    "PROPOSE", "APPROVED", "BUILD", "TEST", "REPORT", "REVIEW", "MERGED",
    "DIAGNOSE", "FIX", "BLOCKED",
]

WORK_TRANSITIONS: Dict[str, List[str]] = {
    "PROPOSE": ["APPROVED", "BLOCKED"],    # chỉ MAIN đẩy sang APPROVED
    "APPROVED": ["BUILD"],
    "BUILD": ["TEST"],
    "TEST": ["REPORT", "DIAGNOSE"],
    "DIAGNOSE": ["FIX", "BLOCKED"],
    "FIX": ["TEST"],
    "REPORT": ["REVIEW"],
    "REVIEW": ["MERGED", "BUILD", "BLOCKED"],  # chỉ MAIN đẩy sang MERGED
    "MERGED": [],
    "BLOCKED": ["PROPOSE"],
}
