"""Bộ nhớ vòng khung hình cho timeline vi phạm -2s -> 0s -> kết thúc (FEAT-032).

- Chỉ lưu (frame_id, ts_ms), không giữ ảnh thô, nên RAM cố định theo capacity.
- Pha vi phạm đi đúng VIOLATION_TRANSITIONS trong contracts/state.py.
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional, Tuple

from contracts.logic import ErrorEvent, Health, NodeId, SignalPacket
from contracts.state import VIOLATION_TRANSITIONS, ViolationPhase

_NODE_PATH = "response.ring_buffer"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _err(trace_id: str, code, err_class, message: str, recoverable: bool) -> ErrorEvent:
    return ErrorEvent(
        trace_id=trace_id or "NO_TRACE",
        ts_ms=_now_ms(),
        source="response",
        code=code,
        node_path=_NODE_PATH,
        err_class=err_class,
        message=message,
        recoverable=recoverable,
    )


class FrameRingBuffer:
    """Thực thi NodeContract cho node response (phần timeline)."""

    node_id: NodeId = "response"

    def __init__(self, capacity: int, pre_event_ms: int) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity phải là số nguyên >= 1")
        if isinstance(pre_event_ms, bool) or not isinstance(pre_event_ms, int) or pre_event_ms < 0:
            raise ValueError("pre_event_ms phải là số nguyên >= 0")
        self._capacity: int = capacity
        self._pre_ms: int = pre_event_ms
        self._frames: Deque[Tuple[int, int]] = deque(maxlen=capacity)
        self._phase: ViolationPhase = "WATCHING"
        self._t0_ms: Optional[int] = None
        self._health: Health = "OK"
        self._overrun_count: int = 0

    @property
    def phase(self) -> ViolationPhase:
        return self._phase

    @property
    def size(self) -> int:
        return len(self._frames)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def t0_ms(self) -> Optional[int]:
        return self._t0_ms

    @property
    def overrun_count(self) -> int:
        return self._overrun_count

    def health(self) -> Health:
        return self._health

    def push(self, packet: SignalPacket) -> Optional[ErrorEvent]:
        """Thêm 1 khung vào buffer. Trả ErrorEvent nếu khung sai hoặc làm mất khung còn cần giữ."""
        if not isinstance(packet, SignalPacket):
            self._health = "DEGRADED"
            return _err("NO_TRACE", "E_SIG_BAD_FORMAT", "contract",
                        "Đầu vào ring buffer không phải SignalPacket", True)
        if not packet.trace_id:
            self._health = "DEGRADED"
            return _err("NO_TRACE", "E_SIG_BAD_FORMAT", "contract",
                        "SignalPacket thiếu trace_id", True)
        if isinstance(packet.frame_id, bool) or not isinstance(packet.frame_id, int) or packet.frame_id <= 0:
            self._health = "DEGRADED"
            return _err(packet.trace_id, "E_SIG_BAD_FORMAT", "contract",
                        "SignalPacket thiếu hoặc sai frame_id", True)
        if self._frames and packet.ts_ms < self._frames[-1][1]:
            self._health = "DEGRADED"
            return _err(packet.trace_id, "E_SIG_TS_DRIFT", "data",
                        f"ts_ms lùi: {packet.ts_ms} < {self._frames[-1][1]}", True)

        overrun: Optional[ErrorEvent] = None
        if len(self._frames) == self._capacity and self._phase == "ACTIVE":
            oldest_ts = self._frames[0][1]
            assert self._t0_ms is not None
            if oldest_ts >= self._t0_ms - self._pre_ms:
                self._overrun_count += 1
                self._health = "DEGRADED"
                overrun = _err(
                    packet.trace_id, "E_RESP_BUFFER_OVERRUN", "performance",
                    f"capacity={self._capacity} quá nhỏ: đẩy mất khung ts={oldest_ts} còn thuộc sự kiện",
                    True,
                )
        self._frames.append((packet.frame_id, packet.ts_ms))
        if overrun is None and self._health == "DEGRADED" and self._phase != "ACTIVE":
            self._health = "OK"
        return overrun

    def _move(self, new_phase: ViolationPhase) -> None:
        allowed = VIOLATION_TRANSITIONS.get(self._phase, [])
        if new_phase not in allowed:
            raise ValueError(f"Chuyển pha không hợp lệ: {self._phase} -> {new_phase}")
        self._phase = new_phase

    def mark_suspect(self) -> None:
        """WATCHING -> SUSPECT: có dấu hiệu, chưa xác nhận."""
        self._move("SUSPECT")

    def clear_suspect(self) -> None:
        """SUSPECT -> WATCHING: dấu hiệu giả."""
        self._move("WATCHING")

    def begin_violation(self, t0_ms: int) -> None:
        """SUSPECT -> ACTIVE tại t0 (0s). Khung từ t0 - pre_event_ms được giữ làm tiền sự kiện."""
        if isinstance(t0_ms, bool) or not isinstance(t0_ms, int):
            raise ValueError("t0_ms phải là số nguyên")
        self._move("ACTIVE")
        self._t0_ms = t0_ms

    def ts_of(self, frame_id: int) -> Optional[int]:
        """ts_ms của khung còn nằm trong buffer; None nếu khung đã bị đẩy ra hoặc chưa từng vào."""
        for fid, ts in reversed(self._frames):
            if fid == frame_id:
                return ts
        return None

    def pre_event_frames(self) -> Tuple[int, ...]:
        """frame_id trong [t0 - pre_event_ms, t0]; rỗng nếu chưa có t0."""
        if self._t0_ms is None:
            return ()
        lo = self._t0_ms - self._pre_ms
        return tuple(fid for fid, ts in self._frames if lo <= ts <= self._t0_ms)

    def close_violation(self) -> Tuple[int, ...]:
        """ACTIVE -> CLOSED; trả frame_id từ t0 - pre_event_ms tới khung mới nhất."""
        self._move("CLOSED")
        assert self._t0_ms is not None
        lo = self._t0_ms - self._pre_ms
        clip = tuple(fid for fid, ts in self._frames if ts >= lo)
        return clip

    def reset(self) -> None:
        """CLOSED -> WATCHING, xóa t0 để sẵn sàng cho sự kiện mới. Buffer khung vẫn chạy tiếp."""
        self._move("WATCHING")
        self._t0_ms = None
        if self._health == "DEGRADED":
            self._health = "OK"

    def self_test(self) -> Tuple[bool, str]:
        probe = FrameRingBuffer(capacity=4, pre_event_ms=100)
        for i in range(1, 5):
            err = probe.push(SignalPacket(
                trace_id=f"rb-selftest-{i}", ts_ms=i * 50, source="signal",
                frame_id=i, width=640, height=480,
            ))
            if err is not None:
                return False, f"{err.code}: {err.message}"
        probe.mark_suspect()
        probe.begin_violation(200)
        clip = probe.close_violation()
        if clip != (2, 3, 4):
            return False, f"clip sai: {clip}"
        return True, f"FrameRingBuffer OK (capacity={self._capacity}, pre_event_ms={self._pre_ms})"
