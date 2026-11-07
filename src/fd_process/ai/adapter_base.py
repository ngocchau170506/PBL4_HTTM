"""Cổng chạy AI an toàn: TRƯỚC -> GỌI -> SAU (Luật R6 & DRAFT/logic.ts).

- Mọi lần gọi AI (StubAdapter hay YoloAdapter) đều phải đi qua hàm run_ai.
- Kiểm tra đầu vào, giới hạn thời gian timeout_ms và xác thực đầu ra DetectionResult.
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import time
from typing import Union

from contracts.logic import (
    AiAdapter,
    Detection,
    DetectionResult,
    ErrorClass,
    ErrorCode,
    ErrorEvent,
    SignalPacket,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ai_error(
    frame: SignalPacket,
    adapter_name: str,
    code: ErrorCode,
    err_class: ErrorClass,
    message: str,
    recoverable: bool,
) -> ErrorEvent:
    safe_trace = getattr(frame, "trace_id", "") or "NO_TRACE"
    return ErrorEvent(
        trace_id=safe_trace,
        ts_ms=_now_ms(),
        source="process.ai",
        code=code,
        node_path=f"process.sampler->process.ai({adapter_name})",
        err_class=err_class,
        message=message,
        recoverable=recoverable,
    )


def run_ai(
    adapter: AiAdapter,
    frame: SignalPacket,
    timeout_ms: int,
) -> Union[DetectionResult, ErrorEvent]:
    """Thực thi cổng kiểm soát 3 bước TRƯỚC -> GỌI -> SAU cho mọi AiAdapter."""
    adapter_name = getattr(adapter, "name", "unknown")

    if not isinstance(frame, SignalPacket):
        return ErrorEvent(
            trace_id="NO_TRACE",
            ts_ms=_now_ms(),
            source="process.ai",
            code="E_SIG_BAD_FORMAT",
            node_path=f"process.sampler->process.ai({adapter_name})",
            err_class="contract",
            message="Đầu vào không phải kiểu SignalPacket",
            recoverable=False,
        )

    if not frame.trace_id:
        return _ai_error(
            frame,
            adapter_name,
            "E_SIG_BAD_FORMAT",
            "contract",
            "SignalPacket thiếu trace_id",
            False,
        )

    if isinstance(frame.frame_id, bool) or not isinstance(frame.frame_id, int) or frame.frame_id <= 0:
        return _ai_error(
            frame,
            adapter_name,
            "E_SIG_BAD_FORMAT",
            "contract",
            "SignalPacket thiếu hoặc sai frame_id",
            False,
        )

    if adapter.health() == "DOWN":
        return _ai_error(
            frame,
            adapter_name,
            "E_FLOW_NODE_DOWN",
            "process",
            f"Adapter {adapter_name} đang ở trạng thái DOWN",
            True,
        )

    t0_s = time.monotonic()
    try:
        result = adapter.infer(frame)
    except TimeoutError as exc:
        return _ai_error(
            frame,
            adapter_name,
            "E_PROC_TIMEOUT",
            "performance",
            f"Adapter {adapter_name} quá hạn thời gian: {exc}",
            True,
        )
    except Exception as exc:
        return _ai_error(
            frame,
            adapter_name,
            "E_PROC_BAD_OUTPUT",
            "algorithm",
            f"Adapter {adapter_name} ném ngoại lệ: {exc}",
            True,
        )
    elapsed_ms = (time.monotonic() - t0_s) * 1000.0

    if elapsed_ms > float(timeout_ms):
        return _ai_error(
            frame,
            adapter_name,
            "E_PROC_TIMEOUT",
            "performance",
            f"infer mất {elapsed_ms:.1f}ms vượt ngưỡng {timeout_ms}ms",
            True,
        )

    if not isinstance(result, DetectionResult) or not isinstance(result.items, tuple):
        return _ai_error(
            frame,
            adapter_name,
            "E_PROC_BAD_OUTPUT",
            "contract",
            "Đầu ra không phải DetectionResult hợp lệ",
            True,
        )

    if result.trace_id != frame.trace_id or result.frame_id != frame.frame_id:
        return _ai_error(
            frame,
            adapter_name,
            "E_PROC_BAD_OUTPUT",
            "contract",
            "Kết quả không khớp trace_id hoặc frame_id của SignalPacket đầu vào",
            True,
        )

    for item in result.items:
        if not isinstance(item, Detection) or not (0.0 <= float(item.conf) <= 1.0):
            return _ai_error(
                frame,
                adapter_name,
                "E_PROC_BAD_OUTPUT",
                "algorithm",
                "Độ tự tin conf nằm ngoài đoạn [0, 1]",
                True,
            )

    return result
