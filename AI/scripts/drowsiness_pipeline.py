"""Stateful YOLO + MediaPipe monitoring pipeline."""

import math
import time
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
from ultralytics import YOLO

from .calibration import (
    BASELINE_PATH, LEFT_EYE, RIGHT_EYE, calculate_ear, calculate_mar,
    calculate_head_pose, validate_baseline,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
YOLO_MODEL_PATH = PROJECT_ROOT / "AI" / "models" / "best.pt"
EYE_CLOSED_SECONDS, YAWN_SECONDS = 2.0, 1.0
K_EAR, K_MAR = 2.0, 2.0
BUFFER_FPS, BUFFER_SIZE = 10, 10


def _distance(p1, p2):
    return math.hypot(p1.x - p2.x, p1.y - p2.y)


def fit_frame_to_size(frame, target_width, target_height):
    height, width = frame.shape[:2]
    scale = min(target_width / width, target_height / height)
    resized = cv2.resize(frame, (max(1, int(width * scale)),
                                  max(1, int(height * scale))))
    top = (target_height - resized.shape[0]) // 2
    bottom = target_height - resized.shape[0] - top
    left = (target_width - resized.shape[1]) // 2
    right = target_width - resized.shape[1] - left
    return cv2.copyMakeBorder(resized, top, bottom, left, right,
                              cv2.BORDER_CONSTANT, value=(10, 10, 10))


class DrowsinessPipeline:
    def __init__(self, face_landmarker, baseline_path=BASELINE_PATH,
                 model_path=YOLO_MODEL_PATH):
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"Khong tim thay YOLO model: {model_path}")
        baseline = validate_baseline(baseline_path)
        if baseline is None:
            raise ValueError("Baseline khong ton tai hoac khong hop le")
        self.face_landmarker = face_landmarker
        self.model = YOLO(str(model_path))
        self.ear_threshold = baseline["ear"]["mean"] - K_EAR * baseline["ear"]["std"]
        self.mar_threshold = baseline["mar"]["mean"] + K_MAR * baseline["mar"]["std"]
        self.frame_buffer = deque(maxlen=BUFFER_SIZE)
        self.last_sample_at = 0.0
        self.eye_start = self.mouth_start = None
        self.eye_triggered = self.yawn_triggered = False
        self.timestamp = -1
        self.last_yolo_alerts = set()

    def _detect_objects(self, frame):
        detections, names = [], set()
        for result in self.model.predict(frame, conf=0.1, imgsz=640, verbose=False):
            for box in result.boxes:
                class_id, confidence = int(box.cls[0]), float(box.conf[0])
                name = str(self.model.names[class_id]).strip()
                names.add(name.casefold())
                detections.append((name, confidence))
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 165, 0), 2)
                cv2.putText(frame, f"{name} {confidence:.2f}",
                            (x1, max(y1 - 10, 20)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 165, 0), 2)
        return detections, names

    def process(self, frame):
        now = time.monotonic()
        if now - self.last_sample_at >= 1.0 / BUFFER_FPS:
            self.frame_buffer.append(frame.copy())
            self.last_sample_at = now
        self.timestamp = max(self.timestamp + 1, int(now * 1000))
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
        )
        result = self.face_landmarker.detect_for_video(image, self.timestamp)
        detections, yolo_classes = self._detect_objects(frame)
        ear = mar = None
        eye_state = mouth_state = "NO FACE"
        eye_duration = mouth_duration = 0.0
        if result.face_landmarks:
            landmarks = result.face_landmarks[0]
            height, width = frame.shape[:2]
            ear = (calculate_ear(landmarks, LEFT_EYE, width, height)
                   + calculate_ear(landmarks, RIGHT_EYE, width, height)) / 2
            mar = calculate_mar(landmarks, width, height)
            if ear < self.ear_threshold:
                self.eye_start = self.eye_start or now
                eye_duration = now - self.eye_start
                self.eye_triggered = eye_duration >= EYE_CLOSED_SECONDS
            else:
                self.eye_start, self.eye_triggered = None, False
            if mar > self.mar_threshold:
                self.mouth_start = self.mouth_start or now
                mouth_duration = now - self.mouth_start
                self.yawn_triggered = mouth_duration >= YAWN_SECONDS
            else:
                self.mouth_start, self.yawn_triggered = None, False
            eye_state = "EYE DROWSY" if self.eye_triggered else ("EYE CLOSED" if ear < self.ear_threshold else "EYE OPEN")
            mouth_state = "YAWNING" if self.yawn_triggered else ("MOUTH OPEN" if mar > self.mar_threshold else "MOUTH CLOSED")
        else:
            self.eye_start = self.mouth_start = None
            self.eye_triggered = self.yawn_triggered = False
        alerts = (["EYE DROWSY"] if self.eye_triggered else []) + (["MOUTH OPEN"] if self.yawn_triggered else [])
        alerts += [name.upper() for name in ("distracted head", "phone", "smoking") if name in yolo_classes]
        state = {
            "face": bool(result.face_landmarks), "ear": ear, "mar": mar,
            "eye_state": eye_state, "mouth_state": mouth_state,
            "eye_duration": eye_duration, "mouth_duration": mouth_duration,
            "eye_threshold_seconds": EYE_CLOSED_SECONDS,
            "yawn_threshold_seconds": YAWN_SECONDS,
            "head_status": "DISTRACTED HEAD" if "distracted head" in yolo_classes else "NORMAL",
            "detections": detections, "buffer_count": len(self.frame_buffer),
            "combined_status": " | ".join(alerts) if alerts else "NORMAL",
        }
        return frame, state

    def close(self):
        self.face_landmarker = None
