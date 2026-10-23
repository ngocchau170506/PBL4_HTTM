
"""YOLO driver monitoring with temporal alert confirmation and RAM buffering."""

from collections import deque
from enum import Enum
import json
import os
from pathlib import Path
import time

import cv2
import mediapipe as mp
from ultralytics import YOLO

from AI.scripts.calibration import (
    BASELINE_PATH,
    LEFT_EYE,
    MOUTH_BOTTOM,
    MOUTH_LEFT,
    MOUTH_RIGHT,
    MOUTH_TOP,
    RIGHT_EYE,
    calculate_ear,
    calculate_mar,
    validate_baseline,
)
from AI.scripts.esp_communication import EspCommunicator
from AI.scripts.event_recorder import EventRecorder
from AI.scripts.time_utils import filename_timestamp, iso_timestamp, local_now

PROJECT_ROOT = Path(__file__).resolve().parents[2]
YOLO_MODEL_PATH = PROJECT_ROOT / "AI" / "models" / "best.pt"
CONFIDENCE_THRESHOLD = 0.2
BUFFER_SIZE = 10
DROWSY_THRESHOLD_SECONDS = 2.0
YAWNING_THRESHOLD_SECONDS = 2.0

# ---------------------------------------------------------
# Hang so kiem thu va cau hinh Hybrid Two-Stage Verification
# ---------------------------------------------------------
# Mốc thời gian kích hoạt và xác nhận (giây)
MEDIAPIPE_TRIGGER_SECONDS = 1.0       # Kích hoạt MediaPipe khi YOLO nghi ngờ nhắm mắt >= 1.0s
DROWSY_CONFIRM_SECONDS = 2.0          # Mốc YOLO kết luận DROWSY (đồng bộ với drowsy_threshold)
DROWSY_FALLBACK_SECONDS = 2.5         # Giới hạn dự phòng tối đa (nếu chưa đủ bằng chứng)

YAWN_MEDIAPIPE_TRIGGER_SECONDS = 0.5  # Kích hoạt MediaPipe khi YOLO nghi ngờ ngáp >= 0.5s
YAWN_CONFIRM_SECONDS = 1.0            # Mốc YOLO kết luận YAWNING (hoặc yawning_threshold)
YAWN_FALLBACK_SECONDS = 1.3           # Giới hạn dự phòng tối đa ngáp

# Tiêu chí đánh giá chất lượng xác minh MediaPipe
MIN_VALID_SAMPLES = 5                 # Số frame MediaPipe có landmark hợp lệ tối thiểu trong window
CLOSED_VOTE_THRESHOLD = 0.70          # Tỉ lệ vote mắt nhắm hợp lệ (cấu hình thử nghiệm 0.6, 0.7, 0.8)
OPEN_VOTE_THRESHOLD = 0.70            # Tỉ lệ vote miệng mở hợp lệ

# Hệ số tính ngưỡng động từ baseline cá nhân
EAR_BASELINE_FACTOR = 0.75            # Ngưỡng nhắm mắt = mean_ear * 0.75
MAR_BASELINE_FACTOR = 1.8             # Ngưỡng ngáp = max(mean_mar * 1.8, MAR_MIN_THRESHOLD)
MAR_MIN_THRESHOLD = 0.40              # Ngưỡng ngáp tối thiểu

# Ngưỡng mặc định dự phòng khi chưa có baseline hợp lệ
DEFAULT_EAR_THRESHOLD = 0.22
DEFAULT_MAR_THRESHOLD = 0.50


class AlertState(str, Enum):
    IDLE = "IDLE"                    # Bình thường, chưa phát hiện dấu hiệu
    WATCHING = "WATCHING"            # YOLO phát hiện Eye_Closed / Mouth_Open (chờ đạt mốc kích hoạt)
    VERIFYING = "VERIFYING"          # MediaPipe được kích hoạt xác minh (1.0s - 2.0s cho mắt, 0.5s - 1.0s cho miệng)
    CONFIRMED = "CONFIRMED"          # Xác nhận cảnh báo: Cả YOLO và MediaPipe đều đồng thuận
    SUSPECTED = "SUSPECTED"          # Cảnh báo phòng ngừa: YOLO có bằng chứng mạnh nhưng MediaPipe thiếu dữ liệu
    CONFLICT = "CONFLICT"            # Mâu thuẫn: YOLO thấy mắt nhắm nhưng MediaPipe có đủ mẫu và xác nhận mắt mở
    TRACKING_LOST = "TRACKING_LOST"  # Mất theo dõi khuôn mặt hoàn toàn


def _is_drowsy(name):
    name = name.casefold().replace(" ", "_")
    return "eye_closed" in name or "eye_close" in name or "drowsy_eye" in name


