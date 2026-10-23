"""Bounded, timestamp-based frame sampling for temporal CNN events."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
import time
from typing import Any, Optional

import cv2
import numpy as np


@dataclass
class CnnFrame:
    frame_id: int
    capture_timestamp_ns: int
    capture_time_utc: str
    image: np.ndarray
    face_valid: Optional[bool] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CnnFrameBuffer:
    """Thread-safe bounded sampler. Images are stored in OpenCV BGR format."""

    def __init__(
        self,
        buffer_seconds: float = 5.0,
        sample_fps: float = 5.0,
        image_size: tuple[int, int] = (224, 224),
        max_frames: Optional[int] = None,
    ):
        if buffer_seconds <= 0 or sample_fps <= 0:
            raise ValueError("buffer_seconds and sample_fps must be positive")
        self.buffer_seconds = float(buffer_seconds)
        self.sample_fps = float(sample_fps)
        self.image_size = tuple(image_size)
        self.max_frames = max_frames or max(1, int(buffer_seconds * sample_fps) + 2)
        self._frames: deque[CnnFrame] = deque(maxlen=self.max_frames)
        self._next_sample_ns: Optional[int] = None
        self._last_timestamp_ns: Optional[int] = None
        self._last_frame_id: Optional[int] = None
        self._lock = threading.RLock()

    @property
    def sample_interval_ns(self) -> int:
        return int(1_000_000_000 / self.sample_fps)

    def append(
        self,
        frame_id: int,
        capture_timestamp_ns: int,
        image: np.ndarray,
        capture_time_utc: str,
        face_valid: Optional[bool] = None,
        metadata: Optional[dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[CnnFrame]:
        if capture_timestamp_ns < 0:
            raise ValueError("capture_timestamp_ns must be non-negative")
        if image is None or not isinstance(image, np.ndarray) or image.size == 0:
            raise ValueError("image must be a non-empty numpy array")
        with self._lock:
            if (
                self._last_timestamp_ns is not None
                and capture_timestamp_ns <= self._last_timestamp_ns
            ):
                return None
            if self._last_frame_id is not None and frame_id <= self._last_frame_id:
                return None
            self._last_timestamp_ns = capture_timestamp_ns
            self._last_frame_id = frame_id
            self._purge_locked(capture_timestamp_ns)
            if (
                not force
                and self._next_sample_ns is not None
                and capture_timestamp_ns < self._next_sample_ns
            ):
                return None
            self._next_sample_ns = capture_timestamp_ns + self.sample_interval_ns
            sampled = cv2.resize(image, self.image_size, interpolation=cv2.INTER_AREA)
            item = CnnFrame(
                frame_id=int(frame_id),
                capture_timestamp_ns=int(capture_timestamp_ns),
                capture_time_utc=str(capture_time_utc),
                image=sampled.copy(),
                face_valid=face_valid,
                metadata=dict(metadata or {}),
            )
            self._frames.append(item)
            self._purge_locked(capture_timestamp_ns)
            return item

    def snapshot(self, end_timestamp_ns: Optional[int] = None) -> list[CnnFrame]:
        with self._lock:
            end = end_timestamp_ns if end_timestamp_ns is not None else time.monotonic_ns()
            self._purge_locked(end)
            return list(self._frames)

    def before(self, timestamp_ns: int, seconds: float) -> list[CnnFrame]:
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        lower = timestamp_ns - int(seconds * 1_000_000_000)
        with self._lock:
            self._purge_locked(timestamp_ns)
            return [
                frame for frame in self._frames
                if lower <= frame.capture_timestamp_ns <= timestamp_ns
            ]

    def __len__(self) -> int:
        with self._lock:
            return len(self._frames)

    def get(self, frame_id: int) -> Optional[CnnFrame]:
        with self._lock:
            for frame in self._frames:
                if frame.frame_id == frame_id:
                    return frame
        return None

    def _purge_locked(self, newest_timestamp_ns: int) -> None:
        cutoff = newest_timestamp_ns - int(self.buffer_seconds * 1_000_000_000)
        while self._frames and self._frames[0].capture_timestamp_ns < cutoff:
            self._frames.popleft()
