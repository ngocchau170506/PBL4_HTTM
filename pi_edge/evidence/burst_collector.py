"""Lightweight 10-Second JPEG Keyframe Window Collector (LCK-02: ZERO software MP4 encoding on Pi 4!).

Restores the original 10-second continuous violation evidence window (-3s pre-event -> 0s trigger -> +7s post-event)
while keeping CPU usage minimal on Raspberry Pi 4:
  - Ring buffer retains the latest 4.0s of clean raw frames (NO HUD overlays) at 4.0 FPS (640x360).
  - On Tier-1 Edge Reflex trigger:
      1. ESP32 speaker alerts IMMEDIATELY (<5ms, non-blocking).
      2. Freezes the pre-event frames (-3.0s -> 0s) and continues collecting post-event frames (+7.0s)
         up to a full 10.0s continuous window (~40 keyframes @ 4.0 FPS, ~800 KB total).
      3. Once the 10.0s window finishes, encodes clean JPEGs and returns one deduplicated burst for
         Server of AI Tier-2 Deep CNN verification & browser-playable WebM/H.264 stitching.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


@dataclass(frozen=True)
class RawRingFrame:
    frame_id: int
    ts_mono: float
    image: np.ndarray


@dataclass
class _ActiveWindow:
    meta: Dict[str, Any]
    started_mono: float
    end_mono: float
    frames: List[RawRingFrame] = field(default_factory=list)
    last_frame_id: int = -1


class BurstCollector:
    def __init__(
        self,
        ring_seconds: float = 4.0,
        sample_fps: float = 4.0,
        target_size: Tuple[int, int] = (640, 360),
        max_burst_frames: int = 44,
        jpeg_quality: int = 78,
        pre_event_s: float = 3.0,
        post_event_s: float = 7.0,
    ):
        self.ring_seconds = max(1.0, float(ring_seconds))
        self.sample_fps = max(1.0, float(sample_fps))
        self.sample_interval_s = 1.0 / self.sample_fps
        self.target_size = (int(target_size[0]), int(target_size[1]))
        self.max_burst_frames = max(4, min(55, int(max_burst_frames)))
        self.jpeg_quality = max(40, min(92, int(jpeg_quality)))
        self.pre_event_s = max(0.5, float(pre_event_s))
        self.post_event_s = max(1.0, float(post_event_s))

        max_len = max(16, int((self.ring_seconds + 1.0) * self.sample_fps) + 4)
        self._ring: deque[RawRingFrame] = deque(maxlen=max_len)
        self._active: Optional[_ActiveWindow] = None
        self._lock = threading.Lock()
        self._last_sample_mono = 0.0

    @property
    def has_active_window(self) -> bool:
        with self._lock:
            return self._active is not None

    def push_clean_frame(self, frame_id: int, image_bgr: np.ndarray, now_mono: Optional[float] = None) -> bool:
        """Sample clean raw frame at sample_fps without burning HUD overlays; also feeds any active 10s window."""
        if image_bgr is None or image_bgr.size == 0:
            return False
        now_m = now_mono if now_mono is not None else time.monotonic()
        with self._lock:
            if (now_m - self._last_sample_mono) < self.sample_interval_s:
                return False
            self._last_sample_mono = now_m
            resized = cv2.resize(image_bgr, self.target_size, interpolation=cv2.INTER_LINEAR)
            rf = RawRingFrame(frame_id=int(frame_id), ts_mono=now_m, image=resized)
            self._ring.append(rf)
            if self._active is not None and rf.frame_id != self._active.last_frame_id:
                if len(self._active.frames) < self.max_burst_frames:
                    self._active.frames.append(rf)
                    self._active.last_frame_id = rf.frame_id
            return True

    def start_window(
        self,
        meta: Dict[str, Any],
        now_mono: Optional[float] = None,
        fallback_frame: Optional[np.ndarray] = None,
    ) -> bool:
        """Start a 10-second continuous evidence window (-pre_event_s -> +post_event_s) if none is active."""
        now_m = now_mono if now_mono is not None else time.monotonic()
        with self._lock:
            if self._active is not None:
                return False
            cutoff = now_m - self.pre_event_s
            pre_frames = [rf for rf in self._ring if rf.ts_mono >= cutoff]
            if not pre_frames and self._ring:
                pre_frames = [self._ring[-1]]
            if not pre_frames and fallback_frame is not None and fallback_frame.size > 0:
                resized = cv2.resize(fallback_frame, self.target_size, interpolation=cv2.INTER_LINEAR)
                pre_frames = [RawRingFrame(frame_id=0, ts_mono=now_m, image=resized)]
            last_id = pre_frames[-1].frame_id if pre_frames else -1
            self._active = _ActiveWindow(
                meta=dict(meta),
                started_mono=now_m,
                end_mono=now_m + self.post_event_s,
                frames=list(pre_frames),
                last_frame_id=last_id,
            )
            return True

    def _encode_frames(self, frames: List[RawRingFrame]) -> List[Tuple[str, bytes]]:
        if not frames:
            return []
        if len(frames) > self.max_burst_frames:
            step = len(frames) / float(self.max_burst_frames)
            frames = [frames[min(len(frames) - 1, int(i * step))] for i in range(self.max_burst_frames)]
        out: List[Tuple[str, bytes]] = []
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        for idx, rf in enumerate(frames):
            ok, enc = cv2.imencode(".jpg", rf.image, params)
            if ok:
                out.append((f"k{idx:03d}_f{rf.frame_id}.jpg", enc.tobytes()))
        return out

    def poll_completed_window(
        self,
        now_mono: Optional[float] = None,
        force_flush: bool = False,
    ) -> Optional[Tuple[Dict[str, Any], List[Tuple[str, bytes]], float]]:
        """Return (meta, jpeg_frames, effective_fps) once the 10-second window completes."""
        now_m = now_mono if now_mono is not None else time.monotonic()
        win: Optional[_ActiveWindow] = None
        with self._lock:
            if self._active is None:
                return None
            if force_flush or now_m >= self._active.end_mono or len(self._active.frames) >= self.max_burst_frames:
                win = self._active
                self._active = None
        if win is None:
            return None
        jpegs = self._encode_frames(win.frames)
        # Compute real effective FPS across the captured span so stitched video plays at 1x real time!
        if len(win.frames) >= 2:
            span = max(0.5, win.frames[-1].ts_mono - win.frames[0].ts_mono)
            eff_fps = max(1.0, min(self.sample_fps + 1.0, (len(jpegs) - 1) / span))
        else:
            eff_fps = self.sample_fps
        return win.meta, jpegs, round(eff_fps, 2)

    def extract_jpeg_burst(self, fallback_frame: Optional[np.ndarray] = None) -> List[Tuple[str, bytes]]:
        """Immediate snapshot burst from ring buffer (retained for backward compatibility & unit tests)."""
        with self._lock:
            frames = list(self._ring)
        if not frames and fallback_frame is not None and fallback_frame.size > 0:
            resized = cv2.resize(fallback_frame, self.target_size, interpolation=cv2.INTER_LINEAR)
            frames = [RawRingFrame(frame_id=0, ts_mono=time.monotonic(), image=resized)]
        return self._encode_frames(frames)
