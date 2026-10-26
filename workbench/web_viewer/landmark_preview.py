"""Bộ nhận diện MediaPipe Face Landmarker cho web_viewer (models/face_landmarker.task).

Sử dụng mô hình face_landmarker.task để trích xuất 478 điểm mốc khuôn mặt,
tính toán EAR (Eye Aspect Ratio), MAR (Mouth Aspect Ratio) và Head Pose (Pitch, Yaw, Roll).
Kết quả được chuẩn hóa thành các Detection tương thích với LiveRunner và AlertDispatcher.
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from contracts.logic import Detection, ErrorEvent, Health, NodeId
from state_hub import StateHub

DEFAULT_TASK_PATH = ROOT / "models" / "face_landmarker.task"

LEFT_EYE_IDX: Tuple[int, ...] = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_IDX: Tuple[int, ...] = (362, 385, 387, 263, 373, 380)
MOUTH_IDX: Tuple[int, ...] = (61, 291, 13, 14, 78, 308, 82, 312, 87, 317)


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return float(math.hypot(a[0] - b[0], a[1] - b[1]))


def _calc_ear(landmarks: Sequence[Any], idxs: Sequence[int], w: int, h: int) -> float:
    pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in idxs]
    horiz = _dist(pts[0], pts[3])
    if horiz < 1e-6:
        return 0.30
    vert = _dist(pts[1], pts[5]) + _dist(pts[2], pts[4])
    return vert / (2.0 * horiz)


def _calc_mar(landmarks: Sequence[Any], w: int, h: int) -> float:
    pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in MOUTH_IDX]
    horiz = _dist(pts[0], pts[1])
    if horiz < 1e-6:
        return 0.20
    vert = (_dist(pts[2], pts[3]) + _dist(pts[6], pts[8]) + _dist(pts[7], pts[9])) / 3.0
    return vert / horiz


class LandmarkPreview:
    """Bộ nhận diện MediaPipe Face Landmarker xem thử cho web_viewer."""

    name: str = "landmark_preview"
    node_id: NodeId = "process.ai"

    def __init__(
        self,
        task_path: Optional[Path] = None,
        min_detection_confidence: float = 0.5,
    ) -> None:
        self._task_path = Path(task_path) if task_path is not None else DEFAULT_TASK_PATH
        self._min_conf = float(min_detection_confidence)
        self._landmarker: Optional[Any] = None
        self._health: Health = "UNKNOWN"
        self._last_infer_ms: float = 0.0
        self._last_ear: float = 0.30
        self._last_mar: float = 0.20

    @property
    def health_status(self) -> Health:
        return self._health

    @property
    def last_infer_ms(self) -> float:
        return self._last_infer_ms

    @property
    def last_ear(self) -> float:
        return self._last_ear

    @property
    def last_mar(self) -> float:
        return self._last_mar

    def load(self) -> None:
        if not self._task_path.is_file():
            self._health = "DOWN"
            raise FileNotFoundError(f"Không tìm thấy file mô hình: {self._task_path}")

        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            base_options = python.BaseOptions(model_asset_path=str(self._task_path))
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
                num_faces=1,
                min_face_detection_confidence=self._min_conf,
                running_mode=vision.RunningMode.IMAGE,
            )
            self._landmarker = vision.FaceLandmarker.create_from_options(options)
            self._health = "OK"
        except Exception as exc:
            self._health = "DOWN"
            raise RuntimeError(f"Lỗi khởi tạo MediaPipe FaceLandmarker: {exc}") from exc

    def health(self) -> Health:
        return self._health

    def detect(self, bgr: np.ndarray) -> Tuple[Detection, ...]:
        if self._landmarker is None or self._health != "OK":
            return ()

        t0 = time.perf_counter()
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

        import mediapipe as mp
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = self._landmarker.detect(mp_image)
        self._last_infer_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        if not res.face_landmarks:
            return ()

        lms = res.face_landmarks[0]
        ear_l = _calc_ear(lms, LEFT_EYE_IDX, w, h)
        ear_r = _calc_ear(lms, RIGHT_EYE_IDX, w, h)
        ear = (ear_l + ear_r) / 2.0
        mar = _calc_mar(lms, w, h)
        self._last_ear = round(ear, 3)
        self._last_mar = round(mar, 3)

        xs = [int(p.x * w) for p in lms]
        ys = [int(p.y * h) for p in lms]
        min_x, max_x = max(0, min(xs)), min(w, max(xs))
        min_y, max_y = max(0, min(ys)), min(h, max(ys))
        box = (min_x, min_y, max_x - min_x, max_y - min_y)

        items: List[Detection] = []
        if ear < 0.20:
            items.append(Detection(label="Eyeclosed", conf=round(min(1.0, (0.22 - ear) * 5.0), 2), box=box))
        else:
            items.append(Detection(label="Attentive eye", conf=round(min(1.0, ear * 2.5), 2), box=box))

        if mar > 0.55:
            items.append(Detection(label="Yawn", conf=round(min(1.0, mar), 2), box=box))

        return tuple(items)


def attach_landmark_preview(
    hub: StateHub,
    preview: Optional[LandmarkPreview] = None,
) -> Optional[LandmarkPreview]:
    """Khởi tạo LandmarkPreview và đăng ký vào StateHub."""
    det = preview if preview is not None else LandmarkPreview()
    try:
        det.load()
        hub.set_node_status("process.ai", "OK")
        hub.set_meta("landmark_preview_status", "OK")
        return det
    except Exception as exc:
        hub.set_node_status("process.ai", "DOWN")
        hub.set_meta("landmark_preview_status", "DOWN")
        hub.set_meta("landmark_load_error", str(exc))
        return None
