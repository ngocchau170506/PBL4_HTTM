"""Single-owner camera capture with latest-frame delivery and an optional sampler."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import time
import threading
from typing import Callable, Optional

import cv2

from AI.scripts.diagnostic_log import emit_error_event


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
        self._max_fps = float(os.getenv("CAMERA_CAPTURE_FPS", "30.0"))
        self._target_w = int(os.getenv("CAMERA_WIDTH", "640"))
        self._target_h = int(os.getenv("CAMERA_HEIGHT", "480"))
        self.error = None
        self._stop = threading.Event()
        self._condition = threading.Condition()
        self._latest = None
        self._latest_id = 0
        self._closed = False
        self._thread = threading.Thread(target=self._capture_loop, name="camera-capture", daemon=True)
        self._thread.start()

    def set_max_fps(self, fps: float) -> None:
        """Đồng bộ tần số giải mã BGR với nhịp mục tiêu."""
        self._max_fps = max(1.0, float(fps))

    def read_latest(
        self,
        after_frame_id: int = 0,
        timeout: float = 1.0,
        allow_repeat: bool = False,
    ):
        with self._condition:
            if allow_repeat and self._latest is not None:
                if self._latest_id <= after_frame_id and not self._closed:
                    self._condition.wait_for(
                        lambda: self._closed or self._latest_id > after_frame_id,
                        timeout=min(timeout, 0.003),
                    )
                if self._closed or self._latest is None:
                    return False, None, after_frame_id, None, None
                frame, frame_id, timestamp_ns, capture_time_utc = self._latest
                if frame_id <= after_frame_id:
                    timestamp_ns = time.monotonic_ns()
                    capture_time_utc = datetime.now(timezone.utc).isoformat()
                return True, frame, frame_id, timestamp_ns, capture_time_utc

            ready = self._condition.wait_for(
                lambda: self._closed or self._latest_id > after_frame_id,
                timeout=timeout,
            )
            if not ready or self._latest is None or self._latest_id <= after_frame_id:
                return False, None, after_frame_id, None, None
            frame, frame_id, timestamp_ns, capture_time_utc = self._latest
            # Không copy: khung hình trong _latest là bất biến (luồng capture luôn tạo mảng mới),
            # tiết kiệm ~0.9MB memcpy mỗi nhịp.
            return True, frame, frame_id, timestamp_ns, capture_time_utc

    def close(self) -> None:
        self._stop.set()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=2.0)
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass

    def _capture_loop(self) -> None:
        last_decode_ts = 0.0

        while True:
            min_decode_interval = 1.0 / max(1.0, self._max_fps)
            with self._condition:
                if self._closed:
                    return

            if self._capture is None:
                self.error = "WAITING_FOR_CAMERA"
                if self._capture_factory is not None:
                    if self._stop.wait(1.5):
                        return
                    try:
                        self._capture = self._capture_factory()
                    except Exception:
                        self._capture = None
                    continue
                else:
                    if self._stop.wait(1.5):
                        return
                    continue

            try:
                now_mono = time.monotonic()
                if self._max_fps < 25.0 and (now_mono - last_decode_ts) < min_decode_interval:
                    ok_grab = self._capture.grab()
                    if not ok_grab:
                        ok, frame = False, None
                    else:
                        time.sleep(0.005)
                        continue
                else:
                    ok, frame = self._capture.read()
                    last_decode_ts = now_mono
            except Exception:
                ok, frame = False, None

            if not ok or frame is None:
                if self.error != "CAMERA_DISCONNECTED":
                    emit_error_event(
                        code="E_CONN_LOST",
                        source="connect.camera",
                        node_path="connect.camera.capture_loop",
                        err_class="source",
                        message="Mat tin hieu khung hinh tu camera ngoai (RTSP/USB); dang thu ket noi lai",
                        recoverable=True,
                    )
                self.error = "CAMERA_DISCONNECTED"
                if self._capture_factory is not None:
                    try:
                        self._capture.release()
                    except Exception:
                        pass
                    self._capture = None
                    if self._stop.wait(1.5):
                        return
                    try:
                        self._capture = self._capture_factory()
                    except Exception:
                        self._capture = None
                    continue
                with self._condition:
                    self._closed = True
                    self._condition.notify_all()
                return

            # Hạ độ phân giải ngay tại nguồn nếu Camera RTSP gửi luồng Full HD 1920x1080
            # (vì CAP_FFMPEG bỏ qua CAP_PROP_FRAME_WIDTH trên RTSP), giúp giảm 75% tải CPU & RAM trên Pi
            h, w = frame.shape[:2]
            if w > self._target_w or h > self._target_h:
                frame = cv2.resize(frame, (self._target_w, self._target_h), interpolation=cv2.INTER_AREA)

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
