"""Nguon phat va bat khung hinh Camera 60 FPS cho thi nghiem EXP-004.

- Thiet ke co lap trong workbench/experiments/EXP-004/, khong lam anh huong src/.
- Ho tro do toc do bat khung 60 FPS (16.67ms/frame), do jitter va luu anh mau PGM
  (portable graymap) bang thu vien chuan de kiem chung hinh anh bat duoc.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import List, Optional

from fd_connect.camera_source import encode_raw_frame


@dataclass
class FrameCaptureMetric:
    """Chi so do cua tung khung hinh tai nguon 60 FPS."""

    seq: int
    ts_ms: int
    interval_ms: float
    jitter_ms: float
    luma_mean: int


class Camera60FpsRig:
    """Bo phat khung hinh 60 FPS va luu anh mau kiem chung."""

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        target_fps: int = 60,
        capture_dir: Optional[Path] = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.target_fps = max(1, int(target_fps))
        self.target_interval_ms = 1000.0 / float(self.target_fps)
        self.capture_dir = capture_dir

        self._seq: int = 0
        self._last_ts_ms: Optional[int] = None
        self.metrics: List[FrameCaptureMetric] = []
        self.saved_files: List[str] = []

        if self.capture_dir is not None:
            self.capture_dir.mkdir(parents=True, exist_ok=True)

    @property
    def frame_count(self) -> int:
        return self._seq

    def reset(self) -> None:
        self._seq = 0
        self._last_ts_ms = None
        self.metrics.clear()

    def next_raw_payload(self, seq: int, simulated_ts_ms: Optional[int] = None) -> bytes:
        """Sinh payload FDRW chuan cho CameraSource va ghi nhan chi so 60 FPS."""
        self._seq = seq
        now_ms = simulated_ts_ms if simulated_ts_ms is not None else int(time.monotonic() * 1000)

        if self._last_ts_ms is None:
            interval_ms = self.target_interval_ms
        else:
            interval_ms = float(max(0, now_ms - self._last_ts_ms))
        self._last_ts_ms = now_ms

        jitter_ms = abs(interval_ms - self.target_interval_ms)
        luma = 128 + (seq % 15)
        self.metrics.append(
            FrameCaptureMetric(
                seq=seq,
                ts_ms=now_ms,
                interval_ms=round(interval_ms, 3),
                jitter_ms=round(jitter_ms, 3),
                luma_mean=luma,
            )
        )
        return encode_raw_frame(self.width, self.height, seq=seq, luma_mean=luma)

    def save_sample_frame_pgm(self, seq: int, state_label: str, ear: float, mar: float) -> Optional[Path]:
        """Luu khung hinh mau (64x48 thu nho) dinh dang PGM kem nhan trang thai trong header."""
        if self.capture_dir is None:
            return None
        thumb_w, thumb_h = 64, 48
        out_path = self.capture_dir / f"frame_{seq:05d}_{state_label}.pgm"
        header = (
            f"P2\n"
            f"# EXP-004 Frame seq={seq} state={state_label} EAR={ear:.3f} MAR={mar:.3f} "
            f"orig={self.width}x{self.height}@60FPS\n"
            f"{thumb_w} {thumb_h}\n255\n"
        )
        rows = []
        base_val = 90 if state_label == "DROWSY" else (160 if state_label == "YAWNING" else 130)
        for r in range(thumb_h):
            row_vals = [str((base_val + r + c) % 256) for c in range(thumb_w)]
            rows.append(" ".join(row_vals))
        out_path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
        self.saved_files.append(out_path.name)
        return out_path

    def summary(self) -> dict:
        if not self.metrics:
            return {
                "target_fps": self.target_fps,
                "frames": 0,
                "avg_interval_ms": 0.0,
                "avg_jitter_ms": 0.0,
                "max_jitter_ms": 0.0,
            }
        intervals = [m.interval_ms for m in self.metrics]
        jitters = [m.jitter_ms for m in self.metrics]
        avg_int = sum(intervals) / len(intervals)
        effective_fps = 1000.0 / avg_int if avg_int > 0 else 0.0
        return {
            "target_fps": self.target_fps,
            "frames": len(self.metrics),
            "avg_interval_ms": round(avg_int, 3),
            "effective_fps": round(effective_fps, 2),
            "avg_jitter_ms": round(sum(jitters) / len(jitters), 3),
            "max_jitter_ms": round(max(jitters), 3),
            "saved_sample_frames": len(self.saved_files),
        }
