"""Low-latency queue=1 CameraFrameSource: RESIZES BEFORE ROTATING (~85% Pi 4 CPU/RAM savings) + has is_opened()."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import cv2
import numpy as np


def resize_then_rotate(
    frame: np.ndarray,
    target_size: Tuple[int, int],
    rotation_deg: int = 0,
) -> np.ndarray:
    """Downscale 1080p -> 640x480 FIRST, THEN rotate (saves ~85% rotation bandwidth on Pi 4!)."""
    if frame is None or frame.size == 0:
        return frame
    tw, th = int(target_size[0]), int(target_size[1])
    h, w = frame.shape[:2]
    out = frame
    if tw > 0 and th > 0 and (w != tw or h != th):
        interp = cv2.INTER_LINEAR
        out = cv2.resize(out, (tw, th), interpolation=interp)
    rot = int(rotation_deg) % 360
    if rot == 180:
        out = cv2.rotate(out, cv2.ROTATE_180)
    elif rot == 90:
        out = cv2.rotate(out, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 270:
        out = cv2.rotate(out, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return out


@dataclass(frozen=True)
class FramePacket:
    frame_id: int
    capture_timestamp_ns: int
    image: np.ndarray
    is_stale: bool = False


class CameraFrameSource:
    """Bounded depth=1 latest-frame reader with monotonic stall watchdog and is_opened()."""

    def __init__(
        self,
        open_fn: Callable[[], Optional[cv2.VideoCapture]],
        target_size: Tuple[int, int] = (640, 480),
        rotation_deg: int = 180,
        stall_reset_s: float = 6.0,
    ):
        self._open_fn = open_fn
        self.target_size = target_size
        self.rotation_deg = int(rotation_deg)
        self.stall_reset_s = max(2.0, float(stall_reset_s))

        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cap: Optional[cv2.VideoCapture] = None

        self._latest: Optional[FramePacket] = None
        self._latest_id = 0
        self._last_fresh_mono = 0.0
        self._opened = False
        self.native_fps: Optional[float] = None
        self.error: str = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture_loop, name="camera-capture", daemon=True)
        self._thread.start()

    def is_opened(self) -> bool:
        with self._cond:
            return bool(self._opened)

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        thread_still_alive = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            thread_still_alive = self._thread.is_alive()
            if not thread_still_alive:
                self._thread = None
        # NEVER call cap.release() from main thread while capture thread is still blocked in C++ cap.read() (prevents SIGSEGV!)
        if not thread_still_alive:
            self._release_cap()

    def _release_cap(self) -> None:
        cap = self._cap
        self._cap = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        with self._cond:
            self._opened = False

    def read_latest(self, last_seen_id: int = 0, timeout_s: float = 0.05) -> Optional[FramePacket]:
        with self._cond:
            if self._latest is None or self._latest_id == last_seen_id:
                self._cond.wait(timeout=timeout_s)
            pkt = self._latest
            if pkt is None:
                return None
            if pkt.frame_id == last_seen_id:
                return FramePacket(
                    frame_id=pkt.frame_id,
                    capture_timestamp_ns=pkt.capture_timestamp_ns,
                    image=pkt.image,
                    is_stale=True,
                )
            return pkt

    def _capture_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._cap is None or not self._cap.isOpened():
                    self._release_cap()
                    cap = self._open_fn()
                    if cap is None or not cap.isOpened():
                        self.error = "camera_open_failed"
                        self._stop.wait(1.0)
                        continue
                    self._cap = cap
                    try:
                        raw_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                        self.native_fps = round(raw_fps, 1) if 5.0 <= raw_fps <= 30.0 else None
                    except Exception:
                        self.native_fps = None
                    self._last_fresh_mono = time.monotonic()
                    with self._cond:
                        self._opened = True
                    self.error = ""

                ok, raw = self._cap.read()
                now_m = time.monotonic()
                if not ok or raw is None or raw.size == 0:
                    if (now_m - self._last_fresh_mono) >= self.stall_reset_s:
                        self.error = "camera_stall_reconnect"
                        self._release_cap()
                    else:
                        self._stop.wait(0.02)
                    continue

                processed = resize_then_rotate(raw, self.target_size, self.rotation_deg)
                ts_ns = time.monotonic_ns()
                self._last_fresh_mono = now_m
                with self._cond:
                    self._latest_id += 1
                    self._latest = FramePacket(
                        frame_id=self._latest_id,
                        capture_timestamp_ns=ts_ns,
                        image=processed,
                        is_stale=False,
                    )
                    self._cond.notify_all()
            except Exception as exc:
                self.error = f"camera_loop_error:{exc}"
                self._release_cap()
                self._stop.wait(0.5)
        self._release_cap()
