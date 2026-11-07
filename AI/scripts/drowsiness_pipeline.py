
"""YOLO driver monitoring with temporal alert confirmation and RAM buffering."""

from enum import Enum
import json
import os
from pathlib import Path
import time

import cv2
try:
    import mediapipe as mp
except ImportError:
    mp = None
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
    calculate_head_pose,
    calculate_mar,
    validate_baseline,
)
from AI.scripts.esp_communication import EspCommunicator
from AI.scripts.sensor_fusion import SensorFusion
from AI.scripts.telemetry import Telemetry
from AI.scripts.event_recorder import EventRecorder
from AI.scripts.time_utils import filename_timestamp, iso_timestamp, local_now

PROJECT_ROOT = Path(__file__).resolve().parents[2]
YOLO_MODEL_PATH = PROJECT_ROOT / "AI" / "models" / "best.pt"
CONFIDENCE_THRESHOLD = 0.2
BUFFER_SIZE = 10
DROWSY_THRESHOLD_SECONDS = 2.0
YAWNING_THRESHOLD_SECONDS = 1.0

# Minimum valid baseline samples; decisions use independent temporal evidence.
MIN_VALID_SAMPLES = 5

# Hệ số tính ngưỡng động từ baseline cá nhân
EAR_BASELINE_FACTOR = 0.75            # Ngưỡng nhắm mắt = mean_ear * 0.75
MAR_BASELINE_FACTOR = 1.8             # Ngưỡng ngáp = max(mean_mar * 1.8, MAR_MIN_THRESHOLD)
MAR_MIN_THRESHOLD = 0.40              # Ngưỡng ngáp tối thiểu

# Ngưỡng mặc định dự phòng khi chưa có baseline hợp lệ
DEFAULT_EAR_THRESHOLD = 0.22
DEFAULT_MAR_THRESHOLD = 0.50


class AlertState(str, Enum):
    IDLE = "IDLE"                    # Bình thường, chưa phát hiện dấu hiệu
    VERIFYING = "VERIFYING"          # Abnormal evidence has not reached the duration threshold.
    CONFIRMED = "CONFIRMED"          # Xác nhận cảnh báo: Cả YOLO và MediaPipe đều đồng thuận
    SUSPECTED = "SUSPECTED"          # Single-source alert: YOLO or MediaPipe unavailable.
    CONFLICT = "CONFLICT"            # Mâu thuẫn: YOLO thấy mắt nhắm nhưng MediaPipe có đủ mẫu và xác nhận mắt mở
    TRACKING_LOST = "TRACKING_LOST"  # Mất theo dõi khuôn mặt hoàn toàn


def _normalize_label(name):
    return str(name).casefold().strip().replace(" ", "_").replace("-", "_")


def _is_eye_open(name):
    label = _normalize_label(name)
    return (
        label in {"attentive_eye", "open", "eye_open", "open_eye"}
        or "eye_open" in label
        or "open_eye" in label
        or "attentive_eye" in label
    )


def _is_drowsy(name):
    if _is_eye_open(name):
        return False
    label = _normalize_label(name)
    return (
        label in {"eyeclosed", "closed", "close", "asleep", "drowsy_eye", "eye_closed", "eye_close"}
        or "eye_closed" in label
        or "eye_close" in label
        or "eyeclosed" in label
        or "drowsy_eye" in label
    )


def _is_no_yawn(name):
    label = _normalize_label(name)
    return (
        label in {"noyawn", "no_yawn", "mouth_closed", "mouth_close", "closed_mouth"}
        or "noyawn" in label
        or "no_yawn" in label
        or "mouth_closed" in label
        or "mouth_close" in label
        or "closed_mouth" in label
    )


def _is_yawning(name):
    if _is_no_yawn(name):
        return False
    label = _normalize_label(name)
    return (
        label in {"yawn", "open_mouth", "yawning", "mouth_open"}
        or "yawn" in label
        or "mouth_open" in label
        or "open_mouth" in label
    )


def _is_distracted_head(name):
    label = _normalize_label(name)
    return "distracted_head" in label or "distracted" in label


def _is_smoking(name):
    label = _normalize_label(name)
    return "smoking" in label or "smoke" in label


