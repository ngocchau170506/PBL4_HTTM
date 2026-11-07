"""Cầu nối chẩn đoán chuẩn hóa (PROPOSE-CR-001 Bước 2).

Rút trực tiếp kiểu hợp đồng `ErrorCode`, `ErrorClass`, `ErrorEvent`, `NodeId`
từ `src/contracts/logic.py` sang hệ thống vận hành thật (`AI/scripts/*`), giúp mọi
lỗi phần cứng / kết nối / mô hình trên xe đều có mã `ErrorCode` và `trace_id`
thống nhất thay vì `print()` tự do.
"""
from __future__ import annotations

from collections import deque
import sys
import threading
import time
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

_SRC_DIR = str(Path(__file__).resolve().parents[2] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from contracts.logic import ErrorClass, ErrorCode, ErrorEvent, NodeId  # noqa: E402

_RING_LOCK = threading.Lock()
_RECENT_ERRORS: Deque[ErrorEvent] = deque(maxlen=50)
_ERROR_SEQ = 0


def next_trace_id(prefix: str = "rt") -> str:
    """Sinh mã định danh vết (trace_id) ngắn gọn, không rỗng theo luật R5."""
    global _ERROR_SEQ
    with _RING_LOCK:
        _ERROR_SEQ += 1
        seq = _ERROR_SEQ
    return f"{prefix}-{int(time.time() * 1000)}-{seq}"


def emit_error_event(
    code: ErrorCode,
    source: NodeId,
    node_path: str,
    err_class: ErrorClass,
    message: str,
    recoverable: bool = True,
    trace_id: Optional[str] = None,
    quiet: bool = False,
) -> ErrorEvent:
    """Tạo `ErrorEvent` chuẩn hợp đồng, lưu vào bộ đệm chẩn đoán và in log có cấu trúc."""
    tid = (trace_id or "").strip() or next_trace_id(source.replace(".", "-"))
    ev = ErrorEvent(
        trace_id=tid,
        ts_ms=int(time.time() * 1000),
        source=source,
        code=code,
        node_path=node_path,
        err_class=err_class,
        message=str(message),
        recoverable=bool(recoverable),
    )
    with _RING_LOCK:
        _RECENT_ERRORS.append(ev)
    if not quiet:
        rec_tag = "RECOVERABLE" if ev.recoverable else "FATAL"
        print(
            f"[FD-ERROR][{ev.code}][{ev.node_path}][{rec_tag}][trace={ev.trace_id}] {ev.message}"
        )
    return ev


def get_recent_error_events(limit: int = 20) -> List[Dict[str, Any]]:
    """Trả về danh sách các `ErrorEvent` gần nhất dưới dạng dict (phục vụ Web Dashboard / Telemetry)."""
    with _RING_LOCK:
        items = list(_RECENT_ERRORS)[-max(1, int(limit)) :]
    return [
        {
            "trace_id": e.trace_id,
            "ts_ms": e.ts_ms,
            "source": e.source,
            "code": e.code,
            "node_path": e.node_path,
            "err_class": e.err_class,
            "message": e.message,
            "recoverable": e.recoverable,
        }
        for e in items
    ]
