"""BlazeFace 6-point 1-PASS OpenCV DNN + 3D Yawn & Head-Tilt Geometry.

Fixes the 2x CPU waste in IOTv5 where calibration.py called .forward("classificators")
and .forward("regressors") in two separate full CNN passes. Uses a single multi-output
.forward(["classificators", "regressors"]) pass (with automatic fallback if OpenCV build lacks it).
"""
from __future__ import annotations

import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np


@dataclass(frozen=True)
class HeadPoseEstimate:
    valid: bool
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    face_box: Optional[Tuple[int, int, int, int]] = None
    score: float = 0.0


def evaluate_mouth_yawn_geometry(
    mouth_box: Tuple[int, int, int, int],
    face_box: Optional[Tuple[int, int, int, int]],
    yaw_deg: float = 0.0,
    min_angle_deg: float = 50.0,
    min_ar: float = 0.60,
) -> Tuple[bool, str]:
    """Spatial 3D yawn vs smile/talk filter (coherent angle >= 50 deg <-> ar >= 0.60, fixes F-017)."""
    x1, y1, x2, y2 = mouth_box
    mw = max(1.0, float(x2 - x1))
    mh = max(1.0, float(y2 - y1))
    cos_yaw = max(0.35, math.cos(math.radians(min(70.0, abs(float(yaw_deg))))))
    eff_w = mw / cos_yaw
    ar = mh / eff_w
    angle_deg = math.degrees(math.atan2(mh, eff_w * 0.5))
    face_ratio_ok = True
    if face_box is not None:
        fh = max(1.0, float(face_box[3] - face_box[1]))
        face_ratio_ok = (mh / fh) >= 0.072
    is_yawn = (angle_deg >= min_angle_deg) and (ar >= min_ar) and face_ratio_ok
    label = f"Yawn(ang={angle_deg:.1f},ar={ar:.2f})" if is_yawn else f"Smile/Talk(ang={angle_deg:.1f},ar={ar:.2f})"
    return is_yawn, label