def _is_yawning(name):
    name = name.casefold().replace(" ", "_")
    return "yawn" in name or "mouth_open" in name or "open_mouth" in name


class PerformanceMonitor:
    """Bộ theo dõi hiệu năng thời gian thực nhẹ (không làm chậm pipeline).
    
    Đo lường:
    - AI processing FPS (làm mượt qua EMA).
    - YOLO inference latency (ms).
    - MediaPipe inference latency (ms khi active).
    - Tổng Pipeline latency (ms).
    - Số lần kích hoạt MediaPipe (MediaPipe activations count & ratio).
    - CPU & RAM tiến trình (qua psutil nếu có).
    """

    def __init__(self, log_interval: float = 5.0, enable_logging: bool = True):
        self.log_interval = float(log_interval)
        self.enable_logging = bool(enable_logging)
        self.last_log_time = time.monotonic()

        self.frame_count = 0
        self.total_yolo_time = 0.0
        self.total_mp_time = 0.0
        self.total_pipeline_time = 0.0
        self.mp_activations_in_window = 0
        self.total_mp_activations = 0

        self._last_frame_time = None
        self.fps = 0.0

        self.yolo_latency_ms = 0.0
        self.mp_latency_ms = 0.0
        self.pipeline_latency_ms = 0.0

        try:
            import psutil
            self._process = psutil.Process()
            # Gọi cpu_percent() lần đầu để khởi tạo mốc đo
            self._process.cpu_percent()
        except Exception:
            self._process = None

    def record_frame(
        self,
        yolo_time_s: float,
        mp_time_s: float,
        pipeline_time_s: float,
        mp_active: bool,
    ):
        now = time.monotonic()
        self.frame_count += 1
        self.total_yolo_time += yolo_time_s
        self.total_mp_time += mp_time_s
        self.total_pipeline_time += pipeline_time_s

        self.yolo_latency_ms = round(yolo_time_s * 1000.0, 2)
        self.mp_latency_ms = round(mp_time_s * 1000.0, 2)
        self.pipeline_latency_ms = round(pipeline_time_s * 1000.0, 2)

        if mp_active:
            self.mp_activations_in_window += 1
            self.total_mp_activations += 1

        if self._last_frame_time is not None:
            delta = now - self._last_frame_time
            if delta > 0:
                instant_fps = 1.0 / delta
                self.fps = round(self.fps * 0.9 + instant_fps * 0.1, 1) if self.fps > 0 else round(instant_fps, 1)
        self._last_frame_time = now

        if self.enable_logging and (now - self.last_log_time >= self.log_interval):
            self._print_summary(now)

    def _print_summary(self, now: float):
        elapsed = now - self.last_log_time
        if self.frame_count > 0:
            avg_yolo = (self.total_yolo_time / self.frame_count) * 1000.0
            avg_pipeline = (self.total_pipeline_time / self.frame_count) * 1000.0
            avg_mp = (
                (self.total_mp_time / self.mp_activations_in_window) * 1000.0
                if self.mp_activations_in_window > 0
                else 0.0
            )
            calc_fps = self.frame_count / elapsed if elapsed > 0 else 0.0

            cpu_info = ""
            ram_info = ""
            if self._process:
                try:
                    cpu_percent = self._process.cpu_percent()
                    ram_mb = self._process.memory_info().rss / (1024 * 1024)
                    cpu_info = f" | CPU: {cpu_percent:.1f}%"
                    ram_info = f" | RAM: {ram_mb:.0f}MB"
                except Exception:
                    pass

            mp_ratio = (self.mp_activations_in_window / self.frame_count) * 100.0
            print(
                f"[PERF] FPS: {calc_fps:.1f} | Pipeline: {avg_pipeline:.1f}ms "
                f"| YOLO: {avg_yolo:.1f}ms | MediaPipe: {avg_mp:.1f}ms "
                f"(active {self.mp_activations_in_window}/{self.frame_count} frames, {mp_ratio:.1f}%)"
                f"{cpu_info}{ram_info}"
            )

        self.last_log_time = now
        self.frame_count = 0
        self.total_yolo_time = 0.0
        self.total_mp_time = 0.0
        self.total_pipeline_time = 0.0
        self.mp_activations_in_window = 0


