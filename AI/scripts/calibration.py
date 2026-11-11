"""Reusable face calibration primitives.

This module deliberately does not open a camera or create a MediaPipe task when
it is imported.  The application owns those resources and passes frames here.
"""

from dataclasses import dataclass
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
import zipfile

import cv2
import numpy as np


def arm64_lacks_aes() -> bool:
    """Kiểm tra nhanh (<0.1ms) xem CPU ARM64 trên Linux (ví dụ BCM2711 Cortex-A72 của Pi 4)
    có thiếu tập lệnh phần cứng AES hay không để tránh lỗi SIGILL khi nạp libmediapipe.so."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        cpuinfo = Path("/proc/cpuinfo")
        if not cpuinfo.exists():
            return False
        text = cpuinfo.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.lower().startswith("features"):
                feats = set(line.split(":", 1)[-1].lower().split())
                return "aes" not in feats
    except Exception:
        pass
    return False


mp = None
if not arm64_lacks_aes():
    try:
        import mediapipe as mp
    except ImportError:
        mp = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = PROJECT_ROOT / "AI" / "models" / "face_landmarker.task"
BASELINE_PATH = PROJECT_ROOT / "AI" / "calibration" / "baseline.json"
CALIBRATION_SECONDS = float(
    os.getenv("CALIBRATION_SECONDS", os.getenv("MEDIAPIPE_PHASE_SECONDS", "12.0"))
)
SAMPLE_INTERVAL = 0.1


def make_mp_image(bgr_frame, landmarker=None):
    """Chuyển khung hình BGR sang định dạng đầu vào cho FaceLandmarker (mp.Image hoặc RGB ndarray)."""
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    if mp is not None and not getattr(landmarker, "_is_opencv_tflite", False):
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    return rgb


@dataclass(slots=True)
class _NormalizedLandmark:
    x: float
    y: float
    z: float = 0.0


@dataclass(slots=True)
class _FaceLandmarkerResult:
    face_landmarks: list


class OpenCvTaskFaceLandmarker:
    """Bộ thực thi trực tiếp gói `face_landmarker.task` của MediaPipe bằng OpenCV TFLite DNN.

    Giải quyết triệt để hạn chế tập lệnh `+aes` (SIGILL) của `libmediapipe.so` trên
    Raspberry Pi 4 (ARM Cortex-A72) bằng cách trích xuất trực tiếp 2 đồ thị TFLite chuẩn
    (`face_detector.tflite` 128x128 và `face_landmarks_detector.tflite` 256x256 - 478 điểm 3D)
    nằm bên trong file `face_landmarker.task` và chạy qua `cv2.dnn.readNetFromTFLite`.
    Đầu ra tương thích 100% với `mp.tasks.vision.FaceLandmarker.detect_for_video()`.
    """

    _is_opencv_tflite = True

    def __init__(self, task_path=MODEL_PATH, min_face_score: float = 0.50, min_presence_score: float = 0.50):
        self.task_path = Path(task_path)
        if not self.task_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy face_landmarker.task: {self.task_path}")
        self.min_face_score = float(min_face_score)
        self.min_presence_score = float(min_presence_score)

        with zipfile.ZipFile(self.task_path, "r") as zf:
            det_bytes = zf.read("face_detector.tflite")
            lm_bytes = zf.read("face_landmarks_detector.tflite")

        self._det_net = cv2.dnn.readNetFromTFLite(bytearray(det_bytes))
        self._lm_net = cv2.dnn.readNetFromTFLite(bytearray(lm_bytes))
        self._anchors = self._generate_ssd_anchors()

    @staticmethod
    def _generate_ssd_anchors() -> np.ndarray:
        anchors = []
        for stride, num_anchors in ((8, 2), (16, 6)):
            grid = 128 // stride
            for y in range(grid):
                for x in range(grid):
                    cx = (x + 0.5) / grid
                    cy = (y + 0.5) / grid
                    for _ in range(num_anchors):
                        anchors.append((cx, cy))
        return np.asarray(anchors, dtype=np.float32)

    def _detect_face_roi(self, rgb: np.ndarray):
        h, w = rgb.shape[:2]
        if self._det_net is None or self._anchors is None:
            return 0, 0, w, h
        scale = min(128.0 / w, 128.0 / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(rgb, (nw, nh))
        padded = np.zeros((128, 128, 3), dtype=np.uint8)
        pad_x = (128 - nw) // 2
        pad_y = (128 - nh) // 2
        padded[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized

        inp = (padded.astype(np.float32) - 127.5) / 127.5
        blob = np.transpose(inp, (2, 0, 1))[np.newaxis, ...]
        self._det_net.setInput(blob)
        reg, clf = self._det_net.forward(["regressors", "classificators"])
        scores = 1.0 / (1.0 + np.exp(-np.clip(clf.reshape(-1), -20.0, 20.0)))
        best_idx = int(np.argmax(scores))
        if float(scores[best_idx]) < self.min_face_score:
            return None

        anchor = self._anchors[best_idx]
        r = reg.reshape(-1, 16)[best_idx]
        cx_pad = anchor[0] * 128.0 + r[0]
        cy_pad = anchor[1] * 128.0 + r[1]
        bw_pad = r[2]
        bh_pad = r[3]

        cx = (cx_pad - pad_x) / scale
        cy = (cy_pad - pad_y) / scale
        box_size = max(bw_pad, bh_pad) / scale * 1.45
        half = box_size * 0.5

        x1 = int(round(cx - half))
        y1 = int(round(cy - half))
        x2 = int(round(cx + half))
        y2 = int(round(cy + half))
        return x1, y1, max(16, x2 - x1), max(16, y2 - y1)

    @staticmethod
    def _crop_with_padding(rgb: np.ndarray, x1: int, y1: int, rw: int, rh: int) -> np.ndarray:
        h, w = rgb.shape[:2]
        x2, y2 = x1 + rw, y1 + rh
        pad_left = max(0, -x1)
        pad_top = max(0, -y1)
        pad_right = max(0, x2 - w)
        pad_bottom = max(0, y2 - h)
        if pad_left or pad_top or pad_right or pad_bottom:
            rgb_pad = cv2.copyMakeBorder(
                rgb, pad_top, pad_bottom, pad_left, pad_right,
                cv2.BORDER_CONSTANT, value=(0, 0, 0),
            )
            return rgb_pad[y1 + pad_top : y2 + pad_top, x1 + pad_left : x2 + pad_left]
        return rgb[y1:y2, x1:x2]

    def detect_for_video(self, image, timestamp_ms: int):
        if self._lm_net is None:
            return _FaceLandmarkerResult(face_landmarks=[])
        if hasattr(image, "numpy_view"):
            rgb = image.numpy_view()
        elif isinstance(image, np.ndarray):
            rgb = image
        else:
            return _FaceLandmarkerResult(face_landmarks=[])

        h, w = rgb.shape[:2]
        roi = self._detect_face_roi(rgb)
        if roi is None:
            return _FaceLandmarkerResult(face_landmarks=[])

        rx, ry, rw, rh = roi
        crop = self._crop_with_padding(rgb, rx, ry, rw, rh)
        if crop.size == 0:
            return _FaceLandmarkerResult(face_landmarks=[])

        inp256 = cv2.resize(crop, (256, 256)).astype(np.float32) / 255.0
        blob = np.transpose(inp256, (2, 0, 1))[np.newaxis, ...]
        self._lm_net.setInput(blob)
        lm_raw, presence_raw = self._lm_net.forward(["Identity", "Identity_1"])
        presence = float(1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, float(presence_raw.reshape(-1)[0]))))))
        if presence < self.min_presence_score:
            return _FaceLandmarkerResult(face_landmarks=[])

        pts = lm_raw.reshape(478, 3)
        landmarks = [
            _NormalizedLandmark(
                x=float((rx + (pt[0] / 256.0) * rw) / w),
                y=float((ry + (pt[1] / 256.0) * rh) / h),
                z=float((pt[2] / 256.0) * (rw / w)),
            )
            for pt in pts
        ]
        return _FaceLandmarkerResult(face_landmarks=[landmarks])

    def close(self):
        self._det_net = None
        self._lm_net = None
        self._anchors = None


def create_face_landmarker(model_path=MODEL_PATH, allow_opencv_fallback: bool = True):
    """Khởi tạo MediaPipe FaceLandmarker an toàn trên cả PC và Raspberry Pi 4.

    - Nếu `DISABLE_MEDIAPIPE=1`: trả về `None`.
    - Nếu CPU là ARM64 thiếu tập lệnh `+aes` (như Raspberry Pi 4 Cortex-A72) hoặc C++ MediaPipe
      không khả dụng: tự động dùng `OpenCvTaskFaceLandmarker` nạp trực tiếp các đồ thị TFLite
      trong `face_landmarker.task` (không bị lỗi SIGILL, giữ nguyên 478 điểm 3D).
    """
    if os.getenv("DISABLE_MEDIAPIPE", "0") == "1":
        return None
    model_file = Path(model_path)
    if not model_file.is_file():
        return None

    if mp is not None and not arm64_lacks_aes():
        try:
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import mediapipe as mp; "
                    f"opts = mp.tasks.vision.FaceLandmarkerOptions("
                    f"base_options=mp.tasks.BaseOptions(model_asset_path={str(model_file)!r}), "
                    f"running_mode=mp.tasks.vision.RunningMode.VIDEO, num_faces=1); "
                    "lm = mp.tasks.vision.FaceLandmarker.create_from_options(opts); lm.close()",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if probe.returncode == 0:
                options = mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(model_file)),
                    running_mode=mp.tasks.vision.RunningMode.VIDEO,
                    num_faces=1,
                )
                return mp.tasks.vision.FaceLandmarker.create_from_options(options)
        except Exception as exc:
            print(f"[MediaPipe] C++ FaceLandmarker không khả dụng ({exc}), chuyển sang OpenCV TFLite.")

    if allow_opencv_fallback:
        try:
            landmarker = OpenCvTaskFaceLandmarker(model_file)
            print(
                f"[MediaPipe] Đã nạp face_landmarker.task qua OpenCV TFLite backend "
                f"(tương thích ARM Cortex-A72 không cần +aes)."
            )
            return landmarker
        except Exception as exc:
            print(f"[MediaPipe] Không thể nạp OpenCvTaskFaceLandmarker: {exc}")
    return None

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
        image = make_mp_image(frame, face_landmarker)
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
