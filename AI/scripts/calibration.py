"""Reusable face calibration primitives.

This module deliberately does not open a camera or create a MediaPipe task when
it is imported.  The application owns those resources and passes frames here.
"""

import json
import math
import os
import statistics
import time
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = PROJECT_ROOT / "AI" / "models" / "face_landmarker.task"
BASELINE_PATH = PROJECT_ROOT / "AI" / "calibration" / "baseline.json"
CALIBRATION_SECONDS = 5.0
SAMPLE_INTERVAL = 0.1

LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
MOUTH_LEFT, MOUTH_RIGHT, MOUTH_TOP, MOUTH_BOTTOM = 61, 291, 13, 14
MODEL_POINTS = np.array(
    [[0.0, 0.0, 0.0], [0.0, 330.0, 65.0],
     [-225.0, -170.0, 135.0], [225.0, -170.0, 135.0],
     [-150.0, 150.0, 125.0], [150.0, 150.0, 125.0]],
    dtype=np.float64,
)
LANDMARK_IDS = [1, 152, 33, 263, 61, 291]


def distance(p1, p2, width, height):
    return math.hypot((p1.x - p2.x) * width, (p1.y - p2.y) * height)


def calculate_ear(landmarks, indices, width, height):
    p1, p2, p3, p4, p5, p6 = [landmarks[i] for i in indices]
    horizontal = distance(p1, p4, width, height)
    if horizontal == 0:
        return 0.0
    return (
        distance(p2, p6, width, height)
        + distance(p3, p5, width, height)
    ) / (2.0 * horizontal)


def calculate_mar(landmarks, width, height):
    horizontal = distance(
        landmarks[MOUTH_LEFT], landmarks[MOUTH_RIGHT], width, height
    )
    if horizontal == 0:
        return 0.0
    return distance(
        landmarks[MOUTH_TOP], landmarks[MOUTH_BOTTOM], width, height
    ) / horizontal


def get_camera_matrix(width, height):
    focal = float(width)
    return np.array(
        [[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]],
        dtype=np.float64,
    )


def calculate_head_pose(landmarks, width, height):
    image_points = np.array(
        [[landmarks[index].x * width, landmarks[index].y * height]
         for index in LANDMARK_IDS],
        dtype=np.float64,
    )
    success, rotation_vector, _ = cv2.solvePnP(
        MODEL_POINTS, image_points, get_camera_matrix(width, height),
        np.zeros((4, 1)), flags=cv2.SOLVEPNP_ITERATIVE,
    )
    if not success:
        return None
    rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
    sy = math.sqrt(rotation_matrix[0, 0] ** 2 + rotation_matrix[1, 0] ** 2)
    return (
        math.degrees(math.atan2(-rotation_matrix[2, 0], sy)),
        math.degrees(math.atan2(rotation_matrix[2, 1], rotation_matrix[2, 2])),
        math.degrees(math.atan2(rotation_matrix[1, 0], rotation_matrix[0, 0])),
    )


def robust_filter(values):
    if len(values) < 5:
        return list(values)
    median = statistics.median(values)
    mad = statistics.median([abs(value - median) for value in values])
    if mad == 0:
        return list(values)
    return [value for value in values if abs(value - median) <= 3.5 * mad]


def calculate_statistics(values):
    filtered = robust_filter(values)
    if not filtered:
        return {"mean": 0.0, "std": 0.0}
    if len(filtered) == 1:
        return {"mean": round(filtered[0], 4), "std": 0.0}
    return {
        "mean": round(statistics.mean(filtered), 4),
        "std": round(statistics.stdev(filtered), 4),
    }


def validate_baseline(path=BASELINE_PATH, minimum_samples=1):
    """Return a baseline only when its required numeric structure is valid."""
    try:
        with Path(path).open(encoding="utf-8") as file:
            baseline = json.load(file)
        samples = baseline["samples"]
        if any(
            not isinstance(samples[key], int) or samples[key] < minimum_samples
            for key in ("valid_ear", "valid_mar", "valid_head_pose")
        ):
            return None
        for metric in ("ear", "mar"):
            if not all(isinstance(baseline[metric][key], (int, float))
                       for key in ("mean", "std")):
                return None
        return baseline
    except (OSError, ValueError, KeyError, TypeError):
        return None