class DrowsinessPipeline:
    def __init__(
        self,
        model_path=YOLO_MODEL_PATH,
        esp_communicator=None,
        drowsy_threshold=DROWSY_THRESHOLD_SECONDS,
        yawning_threshold=YAWNING_THRESHOLD_SECONDS,
        evidence_dir=None,
        save_evidence=True,
        face_landmarker=None,
        baseline_path=None,
        enable_perf_log=True,
        perf_log_interval=5.0,
        event_storage_dir=None,
        enable_event_recorder=True,
        event_recorder=None,
    ):
        model_path = Path(model_path)

        if not model_path.is_file():
            raise FileNotFoundError(
                f"Khong tim thay YOLO model: {model_path}"
            )

        self.model = YOLO(str(model_path))
        self.frame_buffer = deque(maxlen=BUFFER_SIZE)
        self.esp = esp_communicator or EspCommunicator()
        self.drowsy_threshold = float(drowsy_threshold)
        self.yawning_threshold = float(yawning_threshold)
        self._drowsy_started = None
        self._yawning_started = None
        self._drowsy_started_at = None
        self._yawning_started_at = None
        self._confirmed_alert = None
        self.monitoring_started_at = local_now()
        self._monitoring_started_monotonic = time.monotonic()
        self.evidence_dir = Path(
            evidence_dir or os.getenv(
                "EVIDENCE_DIR", str(PROJECT_ROOT / "AI" / "evidence")
            )
        )
        self.save_evidence = bool(save_evidence)
        self.metadata_path = self.evidence_dir / "events.jsonl"
        self.last_event = None

        # MediaPipe Face Landmarker (tái sử dụng từ giai đoạn calibration,
        # không khởi tạo lại trong vòng lặp).
        # Pipeline nhận ownership: close() sẽ giải phóng instance này.
        self._landmarker = face_landmarker
        # Timestamp tăng đơn điệu cho MediaPipe VIDEO mode.
        # Khởi tạo từ thời gian thực để đảm bảo lớn hơn timestamp calibration.
        self._mp_last_timestamp: int = int(time.monotonic() * 1000)
        print(
            f"[Pipeline] MediaPipe landmarker: "
            f"{'SAN SANG (tái sử dụng)' if self._landmarker else 'KHONG CO (chi dung YOLO)'}"
        )

        # Baseline cá nhân và ngưỡng động EAR / MAR
        self.baseline_path = Path(baseline_path or BASELINE_PATH)
        self.baseline = None
        self.baseline_valid = False
        self.ear_threshold = DEFAULT_EAR_THRESHOLD
        self.mar_threshold = DEFAULT_MAR_THRESHOLD
        self._load_and_compute_thresholds()

        # State machine độc lập cho Drowsiness (Nhắm mắt)
        self.drowsy_state = AlertState.IDLE
        self._drowsy_votes = []          # List bool: True nếu EAR < ear_threshold (mắt nhắm)
        self._drowsy_metrics = []        # List tuple: (monotonic_time, ear)
        self._drowsy_conflict_count = 0  # Đếm số lần mâu thuẫn liên tiếp

        # State machine độc lập cho Yawning (Ngáp)
        self.yawning_state = AlertState.IDLE
        self._yawning_votes = []         # List bool: True nếu MAR > mar_threshold (miệng mở)
        self._yawning_metrics = []       # List tuple: (monotonic_time, mar)
        self._yawning_conflict_count = 0

        # Quản lý cảnh báo ESP và chống gửi trùng lặp
        self._active_esp_alert = None    # "DROWSY", "YAWNING", hoặc None
        self._alert_recorded = False     # Đã lưu ảnh & events.jsonl cho sự kiện này chưa
        self._conflict_recorded = False  # Đã ghi nhận log CONFLICT chưa

        # Bộ theo dõi hiệu năng thời gian thực (realtime performance monitor)
        self.perf_monitor = PerformanceMonitor(
            log_interval=float(os.getenv("PERF_LOG_INTERVAL", perf_log_interval)),
            enable_logging=bool(enable_perf_log),
        )
        self.event_recorder = event_recorder
        if self.event_recorder is None and enable_event_recorder:
            self.event_recorder = EventRecorder(
                event_storage_dir or str(PROJECT_ROOT / "AI" / "data"),
            )

    def _update_drowsy_state(self, instantaneous_drowsy: bool, duration: float) -> AlertState:
        """Cập nhật máy trạng thái phát hiện buồn ngủ (Drowsiness State Machine)."""
        if not instantaneous_drowsy:
            self.drowsy_state = AlertState.IDLE
            self._drowsy_votes.clear()
            self._drowsy_metrics.clear()
            return self.drowsy_state

        if duration < MEDIAPIPE_TRIGGER_SECONDS:
            self.drowsy_state = AlertState.WATCHING
        elif MEDIAPIPE_TRIGGER_SECONDS <= duration < self.drowsy_threshold:
            if self.drowsy_state == AlertState.WATCHING:
                # Bắt đầu vào window xác minh 1s -> 2s, làm mới danh sách vote
                self._drowsy_votes.clear()
                self._drowsy_metrics.clear()
            self.drowsy_state = AlertState.VERIFYING
        return self.drowsy_state

    def _update_yawning_state(self, instantaneous_yawning: bool, duration: float) -> AlertState:
        """Cập nhật máy trạng thái phát hiện ngáp (Yawning State Machine)."""
        if not instantaneous_yawning:
            self.yawning_state = AlertState.IDLE
            self._yawning_votes.clear()
            self._yawning_metrics.clear()
            return self.yawning_state

        if duration < YAWN_MEDIAPIPE_TRIGGER_SECONDS:
            self.yawning_state = AlertState.WATCHING
        elif YAWN_MEDIAPIPE_TRIGGER_SECONDS <= duration < self.yawning_threshold:
            if self.yawning_state == AlertState.WATCHING:
                self._yawning_votes.clear()
                self._yawning_metrics.clear()
            self.yawning_state = AlertState.VERIFYING
        return self.yawning_state

    def _run_mediapipe(self, frame, monotonic_time: float) -> dict:
        """Thực thi suy luận MediaPipe Face Landmarker có điều kiện trên frame.
        
        Chỉ được gọi khi hệ thống ở trạng thái VERIFYING.
        Bảo đảm timestamp tăng đơn điệu theo quy chuẩn MediaPipe VIDEO mode.
        """
        if self._landmarker is None:
            return {
                "valid": False, "ear": None, "mar": None,
                "timestamp": monotonic_time, "reason": "NO_LANDMARKER"
            }

        height, width = frame.shape[:2]
        timestamp_ms = max(self._mp_last_timestamp + 1, int(monotonic_time * 1000))
        self._mp_last_timestamp = timestamp_ms

        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
        )
        try:
            result = self._landmarker.detect_for_video(image, timestamp_ms)
        except Exception as exc:
            return {
                "valid": False, "ear": None, "mar": None,
                "timestamp": monotonic_time, "reason": f"ERROR: {exc}"
            }

        if not result.face_landmarks:
            return {
                "valid": False, "ear": None, "mar": None,
                "timestamp": monotonic_time, "reason": "NO_FACE"
            }

        landmarks = result.face_landmarks[0]
        left_ear = calculate_ear(landmarks, LEFT_EYE, width, height)
        right_ear = calculate_ear(landmarks, RIGHT_EYE, width, height)
        ear = (left_ear + right_ear) / 2.0
        mar = calculate_mar(landmarks, width, height)

        # Lọc mẫu INVALID: khoảng cách ngang bằng 0 hoặc landmark bất thường
        if ear <= 0.01:
            return {
                "valid": False, "ear": None, "mar": None,
                "timestamp": monotonic_time, "reason": "DEGENERATE_LANDMARKS"
            }

        return {
            "valid": True,
            "ear": round(ear, 4),
            "mar": round(mar, 4),
            "timestamp": monotonic_time,
            "reason": "OK",
        }

    def _evaluate_drowsy_decision(
        self, duration: float, valid_samples: int, vote_ratio: float, instantaneous: bool
    ) -> AlertState:
        """Đánh giá quyết định Drowsy tại mốc 2.0s và dự phòng tối đa 2.5s."""
        if not instantaneous or duration <= 0:
            return AlertState.IDLE

        if duration < MEDIAPIPE_TRIGGER_SECONDS:
            return AlertState.WATCHING

        # Đã đạt từ 2.0s trở lên: kiểm tra điều kiện CONFIRMED ngay
        # (Nếu đạt ở 2.0s, 2.1s, 2.3s thì kết luận cảnh báo ngay)
        if duration >= DROWSY_CONFIRM_SECONDS:
            if valid_samples >= MIN_VALID_SAMPLES and vote_ratio >= CLOSED_VOTE_THRESHOLD:
                return AlertState.CONFIRMED

            # Nếu đã từng CONFIRMED trong đợt này, duy trì cho đến khi mở mắt
            if self.drowsy_state == AlertState.CONFIRMED:
                return AlertState.CONFIRMED

            # Nếu chưa đủ điều kiện và vẫn trong cửa sổ dự phòng (2.0s <= t < 2.5s):
            if duration < DROWSY_FALLBACK_SECONDS:
                return AlertState.VERIFYING

            # Đã vượt quá 2.5s mà vẫn chưa thể CONFIRMED -> Xử lý 3 nhánh UNCERTAIN
            # Nhánh 2: Mâu thuẫn (MediaPipe có đủ mẫu nhưng vote ratio < 0.70)
            if valid_samples >= MIN_VALID_SAMPLES and vote_ratio < CLOSED_VOTE_THRESHOLD:
                if self.drowsy_state != AlertState.CONFLICT:
                    self._drowsy_conflict_count += 1
                return AlertState.CONFLICT

            # Nhánh 1: YOLO có bằng chứng mạnh liên tục >= 2.5s nhưng MediaPipe thiếu dữ liệu
            return AlertState.SUSPECTED

        return AlertState.VERIFYING

    def _evaluate_yawning_decision(
        self, duration: float, valid_samples: int, vote_ratio: float, instantaneous: bool
    ) -> AlertState:
        """Đánh giá quyết định Yawning tại mốc 1.0s và dự phòng tối đa 1.3s."""
        if not instantaneous or duration <= 0:
            return AlertState.IDLE

        if duration < YAWN_MEDIAPIPE_TRIGGER_SECONDS:
            return AlertState.WATCHING

        if duration >= YAWN_CONFIRM_SECONDS:
            if valid_samples >= MIN_VALID_SAMPLES and vote_ratio >= OPEN_VOTE_THRESHOLD:
                return AlertState.CONFIRMED

            if self.yawning_state == AlertState.CONFIRMED:
                return AlertState.CONFIRMED

            if duration < YAWN_FALLBACK_SECONDS:
                return AlertState.VERIFYING

            if valid_samples >= MIN_VALID_SAMPLES and vote_ratio < OPEN_VOTE_THRESHOLD:
                if self.yawning_state != AlertState.CONFLICT:
                    self._yawning_conflict_count += 1
                return AlertState.CONFLICT

            return AlertState.SUSPECTED

        return AlertState.VERIFYING

    def _load_and_compute_thresholds(self):
        """Doc baseline.json, validate va tinh toan nguong EAR/MAR dong."""
        validated = validate_baseline(self.baseline_path, minimum_samples=MIN_VALID_SAMPLES)
        if validated is not None:
            self.baseline = validated
            self.baseline_valid = True
            ear_mean = float(validated["ear"]["mean"])
            mar_mean = float(validated["mar"]["mean"])
            self.ear_threshold = round(ear_mean * EAR_BASELINE_FACTOR, 4)
            self.mar_threshold = round(max(mar_mean * MAR_BASELINE_FACTOR, MAR_MIN_THRESHOLD), 4)
            print(
                f"[Pipeline] Baseline hop le ({self.baseline_path.name}): "
                f"EAR mean={ear_mean:.4f} -> threshold={self.ear_threshold:.4f} (x{EAR_BASELINE_FACTOR}); "
                f"MAR mean={mar_mean:.4f} -> threshold={self.mar_threshold:.4f}"
            )
        else:
            self.baseline = None
            self.baseline_valid = False
            self.ear_threshold = DEFAULT_EAR_THRESHOLD
            self.mar_threshold = DEFAULT_MAR_THRESHOLD
            print(
                f"[Pipeline] CANH BAO: Baseline khong hop le hoac chua du mau tai {self.baseline_path}. "
                f"Su dung nguong mac dinh: EAR={self.ear_threshold}, MAR={self.mar_threshold}"
            )

    def process(
        self,
        frame,
        frame_id=None,
        capture_timestamp_ns=None,
        capture_time_utc=None,
    ):
        t_pipeline_start = time.perf_counter()
        monotonic_time = (
            capture_timestamp_ns / 1_000_000_000
            if capture_timestamp_ns is not None else time.monotonic()
        )
        wall_time = local_now()

        # Đo latency suy luận YOLO
        t_yolo_start = time.perf_counter()
        results = self.model.predict(
            source=frame,
            conf=CONFIDENCE_THRESHOLD,
            verbose=False,
        )
        t_yolo_end = time.perf_counter()
        yolo_time_s = t_yolo_end - t_yolo_start

        rendered = results[0].plot()
        detected_classes = []
        detections = []

        for box in results[0].boxes:
            class_id = int(box.cls[0].item())
            class_name = self.model.names[class_id]
            detected_classes.append(class_name)
            detections.append((class_name, float(box.conf[0].item())))

        instantaneous_drowsy = any(_is_drowsy(name) for name in detected_classes)
        instantaneous_yawning = any(_is_yawning(name) for name in detected_classes)
        drowsy_duration = self._update_timer(
            instantaneous_drowsy, "_drowsy_started", monotonic_time,
            "_drowsy_started_at", wall_time,
        )
        yawning_duration = self._update_timer(
            instantaneous_yawning, "_yawning_started", monotonic_time,
            "_yawning_started_at", wall_time,
        )

        if self.event_recorder is not None:
            if instantaneous_drowsy and drowsy_duration == 0.0:
                self.event_recorder.start_event(
                    "DROWSY",
                    capture_timestamp_ns or time.monotonic_ns(),
                    capture_time_utc or iso_timestamp(self._drowsy_started_at or wall_time),
                )
            if instantaneous_yawning and yawning_duration == 0.0:
                self.event_recorder.start_event(
                    "YAWNING",
                    capture_timestamp_ns or time.monotonic_ns(),
                    capture_time_utc or iso_timestamp(self._yawning_started_at or wall_time),
                )

        # Cap nhat State Machine
        self._update_drowsy_state(instantaneous_drowsy, drowsy_duration)
        self._update_yawning_state(instantaneous_yawning, yawning_duration)

        # Kich hoat MediaPipe co dieu kien khi o trang thai VERIFYING (Event-triggered)
        mp_active = (
            self.drowsy_state == AlertState.VERIFYING
            or self.yawning_state == AlertState.VERIFYING
        )
        mp_result = None
        t_mp_start = time.perf_counter()
        if mp_active:
            mp_result = self._run_mediapipe(frame, monotonic_time)

            # Tich luy vote cho Drowsiness trong window [1.0s, 2.0s]
            if self.drowsy_state == AlertState.VERIFYING and mp_result["valid"]:
                is_closed = mp_result["ear"] < self.ear_threshold
                self._drowsy_votes.append(is_closed)
                self._drowsy_metrics.append((monotonic_time, mp_result["ear"]))

            # Tich luy vote cho Yawning trong window [0.5s, 1.0s]
            if self.yawning_state == AlertState.VERIFYING and mp_result["valid"]:
                is_open = mp_result["mar"] > self.mar_threshold
                self._yawning_votes.append(is_open)
                self._yawning_metrics.append((monotonic_time, mp_result["mar"]))
        t_mp_end = time.perf_counter()
        mp_time_s = (t_mp_end - t_mp_start) if mp_active else 0.0

        drowsy_valid_samples = len(self._drowsy_votes)
        drowsy_vote_ratio = (
            sum(self._drowsy_votes) / drowsy_valid_samples
            if drowsy_valid_samples > 0
            else 0.0
        )
        yawning_valid_samples = len(self._yawning_votes)
        yawning_vote_ratio = (
            sum(self._yawning_votes) / yawning_valid_samples
            if yawning_valid_samples > 0
            else 0.0
        )

        # ---------------------------------------------------------------------
        # Giai doan 3 & 4: Danh gia quyet dinh tai 2.0s va du phong toi da 2.5s
        # ---------------------------------------------------------------------
        self.drowsy_state = self._evaluate_drowsy_decision(
            duration=drowsy_duration,
            valid_samples=drowsy_valid_samples,
            vote_ratio=drowsy_vote_ratio,
            instantaneous=instantaneous_drowsy,
        )
        self.yawning_state = self._evaluate_yawning_decision(
            duration=yawning_duration,
            valid_samples=yawning_valid_samples,
            vote_ratio=yawning_vote_ratio,
            instantaneous=instantaneous_yawning,
        )

        if self.event_recorder is not None:
            event_timestamp_ns = capture_timestamp_ns or time.monotonic_ns()
            event_metadata = {
                "yolo_status": (
                    "DROWSY" if instantaneous_drowsy
                    else "YAWNING" if instantaneous_yawning else "NORMAL"
                ),
                "yolo_detections": [
                    {"label": name, "confidence": confidence}
                    for name, confidence in detections
                ],
                "yolo_confidence": max((confidence for _, confidence in detections), default=None),
                "confidence": max((confidence for _, confidence in detections), default=None),
                "mediapipe_valid": (
                    bool(mp_result.get("valid")) if mp_result is not None else None
                ),
                "ear": mp_result.get("ear") if mp_result else None,
                "mar": mp_result.get("mar") if mp_result else None,
            }
            for event_type, active, local_state, confirmed in (
                ("DROWSY", instantaneous_drowsy, self.drowsy_state.value,
                 self.drowsy_state in {
                     AlertState.CONFIRMED, AlertState.SUSPECTED, AlertState.CONFLICT
                 }),
                ("YAWNING", instantaneous_yawning, self.yawning_state.value,
                 self.yawning_state in {
                     AlertState.CONFIRMED, AlertState.SUSPECTED, AlertState.CONFLICT
                 }),
            ):
                self.event_recorder.update_event(
                    event_type,
                    event_timestamp_ns,
                    local_state,
                    confirmed,
                    {
                        "frame_id": frame_id,
                        **event_metadata,
                        "yolo_active": active,
                        "local_state": local_state,
                        "face_valid": (
                            bool(mp_result.get("valid"))
                            if mp_result is not None else None
                        ),
                    },
                    confirmed_at_ns=event_timestamp_ns if confirmed else None,
                    confirmed_at_time=capture_time_utc if confirmed else None,
                )
                if not active:
                    self.event_recorder.end_detection(event_type, event_timestamp_ns)

        # Xac dinh loai canh bao va trang thai ESP can gui
        confirmed_alert = "NORMAL"
        detailed_alert = None
        esp_target = None
        alert_duration = 0.0
        event_start = None

        if self.drowsy_state == AlertState.CONFIRMED:
            confirmed_alert = "DROWSY"
            detailed_alert = "DROWSY_ALERT"
            esp_target = "DROWSY"
            alert_duration = drowsy_duration
            event_start = self._drowsy_started_at
        elif self.drowsy_state == AlertState.SUSPECTED:
            confirmed_alert = "SUSPECTED"
            detailed_alert = "SUSPECTED_ALERT"
            esp_target = "DROWSY"  # Mapped thanh DROWSY khi gui ESP theo quyet dinh thiet ke
            alert_duration = drowsy_duration
            event_start = self._drowsy_started_at
        elif self.yawning_state == AlertState.CONFIRMED:
            confirmed_alert = "YAWNING"
            detailed_alert = "YAWNING_ALERT"
            esp_target = "YAWNING"
            alert_duration = yawning_duration
            event_start = self._yawning_started_at
        elif self.yawning_state == AlertState.SUSPECTED:
            confirmed_alert = "SUSPECTED"
            detailed_alert = "SUSPECTED_YAWN_ALERT"
            esp_target = "YAWNING"  # Mapped thanh YAWNING khi gui ESP
            alert_duration = yawning_duration
            event_start = self._yawning_started_at
        elif self.drowsy_state == AlertState.CONFLICT or self.yawning_state == AlertState.CONFLICT:
            confirmed_alert = "CONFLICT"
            detailed_alert = "CONFLICT"
            esp_target = None  # Khong gui ESP khi mau thuan
            alert_duration = max(drowsy_duration, yawning_duration)
            event_start = self._drowsy_started_at or self._yawning_started_at
        elif self.drowsy_state == AlertState.TRACKING_LOST or self.yawning_state == AlertState.TRACKING_LOST:
            confirmed_alert = "TRACKING_LOST"
            detailed_alert = "TRACKING_LOST"
            esp_target = None  # Khong gui ESP khi mat tracking
            alert_duration = 0.0
        else:
            confirmed_alert = "NORMAL"
            detailed_alert = None
            alert_duration = 0.0
            # Chi gui NORMAL len ESP neu truoc do dang co canh bao active tren ESP
            if self._active_esp_alert is not None:
                esp_target = "NORMAL"
            else:
                esp_target = None

        esp_result = None
        confirmed_at = iso_timestamp(wall_time)

        # 1. Truyen tin hieu sang ESP (chi gui khi chuyen trang thai, khong lap lai moi frame)
        if esp_target is not None and esp_target != self._active_esp_alert:
            esp_result = self.esp.send_status(esp_target, alert_duration, confirmed_at)
            self._active_esp_alert = esp_target if esp_target != "NORMAL" else None

        # 2. Ghi nhan bang chung (anh JPG + events.jsonl)
        if detailed_alert in ("DROWSY_ALERT", "SUSPECTED_ALERT", "YAWNING_ALERT", "SUSPECTED_YAWN_ALERT"):
            if not self._alert_recorded:
                relevant_detections = [
                    conf for name, conf in detections
                    if (
                        _is_drowsy(name)
                        if "DROWSY" in detailed_alert or detailed_alert == "SUSPECTED_ALERT"
                        else _is_yawning(name)
                    )
                ]
                self.last_event = self._record_event(
                    frame=frame,
                    alert_type=detailed_alert,
                    started_at=event_start or wall_time,
                    confirmed_at=wall_time,
                    duration_seconds=alert_duration,
                    confidence=max(relevant_detections, default=None),
                )
                self._alert_recorded = True
        elif detailed_alert == "CONFLICT":
            if not self._conflict_recorded:
                relevant_detections = [
                    conf for name, conf in detections if _is_drowsy(name) or _is_yawning(name)
                ]
                self.last_event = self._record_event(
                    frame=frame,
                    alert_type="CONFLICT",
                    started_at=event_start or wall_time,
                    confirmed_at=wall_time,
                    duration_seconds=alert_duration,
                    confidence=max(relevant_detections, default=None),
                )
                self._conflict_recorded = True
        elif detailed_alert is None:
            self._alert_recorded = False
            self._conflict_recorded = False

        self._confirmed_alert = confirmed_alert

        state = {
            "timestamp": iso_timestamp(wall_time),
            "monotonic_time": monotonic_time,
            "monitoring_started_at": iso_timestamp(self.monitoring_started_at),
            "monitoring_duration_seconds": (
                monotonic_time - self._monitoring_started_monotonic
            ),
            "detected_classes": detected_classes,
            "detections": detections,
            "instantaneous_status": (
                "DROWSY" if instantaneous_drowsy
                else "YAWNING" if instantaneous_yawning
                else "NORMAL"
            ),
            "confirmed_alert": confirmed_alert,
            "detailed_alert": detailed_alert,
            "drowsy_duration": drowsy_duration,
            "yawning_duration": yawning_duration,
            "eye_duration": drowsy_duration,
            "mouth_duration": yawning_duration,
            "eye_threshold_seconds": self.drowsy_threshold,
            "yawn_threshold_seconds": self.yawning_threshold,
            "drowsy_state": self.drowsy_state.value,
            "yawning_state": self.yawning_state.value,
            "drowsy_votes_count": len(self._drowsy_votes),
            "yawning_votes_count": len(self._yawning_votes),
            "drowsy_valid_samples": drowsy_valid_samples,
            "drowsy_vote_ratio": round(drowsy_vote_ratio, 3),
            "yawning_valid_samples": yawning_valid_samples,
            "yawning_vote_ratio": round(yawning_vote_ratio, 3),
            "mp_active": mp_active,
            "mp_ear": mp_result["ear"] if (mp_result and mp_result["valid"]) else None,
            "mp_mar": mp_result["mar"] if (mp_result and mp_result["valid"]) else None,
            "baseline_valid": self.baseline_valid,
            "ear_threshold": self.ear_threshold,
            "mar_threshold": self.mar_threshold,
            "status": ", ".join(detected_classes) if detected_classes else "Chua phat hien doi tuong",
            "last_event": self.last_event,
            "ai_fps": self.perf_monitor.fps,
            "yolo_latency_ms": self.perf_monitor.yolo_latency_ms,
            "mp_latency_ms": self.perf_monitor.mp_latency_ms,
            "pipeline_latency_ms": self.perf_monitor.pipeline_latency_ms,
            "mp_total_activations": self.perf_monitor.total_mp_activations,
        }
        self.frame_buffer.append({
            "timestamp": iso_timestamp(wall_time),
            "monotonic_time": monotonic_time,
            "frame": frame.copy(),
            "detections": tuple(detections),
            "instantaneous_status": state["instantaneous_status"],
            "confirmed_alert": confirmed_alert,
            "detailed_alert": detailed_alert,
        })
        state["buffer_count"] = len(self.frame_buffer)
        state["buffer_capacity"] = self.frame_buffer.maxlen
        state["esp_mode"] = self.esp.mode_label
        state["esp_connected"] = self.esp.connected
        state["esp_result"] = esp_result or self.esp.last_result

        # Do tong thoi gian chay pipeline tren frame
        t_pipeline_end = time.perf_counter()
        pipeline_time_s = t_pipeline_end - t_pipeline_start

        self.perf_monitor.record_frame(
            yolo_time_s=yolo_time_s,
            mp_time_s=mp_time_s,
            pipeline_time_s=pipeline_time_s,
            mp_active=mp_active,
        )
        state["ai_fps"] = self.perf_monitor.fps
        state["yolo_latency_ms"] = self.perf_monitor.yolo_latency_ms
        state["mp_latency_ms"] = self.perf_monitor.mp_latency_ms
        state["pipeline_latency_ms"] = self.perf_monitor.pipeline_latency_ms
        state["mp_total_activations"] = self.perf_monitor.total_mp_activations

        return rendered, state

    def _update_timer(
        self, active, attribute, now, wall_attribute, wall_now
    ):
        started = getattr(self, attribute)
        if active:
            if started is None:
                started = now
                setattr(self, attribute, started)
                setattr(self, wall_attribute, wall_now)
            return now - started
        setattr(self, attribute, None)
        setattr(self, wall_attribute, None)
        return 0.0

    def _record_event(
        self, frame, alert_type, started_at, confirmed_at,
        duration_seconds, confidence,
    ):
        evidence_file = None
        if self.save_evidence:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence_file = (
                f"{alert_type}_{filename_timestamp(confirmed_at)}.jpg"
            )
            evidence_path = self.evidence_dir / evidence_file
            if not cv2.imwrite(str(evidence_path), frame):
                raise RuntimeError(f"Khong the luu bang chung: {evidence_path}")

        event = {
            "timestamp": iso_timestamp(confirmed_at),
            "event_started_at": iso_timestamp(started_at),
            "confirmed_at": iso_timestamp(confirmed_at),
            "alert_type": alert_type,
            "duration_seconds": round(max(0.0, duration_seconds), 3),
            "confidence": confidence,
            "evidence_file": evidence_file,
        }
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        with self.metadata_path.open("a", encoding="utf-8") as metadata_file:
            metadata_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def close(self):
        if self.event_recorder is not None:
            self.event_recorder.close()
            self.event_recorder = None
        self.esp.close()
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
            finally:
                self._landmarker = None
