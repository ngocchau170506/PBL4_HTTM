"""BlazeFace 6-point Letterbox OpenCV DNN + Roll-Aligned 3D Head-Tilt, Yawn & Glare Geometry."""
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


def normalize_roll_deg(angle_deg: float) -> float:
    """Normalize roll angle into [-90, +90] degrees with 0 as upright."""
    a = float(angle_deg)
    while a > 90.0:
        a -= 180.0
    while a < -90.0:
        a += 180.0
    return a


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


def expand_square_face_roi(
    face_box: Tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    scale_mult: float = 1.30,
) -> Tuple[int, int, int, int]:
    """Expand face_box into a clamped square crop (default 1.30x) in frame pixel coordinates."""
    fx1, fy1, fx2, fy2 = face_box
    bw = max(16, fx2 - fx1)
    bh = max(16, fy2 - fy1)
    side = max(24, int(round(max(bw, bh) * float(scale_mult))))
    cx = (fx1 + fx2) // 2
    cy = (fy1 + fy2) // 2
    rx1 = max(0, cx - side // 2)
    ry1 = max(0, cy - side // 2)
    rx2 = min(frame_w, rx1 + side)
    ry2 = min(frame_h, ry1 + side)
    rx1 = max(0, rx2 - side)
    ry1 = max(0, ry2 - side)
    return (int(rx1), int(ry1), int(rx2), int(ry2))


def inspect_upper_face_glare(
    frame_bgr: np.ndarray,
    face_box: Tuple[int, int, int, int],
) -> Tuple[bool, float, float]:
    """Detect eyeglass reflection / specular glare on the upper half of face_box.

    Returns (glare_detected, p95_luma, hi235_ratio).
    """
    h, w = frame_bgr.shape[:2]
    fx1, fy1, fx2, fy2 = face_box
    fw = max(1, fx2 - fx1)
    fh = max(1, fy2 - fy1)
    trim_x = int(fw * 0.08)
    ux1 = max(0, min(w - 1, fx1 + trim_x))
    ux2 = max(ux1 + 1, min(w, fx2 - trim_x))
    uy1 = max(0, min(h - 1, fy1))
    uy2 = max(uy1 + 1, min(h, fy1 + max(4, fh // 2)))
    patch = frame_bgr[uy1:uy2, ux1:ux2]
    if patch.size == 0:
        return False, 0.0, 0.0
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    p95 = float(np.percentile(gray, 95))
    hi_ratio = float(np.mean(gray >= 235))
    glare = (p95 >= 205.0) or (hi_ratio > 0.015)
    return glare, round(p95, 1), round(hi_ratio, 4)


def apply_clahe_lab(roi_bgr: np.ndarray, clip_limit: float = 2.2, tile_size: int = 4) -> np.ndarray:
    """Apply gentle local contrast enhancement on LAB L-channel to recover eyelids through lens glare."""
    if roi_bgr is None or roi_bgr.size == 0:
        return roi_bgr
    lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=(int(tile_size), int(tile_size)))
    l_eq = clahe.apply(l_ch)
    return cv2.cvtColor(cv2.merge((l_eq, a_ch, b_ch)), cv2.COLOR_LAB2BGR)


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
        self.available = False
        self.forward_calls = 0
        self._single_output_only = False
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
        if h <= 0 or w <= 0:
            return HeadPoseEstimate(valid=False, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)

        # Aspect-preserving 128x128 letterbox prevents 1.77x horizontal face_box stretch on 16:9 frames
        scale = min(128.0 / float(w), 128.0 / float(h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
        padded = np.zeros((128, 128, 3), dtype=np.uint8)
        pad_x = (128 - nw) // 2
        pad_y = (128 - nh) // 2
        padded[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized

        blob = cv2.dnn.blobFromImage(
            padded, 1.0 / 127.5, (128, 128), (127.5, 127.5, 127.5), swapRB=True, crop=False
        )
        self._net.setInput(blob)

        # Try 1-pass multi-output forward(["classificators", "regressors"]) first (2x faster on OpenCV 4.x Pi 4);
        # automatically latch single-output fallback if OpenCV 5.0 DNN asserts on multi-output.
        try:
            if not self._single_output_only:
                try:
                    outs = self._net.forward(["classificators", "regressors"])
                    if isinstance(outs, (list, tuple)) and len(outs) == 2:
                        clf, reg = outs[0], outs[1]
                        self.forward_calls += 1
                    else:
                        self._single_output_only = True
                        clf = self._net.forward("classificators")
                        reg = self._net.forward("regressors")
                        self.forward_calls += 2
                except Exception:
                    self._single_output_only = True
                    clf = self._net.forward("classificators")
                    reg = self._net.forward("regressors")
                    self.forward_calls += 2
            else:
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
        if best_idx < len(_BLAZEFACE_ANCHORS):
            ax, ay = float(_BLAZEFACE_ANCHORS[best_idx, 0]), float(_BLAZEFACE_ANCHORS[best_idx, 1])
        else:
            ax, ay = 0.5, 0.5
        ax_pad = ax * 128.0
        ay_pad = ay * 128.0

        # Un-letterbox 6 BlazeFace keypoints back into original frame coordinates
        rx = ((ax_pad + float(r[4])) - pad_x) / scale
        ry = ((ay_pad + float(r[5])) - pad_y) / scale
        lx = ((ax_pad + float(r[6])) - pad_x) / scale
        ly = ((ay_pad + float(r[7])) - pad_y) / scale
        nx = ((ax_pad + float(r[8])) - pad_x) / scale
        ny = ((ay_pad + float(r[9])) - pad_y) / scale
        mx = ((ax_pad + float(r[10])) - pad_x) / scale
        my = ((ay_pad + float(r[11])) - pad_y) / scale

        # Ensure left-of-image vs right-of-image consistency for roll axis
        p1, p2 = ((rx, ry), (lx, ly)) if rx <= lx else ((lx, ly), (rx, ry))
        dx_eye = p2[0] - p1[0]
        dy_eye = p2[1] - p1[1]
        eye_dist = max(1.0, math.hypot(dx_eye, dy_eye))
        if abs(dx_eye) < 1e-3:
            roll_deg = 90.0 if dy_eye >= 0 else -90.0
        else:
            roll_deg = normalize_roll_deg(math.degrees(math.atan2(dy_eye, dx_eye)))

        # Project nose & mouth vectors onto roll-aligned head axes (ux, uy), (vx, vy)
        # so tilting the head (roll != 0) never induces fake yaw_deg or pitch_deg!
        eye_mid_x = 0.5 * (p1[0] + p2[0])
        eye_mid_y = 0.5 * (p1[1] + p2[1])
        rad = math.radians(roll_deg)
        ux, uy = math.cos(rad), math.sin(rad)   # eye-line horizontal axis
        vx, vy = -uy, ux                         # face-down vertical axis

        dnx, dny = nx - eye_mid_x, ny - eye_mid_y
        nose_lateral = dnx * ux + dny * uy
        yaw_deg = max(-65.0, min(65.0, (nose_lateral / (eye_dist * 0.5)) * 32.0))

        dmx, dmy = mx - eye_mid_x, my - eye_mid_y
        mouth_down = max(1.0, dmx * vx + dmy * vy)
        nose_down = dnx * vx + dny * vy
        ratio = nose_down / mouth_down
        pitch_deg = max(-55.0, min(55.0, (ratio - 0.48) * 95.0))

        # Decode SSD bounding box relative to anchor in padded space and un-letterbox
        cx = ((ax_pad + float(r[0])) - pad_x) / scale
        cy = ((ay_pad + float(r[1])) - pad_y) / scale
        bw = max(4.0, abs(float(r[2]))) / scale
        bh = max(4.0, abs(float(r[3]))) / scale
        fx1 = max(0, min(w - 1, int(round(cx - bw * 0.5))))
        fy1 = max(0, min(h - 1, int(round(cy - bh * 0.5))))
        fx2 = max(fx1 + 2, min(w, int(round(cx + bw * 0.5))))
        fy2 = max(fy1 + 2, min(h, int(round(cy + bh * 0.5))))
        box = (fx1, fy1, fx2, fy2)

        return HeadPoseEstimate(
            valid=True,
            yaw_deg=round(yaw_deg, 1),
            pitch_deg=round(pitch_deg, 1),
            roll_deg=round(roll_deg, 1),
            face_box=box,
            score=round(best_score, 3),
        )