class CalibrationSession:
    def __init__(self, duration=CALIBRATION_SECONDS, sample_interval=SAMPLE_INTERVAL):
        self.duration = float(duration)
        self.sample_interval = float(sample_interval)
        self.started_at = None
        self.last_sample_at = 0.0
        self.last_timestamp = -1
        self.ear_values, self.mar_values = [], []
        self.yaw_values, self.pitch_values, self.roll_values = [], [], []
        self.finished = False

    @property
    def sample_count(self):
        return len(self.ear_values)

    def start(self, now=None):
        self.started_at = time.monotonic() if now is None else now

    def update(self, frame, face_landmarker, now=None):
        now = time.monotonic() if now is None else now
        if self.started_at is None:
            self.start(now)
        elapsed = now - self.started_at
        if elapsed >= self.duration:
            self.finished = True
            return {
                "elapsed": self.duration,
                "remaining": 0.0,
                "duration": self.duration,
                "sample_count": self.sample_count,
                "face_detected": False,
            }

        height, width = frame.shape[:2]
        timestamp = max(self.last_timestamp + 1, int(now * 1000))
        self.last_timestamp = timestamp
        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
        )
        result = face_landmarker.detect_for_video(image, timestamp)
        metrics = {
            "ear": self.ear_values[-1] if self.ear_values else None,
            "mar": self.mar_values[-1] if self.mar_values else None,
            "yaw": self.yaw_values[-1] if self.yaw_values else None,
            "pitch": self.pitch_values[-1] if self.pitch_values else None,
            "roll": self.roll_values[-1] if self.roll_values else None,
        }
        if result.face_landmarks:
            landmarks = result.face_landmarks[0]
            pose = calculate_head_pose(landmarks, width, height)
            if pose and now - self.last_sample_at >= self.sample_interval:
                ear = (
                    calculate_ear(landmarks, LEFT_EYE, width, height)
                    + calculate_ear(landmarks, RIGHT_EYE, width, height)
                ) / 2.0
                mar = calculate_mar(landmarks, width, height)
                yaw, pitch, roll = pose
                self.ear_values.append(ear)
                self.mar_values.append(mar)
                self.yaw_values.append(yaw)
                self.pitch_values.append(pitch)
                self.roll_values.append(roll)
                metrics["ear"] = ear
                metrics["mar"] = mar
                metrics["yaw"], metrics["pitch"], metrics["roll"] = pose
                self.last_sample_at = now
        return {
            **metrics,
            "elapsed": elapsed,
            "remaining": max(0.0, self.duration - elapsed),
            "duration": self.duration,
            "sample_count": self.sample_count,
            "face_detected": bool(result.face_landmarks),
        }

    def build_baseline(self, minimum_samples=10):
        if not self.finished or self.sample_count < minimum_samples:
            return None
        filtered_counts = {
            "ear": len(robust_filter(self.ear_values)),
            "mar": len(robust_filter(self.mar_values)),
            "head_pose": len(robust_filter(self.yaw_values)),
        }
        if min(filtered_counts.values()) < minimum_samples:
            return None
        return {
            "calibration_duration": self.duration,
            "samples": {
                "raw": self.sample_count,
                "valid_ear": filtered_counts["ear"],
                "valid_mar": filtered_counts["mar"],
                "valid_head_pose": filtered_counts["head_pose"],
            },
            "ear": calculate_statistics(self.ear_values),
            "mar": calculate_statistics(self.mar_values),
            "head_pose": {
                "yaw": calculate_statistics(self.yaw_values),
                "pitch": calculate_statistics(self.pitch_values),
                "roll": calculate_statistics(self.roll_values),
            },
        }

    def save(self, path=BASELINE_PATH, minimum_samples=10):
        baseline = self.build_baseline(minimum_samples)
        if baseline is None:
            return False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(baseline, file, indent=4, ensure_ascii=False)
        os.replace(temporary, path)
        return True


def main():
    """Backward-compatible standalone calibration entry point."""
    print("Use AI.app.main to run the application.")


if __name__ == "__main__":
    main()