def rotate_roi_replicate(roi: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate head ROI with BORDER_REPLICATE (prevents black triangle 'phone' hallucination)."""
    h, w = roi.shape[:2]
    mat = cv2.getRotationMatrix2D((w * 0.5, h * 0.5), float(angle_deg), 1.0)
    return cv2.warpAffine(roi, mat, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def unrotate_bbox_in_roi(
    box: Tuple[int, int, int, int],
    roi_w: int,
    roi_h: int,
    angle_deg: float,
) -> Tuple[int, int, int, int]:
    """Map a bbox detected inside rotate_roi_replicate(roi, +angle_deg) back into original ROI coordinates."""
    x1, y1, x2, y2 = box
    inv_mat = cv2.getRotationMatrix2D((roi_w * 0.5, roi_h * 0.5), -float(angle_deg), 1.0)
    corners = np.array([[x1, y1, 1.0], [x2, y1, 1.0], [x2, y2, 1.0], [x1, y2, 1.0]], dtype=np.float64)
    mapped = corners @ inv_mat.T
    nx1 = max(0, min(roi_w, int(round(float(np.min(mapped[:, 0]))))))
    ny1 = max(0, min(roi_h, int(round(float(np.min(mapped[:, 1]))))))
    nx2 = max(nx1 + 1, min(roi_w, int(round(float(np.max(mapped[:, 0]))))))
    ny2 = max(ny1 + 1, min(roi_h, int(round(float(np.max(mapped[:, 1]))))))
    return (nx1, ny1, nx2, ny2)


def _build_blazeface_anchors() -> np.ndarray:
    """896 standard MediaPipe BlazeFace short-range (128x128) SSD anchor centers (ax, ay) in [0, 1]."""
    anchors = []
    for grid, per_cell in ((16, 2), (8, 6)):
        for gy in range(grid):
            ay = (gy + 0.5) / grid
            for gx in range(grid):
                ax = (gx + 0.5) / grid
                for _ in range(per_cell):
                    anchors.append((ax, ay))
    return np.asarray(anchors, dtype=np.float32)


_BLAZEFACE_ANCHORS = _build_blazeface_anchors()


class BlazeFaceGeometry:
    """Safe ARM64 Pi 4 BlazeFace 6-point detector extracted from face_landmarker.task (no SIGILL +aes)."""

    def __init__(self, task_path: str):
        self._net = None
        self._single_pass_ok = True
        self.available = False
        self.forward_calls = 0
        p = Path(task_path)
        if not p.exists():
            return
        try:
            with zipfile.ZipFile(p, "r") as zf:
                cand = [n for n in zf.namelist() if n.endswith("face_detector.tflite")]
                if not cand:
                    return
                buf = zf.read(cand[0])
            self._net = cv2.dnn.readNetFromTFLite(bytearray(buf))
            self.available = True
        except Exception:
            self._net = None
            self.available = False

    def detect_pose(self, frame_bgr: np.ndarray) -> HeadPoseEstimate:
        if not self.available or self._net is None or frame_bgr is None or frame_bgr.size == 0:
            return HeadPoseEstimate(valid=False, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)
        h, w = frame_bgr.shape[:2]
        blob = cv2.dnn.blobFromImage(frame_bgr, 1.0 / 127.5, (128, 128), (127.5, 127.5, 127.5), swapRB=True, crop=False)
        self._net.setInput(blob)

        try:
            if self._single_pass_ok:
                # 1-PASS multi-output forward (saves 50% CPU vs calling forward() twice!)
                outs = self._net.forward(["classificators", "regressors"])
                self.forward_calls += 1
                clf, reg = outs[0], outs[1]
            else:
                clf = self._net.forward("classificators")
                reg = self._net.forward("regressors")
                self.forward_calls += 2
        except Exception:
            self._single_pass_ok = False
            try:
                clf = self._net.forward("classificators")
                reg = self._net.forward("regressors")
                self.forward_calls += 2
            except Exception:
                return HeadPoseEstimate(valid=False, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)

        scores = 1.0 / (1.0 + np.exp(-np.clip(clf.reshape(-1), -20.0, 20.0)))
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])
        if best_score < 0.55:
            return HeadPoseEstimate(valid=False, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0, score=best_score)

        reg_flat = reg.reshape(-1, 16)
        if best_idx >= len(reg_flat):
            return HeadPoseEstimate(valid=False, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0, score=best_score)

        r = reg_flat[best_idx]
        # Unscale keypoints from distorted 128x128 blob back to real aspect ratio (fixes 1.78x roll inflation on 16:9!)
        sx = float(w) / 128.0
        sy = float(h) / 128.0
        rx, ry = float(r[4]) * sx, float(r[5]) * sy
        lx, ly = float(r[6]) * sx, float(r[7]) * sy
        nx, ny = float(r[8]) * sx, float(r[9]) * sy
        mx, my = float(r[10]) * sx, float(r[11]) * sy

        dx_eye = lx - rx
        dy_eye = ly - ry
        if abs(dx_eye) < 1e-3:
            roll_deg = 90.0 if dy_eye >= 0 else -90.0
        else:
            roll_deg = math.degrees(math.atan2(dy_eye, dx_eye))
            # Normalize mirrored/inverted horizontal ordering to [-90, 90]
            if roll_deg > 90.0:
                roll_deg -= 180.0
            elif roll_deg < -90.0:
                roll_deg += 180.0
        eye_mid_x = 0.5 * (rx + lx)
        eye_dist = max(1e-3, math.hypot(dx_eye, dy_eye))
        yaw_deg = max(-60.0, min(60.0, ((nx - eye_mid_x) / eye_dist) * 60.0))
        eye_mid_y = 0.5 * (ry + ly)
        face_span = max(1e-3, abs(my - eye_mid_y))
        pitch_deg = max(-45.0, min(45.0, (((ny - eye_mid_y) / face_span) - 0.5) * 60.0))

        # Decode SSD bounding box relative to its anchor center (fixes top-left corner bias)
        if best_idx < len(_BLAZEFACE_ANCHORS):
            ax, ay = float(_BLAZEFACE_ANCHORS[best_idx, 0]), float(_BLAZEFACE_ANCHORS[best_idx, 1])
        else:
            ax, ay = 0.5, 0.5
        cx = (float(r[0]) / 128.0 + ax) * w
        cy = (float(r[1]) / 128.0 + ay) * h
        bw, bh = abs(float(r[2])) / 128.0 * w, abs(float(r[3])) / 128.0 * h
        box = (
            max(0, int(cx - bw * 0.5)),
            max(0, int(cy - bh * 0.5)),
            min(w, int(cx + bw * 0.5)),
            min(h, int(cy + bh * 0.5)),
        )
        return HeadPoseEstimate(
            valid=True,
            yaw_deg=round(yaw_deg, 1),
            pitch_deg=round(pitch_deg, 1),
            roll_deg=round(roll_deg, 1),
            face_box=box,
            score=round(best_score, 3),
        )
