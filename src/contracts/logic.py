"""HỢP ĐỒNG DỮ LIỆU — chỉ KHAI BÁO KIỂU, không có logic.

- Đổi file này = Change Request (CR-xxx), MAIN duyệt. AI không tự sửa.
- Chỉ dùng thư viện chuẩn của Python. Không import từ fd_* nào khác (luật R2).
- Mọi gói dữ liệu giữa các fd phải dùng đúng kiểu trong file này (luật R3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional, Protocol, Tuple

# ---------- Định danh node ----------
NodeId = Literal[
    "connect.camera", "connect.esp32",
    "signal",
    "process.baseline", "process.sampler", "process.ai", "process.rules",
    "response",
    "flow", "diagnose", "faultsim", "registry", "config", "eval",
]

Health = Literal["OK", "DEGRADED", "DOWN"]

PacketKind = Literal[
    "RawPacket", "SignalPacket", "DetectionResult", "ViolationEvent", "ErrorEvent",
]

ErrorClass = Literal[
    "source", "data", "contract", "algorithm", "performance", "config", "process",
]

ErrorCode = Literal[
    "E_CONN_OPEN", "E_CONN_LOST", "E_CONN_TIMEOUT",
    "E_SIG_BAD_FORMAT", "E_SIG_EMPTY", "E_SIG_TS_DRIFT",
    "E_PROC_MODEL_LOAD", "E_PROC_TIMEOUT", "E_PROC_BAD_OUTPUT",
    "E_FLOW_EDGE_TYPE", "E_FLOW_QUEUE_FULL", "E_FLOW_NODE_DOWN",
    "E_RESP_SEND", "E_RESP_NO_ACK", "E_RESP_BUFFER_OVERRUN",
    "E_CFG_INVALID",
    "E_SCOPE_VIOLATION",
]


# ---------- Cây luồng ----------
@dataclass(frozen=True)
class FlowEdge:
    src: NodeId
    dst: NodeId
    carries: PacketKind
    optional: bool = False  # True = mất cạnh này thì hệ thống chạy suy giảm


FLOW_TREE: Tuple[FlowEdge, ...] = (
    FlowEdge("connect.camera", "signal", "RawPacket"),
    FlowEdge("connect.esp32", "signal", "RawPacket", optional=True),
    FlowEdge("signal", "process.sampler", "SignalPacket"),
    FlowEdge("process.sampler", "process.baseline", "SignalPacket"),
    FlowEdge("process.sampler", "process.ai", "SignalPacket"),
    FlowEdge("process.ai", "process.rules", "DetectionResult"),
    FlowEdge("process.rules", "response", "ViolationEvent"),
)


# ---------- Gói dữ liệu ----------
@dataclass(frozen=True)
class PacketBase:
    trace_id: str   # bắt buộc, không được rỗng (R5)
    ts_ms: int
    source: NodeId


@dataclass(frozen=True)
class RawPacket(PacketBase):
    payload: bytes
    sensor_kind: Literal["camera", "esp32"]


@dataclass(frozen=True)
class SignalPacket(PacketBase):
    frame_id: int                          # bắt buộc (bản nháp để tùy chọn, đổi lại vì runAi cần)
    width: int
    height: int
    driver_id: Optional[str] = None
    sensor: Optional[Dict[str, float]] = None


@dataclass(frozen=True)
class Detection:
    label: str
    conf: float                            # phải nằm trong [0, 1]
    box: Optional[Tuple[int, int, int, int]] = None


@dataclass(frozen=True)
class DetectionResult(PacketBase):
    frame_id: int
    items: Tuple[Detection, ...]


@dataclass(frozen=True)
class ViolationEvent(PacketBase):
    rule: str
    t0_ms: int                             # thời điểm vi phạm bắt đầu (0s)
    pre_ms: int                            # lấy lại phần tiền sự kiện (mặc định 2000 = -2s)
    frame_ids: Tuple[int, ...]


@dataclass(frozen=True)
class ErrorEvent(PacketBase):
    code: ErrorCode
    node_path: str                         # bắt buộc: chỉ đúng node hoặc cạnh lỗi (R5)
    err_class: ErrorClass
    message: str
    recoverable: bool


# ---------- Giao diện AI cắm vào (luật R6) ----------
class AiAdapter(Protocol):
    name: str                              # "stub" | "yolo"

    def load(self) -> None: ...

    def infer(self, frame: SignalPacket) -> DetectionResult: ...

    def health(self) -> Health: ...


# ---------- Giao diện mọi node phải có ----------
class NodeContract(Protocol):
    node_id: NodeId

    def health(self) -> Health: ...

    def self_test(self) -> Tuple[bool, str]: ...