def _is_phone(name):
    label = _normalize_label(name)
    return "phone" in label or "cellphone" in label


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
        cloud_sync=None,
    ):
        model_path = Path(model_path)
        self.cloud_sync = cloud_sync

        self.model = None
        self.model_error = None
        try:
            if not model_path.exists():
                raise FileNotFoundError(f"YOLO model not found: {model_path}")
            self.model = YOLO(str(model_path))
        except Exception as exc:
            self.model_error = str(exc)
            print(f"[STARTUP] YOLO unavailable; MediaPipe-only mode: {exc}")
        self.fusion = SensorFusion(drowsy_threshold, yawning_threshold)
        self.enable_perf_log = bool(enable_perf_log)
        perf_csv_path = (
            os.getenv("PERF_CSV", str(PROJECT_ROOT / "logs" / "performance.csv"))
            if self.enable_perf_log else None
        )
        self.telemetry = Telemetry(perf_csv_path)
        self._alert_latency_ms = None
        # (đã bỏ frame_buffer 10 khung hình: chỉ ghi, không ai đọc, tốn ~9MB RAM + 10 lần copy ảnh/giây)
        self.esp = esp_communicator if esp_communicator is not None else EspCommunicator()
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

        self.drowsy_state = AlertState.IDLE
        self.yawning_state = AlertState.IDLE

        self.distraction_threshold = float(os.getenv("DISTRACTION_THRESHOLD_SECONDS", "1.5"))
        self.head_pitch_threshold = float(os.getenv("HEAD_PITCH_THRESHOLD_DEG", "22.0"))
        self.head_yaw_threshold = float(os.getenv("HEAD_YAW_THRESHOLD_DEG", "35.0"))
        self.head_roll_threshold = float(os.getenv("HEAD_ROLL_THRESHOLD_DEG", "25.0"))
        self.tracking_lost_alert_s = float(os.getenv("TRACKING_LOST_ALERT_S", "3.0"))

        # Cấu hình đọc 1 lần (trước đây os.getenv được gọi nhiều lần MỖI khung hình)
        self._headless = os.getenv("HEADLESS", "0") == "1"
        self._allow_stationary = os.getenv("ALLOW_STATIONARY_DISTRACTION", "0") == "1"
        self._yolo_imgsz = int(os.getenv("YOLO_IMGSZ", "320"))
        self._yolo_max_det = int(os.getenv("YOLO_MAX_DET", "8"))

        # Bộ nhớ đệm kết quả mô hình bị bỏ qua theo lịch nhiệt (ThrottlePlan): (thời điểm, giá trị)
        self._yolo_cache = None   # (t, detections, yolo_eye, yolo_mouth, yolo_error)
        self._mp_cache = None     # (t, mp_result)
        self._speed_cache = ("", 0.0)
        self.skipped_yolo = 0
        self.skipped_mp = 0
        self._warmup_yolo()

        self._head_pose_smooth = None
        self._distracted_started = None
        self._distracted_started_at = None
        self.distracted_duration = 0.0
        self.distracted_state = AlertState.IDLE
        self.last_head_pose = None
        self._last_face_seen_time = time.monotonic()

        self._active_esp_alert = None
        self._alert_recorded = False
        self._conflict_recorded = False
        self._last_detect_log_time = 0.0

        self.perf_monitor = PerformanceMonitor(
            log_interval=float(os.getenv("PERF_LOG_INTERVAL", perf_log_interval)),
            enable_logging=bool(enable_perf_log),
        )
        self.event_recorder = event_recorder
        if self.event_recorder is None and enable_event_recorder:
            self.event_recorder = EventRecorder(
                event_storage_dir or str(PROJECT_ROOT / "AI" / "data"),
            )

    def _run_mediapipe(self, frame, monotonic_time: float) -> dict:
        """Thực thi suy luận MediaPipe Face Landmarker có điều kiện trên frame.

        Chạy độc lập với YOLO để hỗ trợ cảnh báo khi YOLO thiếu dữ liệu.
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
        pose = calculate_head_pose(landmarks, width, height)

        # Lọc mẫu INVALID: khoảng cách ngang bằng 0 hoặc landmark bất thường
        if not (0.01 < ear < 1.0 and 0.0 <= mar < 3.0):
            return {
                "valid": False, "ear": None, "mar": None, "pose": None,
                "timestamp": monotonic_time, "reason": "DEGENERATE_LANDMARKS"
            }

        return {
            "valid": True,
            "ear": round(ear, 4),
            "mar": round(mar, 4),
            "pose": pose,
            "timestamp": monotonic_time,
            "reason": "OK",
        }

    def _get_current_speed(self) -> float:
        """Trich xuat toc do GPS hien tai tu ESP32 telemetry (ho tro ca JSON FDESP va CSV SPD:)."""
        if not self.esp:
            return 0.0
        # Đường nhanh: ESP đã giải mã JSON 1 lần ở luồng đọc UART
        sensor = getattr(self.esp, "latest_sensor", None)
        if isinstance(sensor, dict) and sensor:
            try:
                if "speed_kmh" in sensor:
                    return float(sensor["speed_kmh"])
                if "spd" in sensor:
                    return float(sensor["spd"])
            except (TypeError, ValueError):
                pass
        telem = getattr(self.esp, "latest_telemetry", "") or ""
        if telem == self._speed_cache[0]:
            return self._speed_cache[1]
        speed = self._parse_speed(telem)
        self._speed_cache = (telem, speed)
        return speed

    @staticmethod
    def _parse_speed(telem: str) -> float:
        if "{" in telem and "}" in telem:
            try:
                j_str = telem[telem.find("{") : telem.rfind("}") + 1]
                j_data = json.loads(j_str)
                if "speed_kmh" in j_data:
                    return float(j_data["speed_kmh"])
                if "spd" in j_data:
                    return float(j_data["spd"])
            except Exception:
                pass
        if "SPD:" in telem:
            try:
                for part in telem.split(","):
                    if part.startswith("SPD:"):
                        return float(part.split(":")[1])
            except Exception:
                pass
        return 0.0

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

    def _warmup_yolo(self):
        """Chạy thử 1 lần ngay lúc khởi động để lần suy luận đầu (khởi tạo kernel/bộ nhớ) không
        làm khựng khung hình thật đầu tiên rồi kéo theo vọt nhiệt/độ trễ."""
        if self.model is None:
            return
        try:
            import numpy as np
            dummy = np.zeros((self._yolo_imgsz, self._yolo_imgsz, 3), dtype=np.uint8)
            self.model.predict(source=dummy, imgsz=self._yolo_imgsz, conf=CONFIDENCE_THRESHOLD,
                               max_det=self._yolo_max_det, verbose=False)
        except Exception as exc:
            print(f"[Pipeline] YOLO warm-up bo qua: {exc}")

    def _run_yolo(self, frame, imgsz):
        """Chạy YOLO trên khung hình; trả về (detections, rendered_boxes, error)."""
        detections = []
        boxes_xyxy = []
        error = None
        try:
            if self.model is None:
                raise RuntimeError(self.model_error or "YOLO unavailable")
            results = self.model.predict(source=frame, imgsz=int(imgsz),
                                         conf=CONFIDENCE_THRESHOLD, max_det=self._yolo_max_det,
                                         verbose=False)
            plotted = None
            if not self._headless and self.enable_perf_log:
                plotted = results[0].plot()
            for box in results[0].boxes:
                name = self.model.names[int(box.cls[0].item())]
                conf = float(box.conf[0].item())
                detections.append((name, conf))
                if self._headless:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    boxes_xyxy.append((name, conf, x1, y1, x2, y2))
            return detections, boxes_xyxy, plotted, error
        except Exception as exc:
            error = str(exc)
        return detections, boxes_xyxy, None, error

    def process(
        self,
        frame,
        frame_id=None,
        capture_timestamp_ns=None,
        capture_time_utc=None,
        plan=None,
    ):
        t_pipeline_start = time.perf_counter()
        monotonic_time = (
            capture_timestamp_ns / 1_000_000_000
            if capture_timestamp_ns is not None else time.monotonic()
        )
        wall_time = local_now()
        run_yolo = plan.run_yolo if plan is not None else True
        run_mp = plan.run_mp if plan is not None else True
        hold_s = plan.hold_s if plan is not None else 0.0
        imgsz = plan.imgsz if plan is not None else self._yolo_imgsz

        # Independent measurements: no detection is unknown, not a normal vote.
        # Mô hình bị bỏ qua theo lịch nhiệt dùng lại kết quả cũ trong TTL (hold_s); quá TTL -> "không biết".
        t_yolo_start = time.perf_counter()
        detections = []
        yolo_error = None
        rendered = frame  # chỉ copy khi thật sự cần vẽ lên ảnh (tiết kiệm 1 lần copy 0.9MB/khung)
        if run_yolo or self.model is None:
            detections, boxes_xyxy, plotted, yolo_error = self._run_yolo(frame, imgsz)
            self._yolo_cache = (monotonic_time, detections, boxes_xyxy, None, yolo_error)
        elif self._yolo_cache is not None and (monotonic_time - self._yolo_cache[0]) <= hold_s:
            _, detections, boxes_xyxy, _, yolo_error = self._yolo_cache
            plotted = None
            self.skipped_yolo += 1
        else:
            boxes_xyxy, plotted = [], None
            self.skipped_yolo += 1
        if plotted is not None:
            rendered = plotted
        elif boxes_xyxy:
            rendered = frame.copy()
            for name, conf, x1, y1, x2, y2 in boxes_xyxy:
                color = (0, 0, 255) if (_is_drowsy(name) or _is_yawning(name) or _is_distracted_head(name) or _is_smoking(name) or _is_phone(name)) else (0, 255, 0)
                cv2.rectangle(rendered, (x1, y1), (x2, y2), color, 2)
                cv2.putText(rendered, f"{name} {conf:.2f}", (x1, max(18, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        yolo_time_s = time.perf_counter() - t_yolo_start
        detected_classes = [name for name, _ in detections]
        yolo_eye = yolo_mouth = None
        yolo_distracted = False
        yolo_smoking = False
        yolo_phone = False
        for name in detected_classes:
            if _is_drowsy(name):
                yolo_eye = True
            elif _is_eye_open(name):
                if yolo_eye is None:
                    yolo_eye = False
            if _is_yawning(name):
                yolo_mouth = True
            elif _is_no_yawn(name):
                if yolo_mouth is None:
                    yolo_mouth = False
            if _is_distracted_head(name):
                yolo_distracted = True
            if _is_smoking(name):
                yolo_smoking = True
            if _is_phone(name):
                yolo_phone = True
        t_mp_start = time.perf_counter()
        if run_mp or self._mp_cache is None:
            mp_result = self._run_mediapipe(frame, monotonic_time)
            self._mp_cache = (monotonic_time, mp_result)
        elif (monotonic_time - self._mp_cache[0]) <= hold_s:
            mp_result = self._mp_cache[1]
            self.skipped_mp += 1
        else:
            mp_result = {
                "valid": False, "ear": None, "mar": None,
                "timestamp": monotonic_time, "reason": "SKIPPED_THERMAL",
            }
            self.skipped_mp += 1
        mp_time_s = time.perf_counter() - t_mp_start
        mp_active = self._landmarker is not None
        mp_eye = mp_result["ear"] < self.ear_threshold if mp_result["valid"] else None
        mp_mouth = mp_result["mar"] > self.mar_threshold if mp_result["valid"] else None
        decisions, tracking_lost = self.fusion.update(
            monotonic_time, {"eye": yolo_eye, "mouth": yolo_mouth},
            {"eye": mp_eye, "mouth": mp_mouth},
            mp_result["valid"] or yolo_eye is not None or yolo_mouth is not None
            or any(name.casefold().replace("_", " ") in {"attentive head", "distracted head"}
                   for name in detected_classes),
        )
        instantaneous_drowsy = decisions["eye"].active
        instantaneous_yawning = decisions["mouth"].active
        drowsy_duration = self._update_timer(
            instantaneous_drowsy, "_drowsy_started", monotonic_time,
            "_drowsy_started_at", wall_time,
        )
        yawning_duration = self._update_timer(
            instantaneous_yawning, "_yawning_started", monotonic_time,
            "_yawning_started_at", wall_time,
        )

        # Xu ly Head Pose 3D & Tracking Lost cho Distraction (Guc dau / Nghieng dau / Mat tap trung)
        pose = mp_result.get("pose")
        if pose is not None:
            yaw, pitch, roll = pose
            if self._head_pose_smooth is None:
                self._head_pose_smooth = (yaw, pitch, roll)
            else:
                alpha = 0.35
                self._head_pose_smooth = (
                    self._head_pose_smooth[0] + alpha * (yaw - self._head_pose_smooth[0]),
                    self._head_pose_smooth[1] + alpha * (pitch - self._head_pose_smooth[1]),
                    self._head_pose_smooth[2] + alpha * (roll - self._head_pose_smooth[2]),
                )
            self.last_head_pose = self._head_pose_smooth

        face_visible = (
            mp_result["valid"]
            or yolo_eye is not None
            or yolo_mouth is not None
            or yolo_distracted
            or any(_is_eye_open(name) or _is_no_yawn(name) for name in detected_classes)
        )
        if face_visible:
            self._last_face_seen_time = monotonic_time
            lost_face_duration = 0.0
        else:
            lost_face_duration = monotonic_time - self._last_face_seen_time

        is_head_distracted = yolo_distracted
        if self._head_pose_smooth is not None and mp_result["valid"]:
            s_yaw, s_pitch, s_roll = self._head_pose_smooth
            if (
                abs(s_pitch) >= self.head_pitch_threshold
                or abs(s_roll) >= self.head_roll_threshold
                or abs(s_yaw) >= self.head_yaw_threshold
            ):
                is_head_distracted = True

        # Luoi an toan: Mat dau mat qua lau khi dang chay xe (toc do >= 10 km/h)
        is_tracking_lost_distracted = False
        current_spd = self._get_current_speed()
        allow_stationary = self._allow_stationary
        if lost_face_duration >= self.tracking_lost_alert_s and (current_spd >= 10.0 or allow_stationary):
            is_tracking_lost_distracted = True

        instantaneous_distracted = is_head_distracted or is_tracking_lost_distracted
        distracted_duration = self._update_timer(
            instantaneous_distracted, "_distracted_started", monotonic_time,
            "_distracted_started_at", wall_time,
        )

        if instantaneous_distracted and distracted_duration >= self.distraction_threshold:
            self.distracted_state = AlertState.CONFIRMED
        elif instantaneous_distracted:
            self.distracted_state = AlertState.VERIFYING
        else:
            self.distracted_state = AlertState.IDLE

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

        self.drowsy_state = AlertState(decisions["eye"].state)
        self.yawning_state = AlertState(decisions["mouth"].state)
        drowsy_duration = decisions["eye"].duration
        yawning_duration = decisions["mouth"].duration
        # Counts describe the current independent sample, not stale votes.
        drowsy_valid_samples = int(mp_eye is not None)
        yawning_valid_samples = int(mp_mouth is not None)
        drowsy_vote_ratio = float(mp_eye is True)
        yawning_vote_ratio = float(mp_mouth is True)

        if self.event_recorder is not None:
            event_timestamp_ns = capture_timestamp_ns or time.monotonic_ns()
            event_metadata = {
                "yolo_status": (
                    "DROWSY" if yolo_eye is True else "YAWNING" if yolo_mouth is True
                    else "NORMAL" if yolo_eye is not None or yolo_mouth is not None else "UNKNOWN"
                ),
                "eye_source": decisions["eye"].source,
                "mouth_source": decisions["mouth"].source,
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
                        "yolo_active": yolo_eye if event_type == "DROWSY" else yolo_mouth,
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
        elif self.distracted_state == AlertState.CONFIRMED:
            confirmed_alert = "DISTRACTED"
            detailed_alert = "DISTRACTED_ALERT"
            esp_target = "DISTRACTED"
            alert_duration = distracted_duration
            event_start = self._distracted_started_at
        elif self.drowsy_state == AlertState.CONFLICT or self.yawning_state == AlertState.CONFLICT:
            confirmed_alert = "CONFLICT"
            detailed_alert = "CONFLICT"
            esp_target = "NORMAL" if self._active_esp_alert else None
            alert_duration = max(drowsy_duration, yawning_duration)
            event_start = self._drowsy_started_at or self._yawning_started_at
        elif self.drowsy_state == AlertState.TRACKING_LOST or self.yawning_state == AlertState.TRACKING_LOST:
            confirmed_alert = "TRACKING_LOST"
            detailed_alert = "TRACKING_LOST"
            esp_target = "NORMAL" if self._active_esp_alert else None
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

        if esp_target in ("DROWSY", "YAWNING", "DISTRACTED"):
            if esp_target != self._active_esp_alert:
                self._alert_latency_ms = max(0.0, (time.monotonic() - (monotonic_time - alert_duration)) * 1000)
        else:
            self._alert_latency_ms = None
        if confirmed_alert == "NORMAL" and yolo_eye is None and yolo_mouth is None and not mp_result["valid"]:
            confirmed_alert = "UNKNOWN"
        esp_result = None
        confirmed_at = iso_timestamp(wall_time)

        # Tao chuoi chan doan nguyen nhan ben trong (alert_reason) ro rang cho tung canh bao
        alert_reason = ""
        if esp_target == "DROWSY":
            ear_str = f", EAR={mp_result['ear']:.3f}<{self.ear_threshold:.3f}" if mp_result.get("valid") and mp_result.get("ear") is not None else ""
            alert_reason = f"Nhắm mắt {drowsy_duration:.1f}s/{self.drowsy_threshold:.1f}s (nguồn={decisions['eye'].source}{ear_str})"
        elif esp_target == "YAWNING":
            mar_str = f", MAR={mp_result['mar']:.3f}>{self.mar_threshold:.3f}" if mp_result.get("valid") and mp_result.get("mar") is not None else ""
            alert_reason = f"Ngáp {yawning_duration:.1f}s/{self.yawning_threshold:.1f}s (nguồn={decisions['mouth'].source}{mar_str})"
        elif esp_target == "DISTRACTED":
            if is_head_distracted and self._head_pose_smooth is not None and mp_result.get("valid"):
                alert_reason = (
                    f"Lệch góc đầu {distracted_duration:.1f}s/{self.distraction_threshold:.1f}s "
                    f"(Pitch={self._head_pose_smooth[1]:.1f}°, Yaw={self._head_pose_smooth[0]:.1f}°)"
                )
            elif yolo_distracted:
                alert_reason = f"YOLO phát hiện lệch đầu {distracted_duration:.1f}s/{self.distraction_threshold:.1f}s (Distracted Head)"
            else:
                alert_reason = f"Mất dấu mặt {lost_face_duration:.1f}s ở tốc độ {current_spd:.1f}km/h"
        elif confirmed_alert == "CONFLICT":
            alert_reason = (
                f"Xung đột: YOLO={detected_classes} nhưng MediaPipe báo bình thường "
                f"(EAR={mp_result.get('ear')}, MAR={mp_result.get('mar')}) -> Chặn cảnh báo giả"
            )

        # 1. Truyen tin hieu sang ESP (chi gui khi chuyen trang thai, khong lap lai moi frame)
        if esp_target is not None and esp_target != self._active_esp_alert:
            print(
                f"[ALERT] Chuyen trang thai ESP: {self._active_esp_alert or 'NONE'} -> {esp_target} "
                f"(confirmed={confirmed_alert}, duration={alert_duration:.2f}s, classes={detected_classes}, reason={alert_reason or 'IDLE'})"
            )
            esp_result = self.esp.send_status(esp_target, alert_duration, confirmed_at)
            self._active_esp_alert = esp_target if esp_target != "NORMAL" else None
        elif self.enable_perf_log and (
            (confirmed_alert != self._confirmed_alert and confirmed_alert not in ("NORMAL", "UNKNOWN", "TRACKING_LOST"))
            or ((instantaneous_drowsy or instantaneous_yawning) and (monotonic_time - self._last_detect_log_time) >= 2.0)
        ):
            self._last_detect_log_time = monotonic_time
            print(
                f"[DETECT] classes={detected_classes} | eye={self.drowsy_state.value}({drowsy_duration:.1f}s) "
                f"mouth={self.yawning_state.value}({yawning_duration:.1f}s) | alert={confirmed_alert}"
            )

        # 2. Ghi nhan bang chung (anh JPG + events.jsonl)
        if detailed_alert in ("DROWSY_ALERT", "SUSPECTED_ALERT", "YAWNING_ALERT", "SUSPECTED_YAWN_ALERT", "DISTRACTED_ALERT"):
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
            "yolo_error": yolo_error,
            "yolo_eye_valid": yolo_eye is not None,
            "yolo_mouth_valid": yolo_mouth is not None,
            "mediapipe_reason": mp_result["reason"],
            "eye_source": decisions["eye"].source,
            "mouth_source": decisions["mouth"].source,
            "eye_active": instantaneous_drowsy,
            "mouth_active": instantaneous_yawning,
            "yolo_eye": yolo_eye,
            "yolo_mouth": yolo_mouth,
            "yolo_distracted": yolo_distracted,
            "yolo_smoking": yolo_smoking,
            "yolo_phone": yolo_phone,
            "mp_eye": mp_eye,
            "mp_mouth": mp_mouth,
            "tracking_lost": tracking_lost,
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
                else "DISTRACTED" if instantaneous_distracted
                else "NORMAL"
            ),
            "confirmed_alert": confirmed_alert,
            "detailed_alert": detailed_alert,
            "esp_target": esp_target,
            "alert_reason": alert_reason,
            "drowsy_duration": drowsy_duration,
            "yawning_duration": yawning_duration,
            "distracted_duration": distracted_duration,
            "distracted_state": self.distracted_state.value,
            "distraction_threshold_seconds": self.distraction_threshold,
            "head_pose": {
                "yaw": round(self._head_pose_smooth[0], 1),
                "pitch": round(self._head_pose_smooth[1], 1),
                "roll": round(self._head_pose_smooth[2], 1),
            } if self._head_pose_smooth else None,
            "eye_duration": drowsy_duration,
            "mouth_duration": yawning_duration,
            "eye_threshold_seconds": self.drowsy_threshold,
            "yawn_threshold_seconds": self.yawning_threshold,
            "drowsy_state": self.drowsy_state.value,
            "yawning_state": self.yawning_state.value,
            "drowsy_votes_count": drowsy_valid_samples,
            "yawning_votes_count": yawning_valid_samples,
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
            "thermal_level": plan.level if plan is not None else 0,
            "yolo_ran": run_yolo,
            "mp_ran": run_mp,
        }
        state["esp_mode"] = self.esp.mode_label
        state["esp_connected"] = self.esp.connected
        state["esp_result"] = esp_result or self.esp.last_result

        t_pipeline_end = time.perf_counter()
        pipeline_time_s = t_pipeline_end - t_pipeline_start

        self.perf_monitor.record_frame(
            yolo_time_s=yolo_time_s,
            mp_time_s=mp_time_s,
            pipeline_time_s=pipeline_time_s,
            mp_active=mp_active and run_mp,
        )
        state["ai_fps"] = self.perf_monitor.fps
        state["yolo_latency_ms"] = self.perf_monitor.yolo_latency_ms
        state["mp_latency_ms"] = self.perf_monitor.mp_latency_ms
        state["pipeline_latency_ms"] = self.perf_monitor.pipeline_latency_ms
        state["mp_total_activations"] = self.perf_monitor.total_mp_activations

        state["frame_age_ms"] = max(0.0, (time.monotonic() - monotonic_time) * 1000)
        state["alert_latency_ms"] = self._alert_latency_ms
        self.telemetry.record(state)
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
        if self.cloud_sync is not None:
            try:
                self.cloud_sync.dispatch_violation(
                    alert_type=alert_type,
                    frame=frame,
                    started_at=started_at,
                    confirmed_at=confirmed_at,
                    duration_seconds=duration_seconds,
                    confidence=confidence,
                )
            except Exception as e:
                print(f"[CLOUD SYNC] dispatch_violation error: {e}")
        return event

    def reset_tracking(self):
        self.fusion = SensorFusion(self.drowsy_threshold, self.yawning_threshold)
        self._drowsy_started = self._yawning_started = None
        self._drowsy_started_at = self._yawning_started_at = None
        self._distracted_started = self._distracted_started_at = None
        self.distracted_state = AlertState.IDLE
        self._head_pose_smooth = None
        self._active_esp_alert = None
        if self.event_recorder is not None:
            self.event_recorder.interrupt()
        self._alert_recorded = self._conflict_recorded = False

    def close(self):
        self.telemetry.close()
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
