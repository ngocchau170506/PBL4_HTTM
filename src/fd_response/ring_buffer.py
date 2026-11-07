"""Bộ nhớ vòng khung hình cho timeline vi phạm -2s -> 0s -> kết thúc (FEAT-032 & FEAT-062).

- Lưu trữ (frame_id, ts_ms, image) cố định bộ nhớ theo capacity (cắt tỉa deque tự động).
- Hỗ trợ lưu trữ mảng ảnh thật hoặc thumbnail thu nhỏ (mặc định 224x224) từ Frame Cache Hướng 2.
- Trích xuất cụm ảnh bằng chứng vi phạm (-2.0s đến +1.0s) đóng gói gửi ServerDispatcher.
- Pha vi phạm đi đúng VIOLATION_TRANSITIONS trong contracts/state.py.
- Không import chéo từ các fd_* khác (Luật R2).
"""
from __future__ import annotations

from collections import deque
import sys
import time
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

from contracts.logic import ErrorEvent, Health, NodeId, SignalPacket, ViolationEvent
from contracts.state import VIOLATION_TRANSITIONS, ViolationPhase

try:
    import cv2
except ImportError:
    cv2 = None

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
    """Thực thi NodeContract cho node response (phần timeline và đệm ảnh bằng chứng)."""

    node_id: NodeId = "response"

    def __init__(
        self,
        capacity: int,
        pre_event_ms: int,
        store_images: bool = True,
        image_size: Optional[Tuple[int, int]] = (224, 224),
    ) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity phải là số nguyên >= 1")
        if isinstance(pre_event_ms, bool) or not isinstance(pre_event_ms, int) or pre_event_ms < 0:
            raise ValueError("pre_event_ms phải là số nguyên >= 0")
        self._capacity: int = capacity
        self._pre_ms: int = pre_event_ms
        self._store_images: bool = bool(store_images)
        self._image_size: Optional[Tuple[int, int]] = image_size
        self._frames: Deque[Tuple[int, int, Any]] = deque(maxlen=capacity)
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

    def push(
        self,
        packet: SignalPacket,
        image: Optional[Any] = None,
    ) -> Optional[ErrorEvent]:
        """Thêm 1 khung vào buffer kèm mảng ảnh (nếu có). Trả ErrorEvent nếu khung sai hoặc bị tràn."""
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
            if self._t0_ms is not None and oldest_ts >= self._t0_ms - self._pre_ms:
                self._overrun_count += 1
                self._health = "DEGRADED"
                overrun = _err(
                    packet.trace_id, "E_RESP_BUFFER_OVERRUN", "performance",
                    f"capacity={self._capacity} quá nhỏ: đẩy mất khung ts={oldest_ts} còn thuộc sự kiện",
                    True,
                )

        # Lấy mảng ảnh từ cache dùng chung nếu không truyền trực tiếp
        resolved_img = image
        if resolved_img is None and self._store_images:
            shared = sys.modules.get("_shared_frame_cache")
            if isinstance(shared, dict):
                resolved_img = shared.get(packet.frame_id)

        # Thu nhỏ kích thước ảnh để bảo vệ bộ nhớ nếu cấu hình image_size
        if resolved_img is not None and self._image_size and cv2 is not None and hasattr(resolved_img, "shape"):
            try:
                target_w, target_h = self._image_size
                h, w = resolved_img.shape[:2]
                if (w, h) != (target_w, target_h):
                    resolved_img = cv2.resize(resolved_img, (target_w, target_h))
            except Exception as rz_err:
                # Giữ nguyên ảnh nếu resize gặp lỗi
                _ = rz_err

        self._frames.append((packet.frame_id, packet.ts_ms, resolved_img))
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
        for item in reversed(self._frames):
            fid, ts = item[0], item[1]
            if fid == frame_id:
                return ts
        return None

    def pre_event_frames(self) -> Tuple[int, ...]:
        """frame_id trong [t0 - pre_event_ms, t0]; rỗng nếu chưa có t0."""
        if self._t0_ms is None:
            return ()
        lo = self._t0_ms - self._pre_ms
        return tuple(item[0] for item in self._frames if lo <= item[1] <= self._t0_ms)

    def close_violation(self) -> Tuple[int, ...]:
        """ACTIVE -> CLOSED; trả danh sách frame_id từ t0 - pre_event_ms tới khung mới nhất."""
        self._move("CLOSED")
        assert self._t0_ms is not None
        lo = self._t0_ms - self._pre_ms
        clip = tuple(item[0] for item in self._frames if item[1] >= lo)
        return clip

    def get_clip_images(
        self,
        clip_ids: Optional[Sequence[int]] = None,
    ) -> Tuple[Tuple[int, int, Any], ...]:
        """Trích xuất danh sách các bộ (frame_id, ts_ms, image) thuộc cụm sự kiện vi phạm."""
        if clip_ids is None:
            return tuple(self._frames)
        wanted = set(clip_ids)
        return tuple(item for item in self._frames if item[0] in wanted)

    def extract_evidence_package(self, violation: ViolationEvent) -> Dict[str, Any]:
        """Đóng gói đầy đủ thông tin bằng chứng vi phạm kèm ảnh phục vụ điều phối gửi."""
        clip_tuples = self.get_clip_images(violation.frame_ids)
        frames_summary = []
        for fid, ts, img in clip_tuples:
            frames_summary.append({
                "frame_id": fid,
                "ts_ms": ts,
                "has_image": img is not None,
            })
        return {
            "trace_id": violation.trace_id,
            "rule": violation.rule,
            "t0_ms": violation.t0_ms,
            "pre_ms": violation.pre_ms,
            "frame_ids": list(violation.frame_ids),
            "frame_count": len(violation.frame_ids),
            "frames": frames_summary,
        }

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
