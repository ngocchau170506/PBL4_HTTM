"""Kho số liệu dùng chung cho web kiểm tra (workbench/web_viewer, thành phần A).

Chỉ dùng thư viện chuẩn, chạy trên PC, không import vào src/. Các thành phần khác ghi vào đây,
trang web chỉ đọc snapshot(). Mọi bộ nhớ đều có giới hạn cứng để chạy lâu không phình RAM.
Dữ liệu sai (health lạ, conf ngoài [0,1], ảnh rỗng/quá lớn) bị từ chối bằng ValueError, không nuốt lỗi.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

VALID_HEALTH = ("OK", "DEGRADED", "DOWN")


def _wall_clock_ms() -> int:
    return int(time.time() * 1000)


class StateHub:
    """Giữ trạng thái mới nhất của luồng để web hiển thị. An toàn khi nhiều luồng ghi/đọc."""

    def __init__(
        self,
        max_errors: int = 50,
        latency_window: int = 500,
        fps_window_ms: int = 2000,
        max_jpeg_bytes: int = 2_000_000,
        clock_ms: Callable[[], int] = _wall_clock_ms,
    ) -> None:
        if max_errors <= 0 or latency_window <= 0 or fps_window_ms <= 0 or max_jpeg_bytes <= 0:
            raise ValueError("các giới hạn của StateHub phải > 0")
        self._lock = threading.Lock()
        self._clock = clock_ms
        self._max_jpeg_bytes = max_jpeg_bytes
        self._fps_window_ms = fps_window_ms
        self._started_ms = clock_ms()

        self._jpeg: Optional[bytes] = None
        self._frame_id: Optional[int] = None
        self._frame_ts_ms: Optional[int] = None
        self._frame_seq = 0

        self._detections: List[Dict[str, Any]] = []
        self._detection_frame_id: Optional[int] = None

        self._arrivals: Deque[int] = deque()
        self._latencies: Deque[float] = deque(maxlen=latency_window)
        self._frames_total = 0

        self._errors: Deque[Dict[str, Any]] = deque(maxlen=max_errors)
        self._errors_total = 0
        self._error_counts: Dict[str, int] = {}

        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._meta: Dict[str, Any] = {}

    def set_frame(self, jpeg: bytes, frame_id: int, ts_ms: int) -> None:
        if not isinstance(jpeg, (bytes, bytearray)) or len(jpeg) == 0:
            raise ValueError("jpeg phải là bytes không rỗng")
        if len(jpeg) > self._max_jpeg_bytes:
            raise ValueError(f"jpeg {len(jpeg)}B vượt giới hạn {self._max_jpeg_bytes}B")
        with self._lock:
            self._jpeg = bytes(jpeg)
            self._frame_id = int(frame_id)
            self._frame_ts_ms = int(ts_ms)
            self._frame_seq += 1

    def get_frame(self) -> Optional[Tuple[bytes, int, int]]:
        """Trả (jpeg, frame_id, seq) mới nhất, hoặc None nếu chưa có ảnh. seq tăng mỗi lần có ảnh mới."""
        with self._lock:
            if self._jpeg is None or self._frame_id is None:
                return None
            return self._jpeg, self._frame_id, self._frame_seq

    def set_detections(self, frame_id: int, items: List[Dict[str, Any]]) -> None:
        clean: List[Dict[str, Any]] = []
        for it in items:
            label = it.get("label")
            conf = it.get("conf")
            box = it.get("box")
            if not isinstance(label, str) or not label:
                raise ValueError(f"detection thiếu label: {it!r}")
            if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
                raise ValueError(f"conf phải trong [0,1]: {it!r}")
            if box is not None:
                if len(box) != 4:
                    raise ValueError(f"box phải có 4 số: {it!r}")
                box = [int(v) for v in box]
            clean.append({"label": label, "conf": round(float(conf), 4), "box": box})
        with self._lock:
            self._detections = clean
            self._detection_frame_id = int(frame_id)

    def record_frame(self) -> None:
        """Gọi mỗi khung xử lý xong để tính FPS thật trong cửa sổ trượt."""
        now = self._clock()
        with self._lock:
            self._arrivals.append(now)
            self._frames_total += 1
            self._trim_arrivals(now)

    def record_latency(self, ms: float) -> None:
        if ms < 0:
            raise ValueError("độ trễ không được âm")
        with self._lock:
            self._latencies.append(float(ms))

    def add_error(self, code: str, node_path: str, trace_id: str, message: str = "", ts_ms: Optional[int] = None) -> None:
        if not code or not node_path or not trace_id:
            raise ValueError("lỗi phải có code, node_path và trace_id (R5)")
        entry = {
            "code": code,
            "node_path": node_path,
            "trace_id": trace_id,
            "message": message,
            "ts_ms": int(ts_ms if ts_ms is not None else self._clock()),
        }
        with self._lock:
            self._errors.append(entry)
            self._errors_total += 1
            self._error_counts[code] = self._error_counts.get(code, 0) + 1

    def set_node_status(self, node_id: str, health: str, queue_depth: Optional[int] = None) -> None:
        if health not in VALID_HEALTH:
            raise ValueError(f"health {health!r} không thuộc {VALID_HEALTH}")
        if not node_id:
            raise ValueError("node_id không được rỗng")
        if queue_depth is not None and queue_depth < 0:
            raise ValueError("queue_depth không được âm")
        with self._lock:
            self._nodes[node_id] = {"health": health, "queue_depth": queue_depth}

    def set_meta(self, key: str, value: Any) -> None:
        if not key:
            raise ValueError("key không được rỗng")
        with self._lock:
            self._meta[key] = value

    def snapshot(self) -> Dict[str, Any]:
        """Bản chụp thuần dict/list/số, dùng thẳng cho json.dumps. Không chứa ảnh."""
        now = self._clock()
        with self._lock:
            self._trim_arrivals(now)
            lat = sorted(self._latencies)
            return {
                "ts_ms": now,
                "uptime_s": round((now - self._started_ms) / 1000.0, 1),
                "frame": {
                    "frame_id": self._frame_id,
                    "ts_ms": self._frame_ts_ms,
                    "has_image": self._jpeg is not None,
                },
                "fps": self._fps_locked(),
                "frames_total": self._frames_total,
                "latency_ms": {
                    "count": len(lat),
                    "avg": round(sum(lat) / len(lat), 3) if lat else None,
                    "p95": round(self._percentile(lat, 95.0), 3) if lat else None,
                    "max": round(lat[-1], 3) if lat else None,
                },
                "detections": {"frame_id": self._detection_frame_id, "items": [dict(d) for d in self._detections]},
                "nodes": {k: dict(v) for k, v in self._nodes.items()},
                "errors": {
                    "total": self._errors_total,
                    "by_code": dict(self._error_counts),
                    "recent": [dict(e) for e in self._errors],
                },
                "meta": dict(self._meta),
            }

    def _trim_arrivals(self, now: int) -> None:
        cutoff = now - self._fps_window_ms
        while self._arrivals and self._arrivals[0] < cutoff:
            self._arrivals.popleft()

    def _fps_locked(self) -> Optional[float]:
        if len(self._arrivals) < 2:
            return None
        span_ms = self._arrivals[-1] - self._arrivals[0]
        if span_ms <= 0:
            return None
        return round((len(self._arrivals) - 1) * 1000.0 / span_ms, 2)

    @staticmethod
    def _percentile(sorted_values: List[float], pct: float) -> float:
        rank = max(1, int(-(-len(sorted_values) * pct // 100)))
        return sorted_values[min(rank, len(sorted_values)) - 1]
