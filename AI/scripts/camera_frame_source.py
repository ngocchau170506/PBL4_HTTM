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
    ):
        self._capture = capture
        self._on_frame = on_frame
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
                with self._condition:
                    self._closed = True
                    self._condition.notify_all()
                return
            frame_id = self._latest_id + 1
            timestamp_ns = time.monotonic_ns()
            capture_time_utc = datetime.now(timezone.utc).isoformat()
            if self._on_frame is not None:
                self._on_frame(
                    frame,
                    frame_id=frame_id,
                    capture_timestamp_ns=timestamp_ns,
                    capture_time_utc=capture_time_utc,
                )
            with self._condition:
                if self._closed:
                    return
                self._latest = (frame, frame_id, timestamp_ns, capture_time_utc)
                self._latest_id = frame_id
                self._condition.notify_all()
