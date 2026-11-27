"""Two-mode 10-second MediaPipe 478-landmark TFLite Calibration & Atomic Driver Profile Store.

ARM64 Raspberry Pi 4 safe: NEVER imports `mediapipe` (avoids Cortex-A72 SIGILL on missing `+aes`).
Extracts `face_landmarks_detector.tflite` directly from `face_landmarker.task` via OpenCV DNN.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import zipfile

import cv2
import numpy as np

from pi_edge.ai.face_geometry import BlazeFaceGeometry, HeadPoseEstimate, inspect_upper_face_glare

LEFT_EYE_IDX = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_IDX = (362, 385, 387, 263, 373, 380)
MOUTH_LEFT_IDX, MOUTH_RIGHT_IDX, MOUTH_TOP_IDX, MOUTH_BOTTOM_IDX = 61, 291, 13, 14

VALID_SOURCES = {"BOOT", "APP_BLE", "APP_HTTP", "CONTROL_CMD"}


@dataclass(frozen=True)
class MeshSample:
    valid: bool
    presence: float
    ear: float
    mar: float
    left_eye_box: Optional[Tuple[int, int, int, int]] = None
    right_eye_box: Optional[Tuple[int, int, int, int]] = None


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _pt_dist(pts: np.ndarray, i: int, j: int) -> float:
    return float(math.hypot(float(pts[i, 0] - pts[j, 0]), float(pts[i, 1] - pts[j, 1])))


def _eye_ear_from_pts(pts: np.ndarray, indices: Sequence[int]) -> float:
    p1, p2, p3, p4, p5, p6 = indices
    horiz = _pt_dist(pts, p1, p4)
    if horiz <= 1e-6:
        return 0.0
    return (_pt_dist(pts, p2, p6) + _pt_dist(pts, p3, p5)) / (2.0 * horiz)


def _eye_bbox_from_pts(
    pts: np.ndarray,
    indices: Sequence[int],
    frame_w: int,
    frame_h: int,
    pad_px: int = 5,
) -> Tuple[int, int, int, int]:
    xs = [float(pts[i, 0]) for i in indices]
    ys = [float(pts[i, 1]) for i in indices]
    x1 = max(0, min(frame_w - 2, int(round(min(xs))) - pad_px))
    y1 = max(0, min(frame_h - 2, int(round(min(ys))) - pad_px))
    x2 = max(x1 + 2, min(frame_w, int(round(max(xs))) + pad_px))
    y2 = max(y1 + 2, min(frame_h, int(round(max(ys))) + pad_px))
    return (x1, y1, x2, y2)


def _robust_ear_open_median(values: Sequence[float]) -> float:
    """Compute blink-resistant open-eye EAR median."""
    if not values:
        return 0.25
    raw_med = float(statistics.median(values))
    if len(values) >= 5 and raw_med > 0.05:
        # Filter transient mid-blink dips (< 68% of median) while keeping >= 5 samples
        non_blink = [v for v in values if v >= raw_med * 0.68]
        if len(non_blink) >= 5:
            return float(statistics.median(non_blink))
    return raw_med


def _crop_square_with_replicate(
    rgb: np.ndarray,
    x1: int,
    y1: int,
    side: int,
) -> np.ndarray:
    h, w = rgb.shape[:2]
    x2, y2 = x1 + side, y1 + side
    pad_l = max(0, -x1)
    pad_t = max(0, -y1)
    pad_r = max(0, x2 - w)
    pad_b = max(0, y2 - h)
    if pad_l or pad_t or pad_r or pad_b:
        padded = cv2.copyMakeBorder(rgb, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_REPLICATE)
        return padded[y1 + pad_t : y2 + pad_t, x1 + pad_l : x2 + pad_l]
    return rgb[y1:y2, x1:x2]


class FaceMesh478TFLite:
    """OpenCV TFLite 478-point 3D FaceMesh extractor from `face_landmarker.task` (zero mediapipe import)."""

    def __init__(self, task_path: str, min_presence: float = 0.50):
        self._net = None
        self._pres_layer = "Identity_2"
        self.min_presence = float(min_presence)
        self.available = False
        p = Path(task_path)
        if not p.is_file():
            return
        try:
            with zipfile.ZipFile(p, "r") as zf:
                cand = [n for n in zf.namelist() if n.endswith("face_landmarks_detector.tflite")]
                if not cand:
                    return
                buf = zf.read(cand[0])
            net = cv2.dnn.readNetFromTFLite(bytearray(buf))
            unconn = list(net.getUnconnectedOutLayersNames() or ())
            if "Identity_2" in unconn:
                self._pres_layer = "Identity_2"
            elif unconn:
                self._pres_layer = unconn[-1]
            self._net = net
            self.available = True
        except Exception:
            self._net = None
            self.available = False

    def infer_ear_mar(
        self,
        frame_bgr: np.ndarray,
        face_box: Tuple[int, int, int, int],
    ) -> MeshSample:
        if (
            not self.available
            or self._net is None
            or frame_bgr is None
            or frame_bgr.size == 0
            or float(frame_bgr.std()) < 6.0
        ):
            return MeshSample(valid=False, presence=0.0, ear=0.0, mar=0.0)
        h, w = frame_bgr.shape[:2]
        fx1, fy1, fx2, fy2 = face_box
        fbw = max(16.0, float(fx2 - fx1))
        fbh = max(16.0, float(fy2 - fy1))
        side = max(24, int(round(max(fbw, fbh) * 1.45)))
        cx = 0.5 * (fx1 + fx2)
        cy = 0.5 * (fy1 + fy2)
        rx1 = int(round(cx - side * 0.5))
        ry1 = int(round(cy - side * 0.5))

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        crop = _crop_square_with_replicate(rgb, rx1, ry1, side)
        if crop is None or crop.size == 0 or float(crop.std()) < 6.0:
            return MeshSample(valid=False, presence=0.0, ear=0.0, mar=0.0)

        try:
            inp256 = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
            blob = np.transpose(inp256, (2, 0, 1))[np.newaxis, ...]
            self._net.setInput(blob)
            lm_raw = self._net.forward("Identity")
            pres_raw = self._net.forward(self._pres_layer)
            raw_pres = float(pres_raw.reshape(-1)[0])
            presence = _clamp(raw_pres, 0.0, 1.0)

            pts = lm_raw.reshape(478, 3)
            if not np.all(np.isfinite(pts[:, :2])):
                return MeshSample(valid=False, presence=round(presence, 3), ear=0.0, mar=0.0)

            # Anatomical 478-point geometry check in 256x256 space (Identity_2 is already sigmoided)
            bw = float(pts[:, 0].max() - pts[:, 0].min())
            bh = float(pts[:, 1].max() - pts[:, 1].min())
            if bw < 48.0 or bh < 48.0:
                return MeshSample(valid=False, presence=round(presence, 3), ear=0.0, mar=0.0)

            l_cx = float(np.mean([pts[i, 0] for i in LEFT_EYE_IDX]))
            l_cy = float(np.mean([pts[i, 1] for i in LEFT_EYE_IDX]))
            r_cx = float(np.mean([pts[i, 0] for i in RIGHT_EYE_IDX]))
            r_cy = float(np.mean([pts[i, 1] for i in RIGHT_EYE_IDX]))
            l_horiz = _pt_dist(pts, LEFT_EYE_IDX[0], LEFT_EYE_IDX[3])
            r_horiz = _pt_dist(pts, RIGHT_EYE_IDX[0], RIGHT_EYE_IDX[3])
            if (
                abs(r_cx - l_cx) < 0.18 * bw
                or not (0.08 * bw <= l_horiz <= 0.45 * bw)
                or not (0.08 * bw <= r_horiz <= 0.45 * bw)
            ):
                return MeshSample(valid=False, presence=round(presence, 3), ear=0.0, mar=0.0)

            eye_mid_y = 0.5 * (l_cy + r_cy)
            mouth_cy = 0.25 * (
                float(pts[MOUTH_LEFT_IDX, 1])
                + float(pts[MOUTH_RIGHT_IDX, 1])
                + float(pts[MOUTH_TOP_IDX, 1])
                + float(pts[MOUTH_BOTTOM_IDX, 1])
            )
            if (mouth_cy - eye_mid_y) < 0.12 * bh:
                return MeshSample(valid=False, presence=round(presence, 3), ear=0.0, mar=0.0)

            pts_px = np.empty((478, 2), dtype=np.float32)
            pts_px[:, 0] = float(rx1) + (pts[:, 0] / 256.0) * float(side)
            pts_px[:, 1] = float(ry1) + (pts[:, 1] / 256.0) * float(side)

            ear_l = _eye_ear_from_pts(pts_px, LEFT_EYE_IDX)
            ear_r = _eye_ear_from_pts(pts_px, RIGHT_EYE_IDX)
            ear = 0.5 * (ear_l + ear_r)
            if abs(ear_l - ear_r) > 0.18 or not (0.005 <= ear <= 0.65):
                return MeshSample(valid=False, presence=round(presence, 3), ear=0.0, mar=0.0)

            mouth_w = _pt_dist(pts_px, MOUTH_LEFT_IDX, MOUTH_RIGHT_IDX)
            mouth_h = _pt_dist(pts_px, MOUTH_TOP_IDX, MOUTH_BOTTOM_IDX)
            mar = (mouth_h / mouth_w) if mouth_w > 1e-6 else 0.0

            l_box = _eye_bbox_from_pts(pts_px, LEFT_EYE_IDX, w, h)
            r_box = _eye_bbox_from_pts(pts_px, RIGHT_EYE_IDX, w, h)
            return MeshSample(
                valid=True,
                presence=round(presence, 3),
                ear=round(ear, 4),
                mar=round(mar, 4),
                left_eye_box=l_box,
                right_eye_box=r_box,
            )
        except Exception:
            return MeshSample(valid=False, presence=0.0, ear=0.0, mar=0.0)


def load_driver_profile(profile_path: Path) -> Optional[Dict[str, Any]]:
    p = Path(profile_path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        req = (
            "locked",
            "created_ts",
            "source",
            "ear_open_median",
            "ear_closed_threshold",
            "mar_rest_median",
            "neutral_yaw_deg",
            "neutral_pitch_deg",
            "neutral_roll_deg",
            "glare_mode",
            "samples",
        )
        for k in req:
            if k not in data:
                return None
        thresh = float(data["ear_closed_threshold"])
        if not (0.10 <= thresh <= 0.35):
            return None
        return {
            "locked": bool(data["locked"]),
            "created_ts": float(data["created_ts"]),
            "source": str(data["source"]),
            "ear_open_median": float(data["ear_open_median"]),
            "ear_closed_threshold": round(_clamp(thresh, 0.13, 0.26), 4),
            "mar_rest_median": float(data["mar_rest_median"]),
            "neutral_yaw_deg": round(_clamp(float(data["neutral_yaw_deg"]), -18.0, 18.0), 2),
            "neutral_pitch_deg": round(_clamp(float(data["neutral_pitch_deg"]), -15.0, 15.0), 2),
            "neutral_roll_deg": round(_clamp(float(data["neutral_roll_deg"]), -15.0, 15.0), 2),
            "glare_mode": bool(data["glare_mode"]),
            "samples": int(data["samples"]),
        }
    except Exception:
        return None


def save_driver_profile_atomic(profile_path: Path, profile: Dict[str, Any]) -> None:
    """Atomically persist `driver_profile.json` via same-directory tempfile + `os.replace`."""
    target = Path(profile_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(target.parent),
            prefix=".driver_profile_",
            suffix=".tmp",
            delete=False,
        ) as fp:
            tmp_name = fp.name
            json.dump(profile, fp, indent=2, ensure_ascii=False)
            fp.write("\n")
            fp.flush()
            try:
                os.fsync(fp.fileno())
            except OSError:
                pass
        os.replace(tmp_name, target)
        tmp_name = None
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


class DriverCalibrator10s:
    """Thread-safe 2-mode 10.0s MediaPipe calibrator (`BOOT` & `APP_LOCK`)."""

    def __init__(
        self,
        calibration_dir: Path,
        face_geo: BlazeFaceGeometry,
        face_mesh: Optional[FaceMesh478TFLite] = None,
        *,
        duration_s: float = 10.0,
        auto_boot: bool = True,
        sample_extractor: Optional[Callable[[np.ndarray], Tuple[HeadPoseEstimate, MeshSample, bool]]] = None,
    ):
        self.calibration_dir = Path(calibration_dir)
        self.profile_path = self.calibration_dir / "driver_profile.json"
        self.duration_s = max(1.0, float(duration_s))
        self._geo = face_geo
        self._mesh = face_mesh
        self._sample_extractor = sample_extractor

        self._lock = threading.Lock()
        self._profile: Optional[Dict[str, Any]] = load_driver_profile(self.profile_path)
        self._state: str = "IDLE"          # IDLE | RUNNING | DONE | FAILED
        self._mode: Optional[str] = None   # BOOT | APP_LOCK | None
        self._source: str = "BOOT"
        self._lock_target: bool = False
        self._boot_armed: bool = bool(auto_boot)
        self._started_mono: Optional[float] = None
        self._progress_s: float = 0.0
        self._last_fail_reason: str = ""

        self._ears: List[float] = []
        self._mars: List[float] = []
        self._yaws: List[float] = []
        self._pitches: List[float] = []
        self._rolls: List[float] = []
        self._glare_hits: int = 0
        self._events: deque[Dict[str, Any]] = deque(maxlen=8)

    @property
    def available(self) -> bool:
        if self._sample_extractor is not None:
            return True
        return bool(self._geo.available and self._mesh is not None and self._mesh.available)

    @property
    def mesh(self) -> Optional[FaceMesh478TFLite]:
        return self._mesh

    def _clear_samples_unlocked(self) -> None:
        self._ears.clear()
        self._mars.clear()
        self._yaws.clear()
        self._pitches.clear()
        self._rolls.clear()
        self._glare_hits = 0

    def notify_real_camera_frame(self, now_mono: float) -> bool:
        """Start Mode 1 (`BOOT`) 10s timer ONLY upon receiving the first real non-stale camera frame."""
        with self._lock:
            if self._state == "RUNNING" and self._started_mono is not None:
                elapsed = max(0.0, float(now_mono) - self._started_mono)
                self._progress_s = min(self.duration_s, elapsed)
                if elapsed >= self.duration_s:
                    self._finalize_unlocked()
                return self._state == "RUNNING"

            if self._boot_armed and self._state == "IDLE" and self.available:
                self._boot_armed = False
                self._state = "RUNNING"
                self._mode = "BOOT"
                self._source = "BOOT"
                self._lock_target = False
                self._started_mono = float(now_mono)
                self._progress_s = 0.0
                self._last_fail_reason = ""
                self._clear_samples_unlocked()
                return True
            return False

    def tick_time(self, now_mono: float) -> None:
        """Advance/finalize active calibration timer even if camera stalls mid-window."""
        with self._lock:
            if self._state == "RUNNING" and self._started_mono is not None:
                elapsed = max(0.0, float(now_mono) - self._started_mono)
                self._progress_s = min(self.duration_s, elapsed)
                if elapsed >= self.duration_s:
                    self._finalize_unlocked()

    def is_running(self, now_mono: Optional[float] = None) -> bool:
        with self._lock:
            if self._state != "RUNNING" or self._started_mono is None:
                return False
            if now_mono is not None:
                elapsed = max(0.0, float(now_mono) - self._started_mono)
                self._progress_s = min(self.duration_s, elapsed)
                if elapsed >= self.duration_s:
                    self._finalize_unlocked()
                    return False
            return True

    def request_calibration(
        self,
        source: str = "CONTROL_CMD",
        lock: bool = True,
        now_mono: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Trigger Mode 2 (`APP_LOCK`) 10s calibration safely from HTTP/BLE/Control threads."""
        clean_src = str(source or "CONTROL_CMD").strip().upper()
        if clean_src not in VALID_SOURCES:
            clean_src = "CONTROL_CMD"
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            if self._state == "RUNNING" and self._mode == "APP_LOCK":
                return False, "CALIBRATION_BUSY"
            self._boot_armed = False
            self._state = "RUNNING"
            self._mode = "APP_LOCK" if lock else "BOOT"
            self._source = clean_src
            self._lock_target = bool(lock)
            self._started_mono = now_m
            self._progress_s = 0.0
            self._last_fail_reason = ""
            self._clear_samples_unlocked()
            return True, "CALIBRATION_STARTED"

    def step_frame(
        self,
        frame_bgr: np.ndarray,
        now_mono: Optional[float] = None,
    ) -> Tuple[HeadPoseEstimate, Optional[MeshSample], Dict[str, Any]]:
        """Run 1 sequential BlazeFace + 478-Mesh calibration pass on `frame_bgr`."""
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        if self._sample_extractor is not None:
            pose, mesh_s, glare = self._sample_extractor(frame_bgr)
        else:
            pose = self._geo.detect_pose(frame_bgr)
            mesh_s = None
            glare = False
            if pose.valid and pose.face_box is not None:
                glare, _, _ = inspect_upper_face_glare(frame_bgr, pose.face_box)
                if self._mesh is not None and self._mesh.available:
                    mesh_s = self._mesh.infer_ear_mar(frame_bgr, pose.face_box)

        with self._lock:
            if self._state == "RUNNING" and self._started_mono is not None:
                elapsed = max(0.0, now_m - self._started_mono)
                self._progress_s = min(self.duration_s, elapsed)
                if (
                    pose.valid
                    and abs(float(pose.yaw_deg)) <= 22.0
                    and abs(float(pose.pitch_deg)) <= 20.0
                    and abs(float(pose.roll_deg)) <= 20.0
                    and mesh_s is not None
                    and mesh_s.valid
                    and float(mesh_s.ear) >= 0.14
                ):
                    self._ears.append(float(mesh_s.ear))
                    self._mars.append(float(mesh_s.mar))
                    self._yaws.append(float(pose.yaw_deg))
                    self._pitches.append(float(pose.pitch_deg))
                    self._rolls.append(float(pose.roll_deg))
                    if glare:
                        self._glare_hits += 1
                if elapsed >= self.duration_s:
                    self._finalize_unlocked()
            status = self._status_dict_unlocked()
        return pose, mesh_s, status

    def _finalize_unlocked(self) -> None:
        n = len(self._ears)
        mode = self._mode or "BOOT"
        has_locked_profile = bool(self._profile is not None and self._profile.get("locked") is True)

        # Mode 1 (BOOT) when a locked profile ALREADY exists:
        # Never overwrite or erase the driver's locked driver_profile.json!
        if mode == "BOOT" and not self._lock_target and has_locked_profile:
            self._state = "DONE"
            self._progress_s = self.duration_s
            self._last_fail_reason = ""
            self._events.append(
                {
                    "state": "DONE",
                    "mode": mode,
                    "locked": True,
                    "reason": "LOCKED_PRESERVED",
                    "samples": n,
                }
            )
            return

        min_req = 8 if self._lock_target else 5
        if n < min_req:
            reason = "NO_FACE" if n == 0 else "LOW_SAMPLES"
            self._state = "FAILED"
            self._progress_s = self.duration_s
            self._last_fail_reason = reason
            # Keep previous good profile untouched!
            self._events.append(
                {
                    "state": "FAILED",
                    "mode": mode,
                    "locked": has_locked_profile,
                    "reason": reason,
                    "samples": n,
                }
            )
            return

        ear_med = _robust_ear_open_median(self._ears)
        ear_thresh = max(0.13, min(0.26, ear_med * 0.70))
        mar_med = float(statistics.median(self._mars)) if self._mars else 0.05
        yaw_med = _clamp(float(statistics.median(self._yaws)) if self._yaws else 0.0, -18.0, 18.0)
        pitch_med = _clamp(float(statistics.median(self._pitches)) if self._pitches else 0.0, -15.0, 15.0)
        roll_med = _clamp(float(statistics.median(self._rolls)) if self._rolls else 0.0, -15.0, 15.0)
        glare_mode = (self._glare_hits / float(max(1, n))) >= 0.30

        new_prof: Dict[str, Any] = {
            "locked": bool(self._lock_target),
            "created_ts": round(time.time(), 3),
            "source": self._source,
            "ear_open_median": round(ear_med, 4),
            "ear_closed_threshold": round(ear_thresh, 4),
            "mar_rest_median": round(mar_med, 4),
            "neutral_yaw_deg": round(yaw_med, 2),
            "neutral_pitch_deg": round(pitch_med, 2),
            "neutral_roll_deg": round(roll_med, 2),
            "glare_mode": bool(glare_mode),
            "samples": int(n),
        }
        try:
            save_driver_profile_atomic(self.profile_path, new_prof)
        except Exception:
            pass
        self._profile = new_prof
        self._state = "DONE"
        self._progress_s = self.duration_s
        self._last_fail_reason = ""
        self._events.append(
            {
                "state": "DONE",
                "mode": mode,
                "locked": bool(new_prof["locked"]),
                "reason": "OK",
                "samples": n,
            }
        )

    def _status_dict_unlocked(self) -> Dict[str, Any]:
        prof = self._profile
        return {
            "state": self._state,
            "mode": self._mode,
            "progress_s": round(float(self._progress_s), 2),
            "duration_s": round(float(self.duration_s), 1),
            "locked": bool(prof.get("locked", False)) if prof else False,
            "profile_ts": float(prof["created_ts"]) if (prof and prof.get("created_ts") is not None) else None,
            "ear_closed_threshold": (
                float(prof["ear_closed_threshold"])
                if (prof and prof.get("ear_closed_threshold") is not None)
                else None
            ),
            "glare_mode": bool(prof.get("glare_mode", False)) if prof else False,
        }

    def get_status_dict(self, now_mono: Optional[float] = None) -> Dict[str, Any]:
        if now_mono is not None:
            self.tick_time(now_mono)
        with self._lock:
            return self._status_dict_unlocked()

    def get_active_profile(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._profile) if self._profile is not None else None

    def get_ear_closed_threshold(self, default: float = 0.18) -> float:
        with self._lock:
            if self._profile and self._profile.get("ear_closed_threshold") is not None:
                return float(self._profile["ear_closed_threshold"])
            return float(default)

    def get_neutral_pose(self) -> Tuple[float, float, float]:
        with self._lock:
            if self._profile is None:
                return 0.0, 0.0, 0.0
            return (
                float(self._profile.get("neutral_yaw_deg", 0.0)),
                float(self._profile.get("neutral_pitch_deg", 0.0)),
                float(self._profile.get("neutral_roll_deg", 0.0)),
            )

    def is_glare_mode(self) -> bool:
        with self._lock:
            return bool(self._profile and self._profile.get("glare_mode", False))

    def pop_event(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._events.popleft() if self._events else None
