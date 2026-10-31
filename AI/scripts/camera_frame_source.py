"""Single-owner camera capture with latest-frame delivery and an optional sampler."""

from __future__ import annotations

from datetime import datetime, timezone
import time
import threading
from typing import Callable, Optional

import cv2


class CameraFrameSource:
    """Capture frames once and expose only the newest frame to inference."""

    def __init__(
        self,
        capture: cv2.VideoCapture,
        on_frame: Optional[Callable[..., None]] = None,
        capture_factory=None,
    ):
        self._capture = capture
        self._on_frame = on_frame
        self._capture_factory = capture_factory
        self.error = None
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._latest = None
        self._latest_id = 0
        self._closed = False
        self._thread = threading.Thread(target=self._capture_loop, name="camera-capture", daemon=True)
        self._thread.start()

    def read_latest(self, after_frame_id: int = 0, timeout: float = 1.0):
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._closed or self._latest_id > after_frame_id,
                timeout=timeout,
            )
            if not ready or self._latest is None or self._latest_id <= after_frame_id:
                return False, None, after_frame_id, None, None
            frame, frame_id, timestamp_ns, capture_time_utc = self._latest
            return True, frame.copy(), frame_id, timestamp_ns, capture_time_utc

    def close(self) -> None:
        self._stop.set()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=2.0)
        self._capture.release()

    def _capture_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
            ok, frame = self._capture.read()
            if not ok:
                self.error = "CAMERA_DISCONNECTED"
                if self._capture_factory is not None:
                    self._capture.release()
                    if self._stop.wait(1.0):
                        return
                    self._capture = self._capture_factory()
                    continue
                with self._condition:
                    self._closed = True
                    self._condition.notify_all()
                return
            frame_id = self._latest_id + 1
            self.error = None
            timestamp_ns = time.monotonic_ns()
            capture_time_utc = datetime.now(timezone.utc).isoformat()
            if self._on_frame is not None:
                try:
                    self._on_frame(
                        frame,
                        frame_id=frame_id,
                        capture_timestamp_ns=timestamp_ns,
                        capture_time_utc=capture_time_utc,
                    )
                except Exception as exc:
                    self.error = f"SAMPLER_ERROR: {exc}"
            with self._condition:
                if self._closed:
                    return
                self._latest = (frame, frame_id, timestamp_ns, capture_time_utc)
                self._latest_id = frame_id
                self._condition.notify_all()
