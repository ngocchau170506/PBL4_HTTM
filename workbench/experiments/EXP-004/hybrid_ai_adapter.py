"""Bo thich ung AI hai tang lai ghep (Hybrid Two-Stage YOLO + MediaPipe) cho EXP-004.

- Trich xuat toan bo thuat toan tieu chuan tu E:\\AI\\XUAT\\PBL4_HTTM-AI\\AI:
  1. Cong thuc EAR (Eye Aspect Ratio), MAR (Mouth Aspect Ratio), Z-Score ca nhan hoa.
  2. Uoc luong goc dau 3D (Yaw, Pitch, Roll) va bo loc muot EMA (alpha=0.3).
  3. May trang thai xac minh 2 tang (Hybrid Two-Stage State Machine):
     IDLE -> WATCHING -> VERIFYING (kich hoat MediaPipe on-demand) -> CONFIRMED / SUSPECTED / CONFLICT.
- Tuan thu 100% hop dong AiAdapter (Luat R6) va tra ve DetectionResult chuan contracts/logic.py.
- Khong thay doi bat ky dong nao cua Brain (src/contracts/logic.py va state.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
import time
from typing import Dict, List, Optional, Tuple, Union

from contracts.logic import Detection, DetectionResult, ErrorEvent, Health, SignalPacket


class HybridAlertState(str, Enum):
    """May trang thai xac minh 2 tang dong bo voi drowsiness_pipeline.py."""

    IDLE = "IDLE"
    WATCHING = "WATCHING"
    VERIFYING = "VERIFYING"
    CONFIRMED = "CONFIRMED"
    SUSPECTED = "SUSPECTED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class Point2D:
    x: float
    y: float


def euclidean_distance(p1: Point2D, p2: Point2D) -> float:
    return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2)


def calculate_ear(eye_pts: Tuple[Point2D, Point2D, Point2D, Point2D, Point2D, Point2D]) -> float:
    """Tinh Eye Aspect Ratio (EAR) tren 6 diem moc mat theo chuan PBL4-HTTM-AI."""
    p1, p2, p3, p4, p5, p6 = eye_pts
    vertical_1 = euclidean_distance(p2, p6)
    vertical_2 = euclidean_distance(p3, p5)
    horizontal = euclidean_distance(p1, p4)
    if horizontal <= 1e-9:
        return 0.0
    return (vertical_1 + vertical_2) / (2.0 * horizontal)


def calculate_mar(left: Point2D, right: Point2D, top: Point2D, bottom: Point2D) -> float:
    """Tinh Mouth Aspect Ratio (MAR) tren 4 diem moc mieng theo chuan PBL4-HTTM-AI."""
    horizontal = euclidean_distance(left, right)
    vertical = euclidean_distance(top, bottom)
    if horizontal <= 1e-9:
        return 0.0
    return vertical / horizontal


def calculate_z_score(value: float, mean: float, std: float) -> float:
    """Tinh Z-Score dua tren baseline ca nhan cua tai xe."""
    if std <= 1e-9:
        return 0.0
    return (value - mean) / std


def ema_smooth(prev: Optional[float], new_val: float, alpha: float = 0.3) -> float:
    """Bo loc EMA lam muot goc quay dau (alpha=0.3 theo head_pose.py)."""
    if prev is None:
        return new_val
    return prev + alpha * (new_val - prev)


def build_synthetic_eye_landmarks(target_ear: float) -> Tuple[Point2D, Point2D, Point2D, Point2D, Point2D, Point2D]:
    """Dung 6 diem moc mat tuong ung voi gia tri EAR muc tieu phuc vu kiem thu."""
    half_v = target_ear / 2.0
    return (
        Point2D(0.0, 0.0),
        Point2D(0.3, half_v),
        Point2D(0.7, half_v),
        Point2D(1.0, 0.0),
        Point2D(0.7, -half_v),
        Point2D(0.3, -half_v),
    )


def build_synthetic_mouth_landmarks(target_mar: float) -> Tuple[Point2D, Point2D, Point2D, Point2D]:
    """Dung 4 diem moc mieng (left, right, top, bottom) tuong ung voi MAR muc tieu."""
    half_v = target_mar / 2.0
    return (
        Point2D(0.0, 0.0),
        Point2D(1.0, 0.0),
        Point2D(0.5, half_v),
        Point2D(0.5, -half_v),
    )


@dataclass
class FrameScenarioInput:
    """Du lieu dau vao cua 1 frame trong kich ban thu nghiem."""

    yolo_label: str
    yolo_conf: float
    target_ear: float
    target_mar: float
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0


class HybridTwoStageAdapter:
    """Bo thich ung AI 2 tang (YOLO + MediaPipe on-demand) tuan thu giao dien AiAdapter."""

    name: str = "hybrid_two_stage"

    def __init__(
        self,
        ear_mean: float = 0.31,
        ear_std: float = 0.025,
        mar_mean: float = 0.22,
        mar_std: float = 0.04,
        mediapipe_eye_trigger_ms: int = 1000,
        drowsy_confirm_ms: int = 2000,
        mediapipe_yawn_trigger_ms: int = 500,
        yawn_confirm_ms: int = 1000,
        min_valid_samples: int = 5,
        vote_threshold: float = 0.70,
        scenario_provider: Optional[Dict[int, FrameScenarioInput]] = None,
    ) -> None:
        self.ear_mean = float(ear_mean)
        self.ear_std = float(ear_std)
        self.mar_mean = float(mar_mean)
        self.mar_std = float(mar_std)

        self.ear_threshold = round(self.ear_mean * 0.75, 4)
        self.mar_threshold = round(max(self.mar_mean * 1.8, 0.40), 4)

        self.mediapipe_eye_trigger_ms = int(mediapipe_eye_trigger_ms)
        self.drowsy_confirm_ms = int(drowsy_confirm_ms)
        self.mediapipe_yawn_trigger_ms = int(mediapipe_yawn_trigger_ms)
        self.yawn_confirm_ms = int(yawn_confirm_ms)
        self.min_valid_samples = int(min_valid_samples)
        self.vote_threshold = float(vote_threshold)

        self._scenario = scenario_provider or {}
        self._loaded: bool = False
        self._health: Health = "OK"

        # Trang thai theo doi Drowsiness & Yawning
        self.drowsy_state: HybridAlertState = HybridAlertState.IDLE
        self.yawning_state: HybridAlertState = HybridAlertState.IDLE
        self._eye_onset_ts_ms: Optional[int] = None
        self._yawn_onset_ts_ms: Optional[int] = None
        self._drowsy_votes: List[bool] = []
        self._yawn_votes: List[bool] = []

        # Lam muot goc dau 3D
        self.smooth_yaw: Optional[float] = None
        self.smooth_pitch: Optional[float] = None
        self.smooth_roll: Optional[float] = None

        # Chi so hieu nang
        self.total_inferences: int = 0
        self.mediapipe_activations: int = 0
        self.conflicts_rejected: int = 0
        self.last_ear: float = self.ear_mean
        self.last_mar: float = self.mar_mean
        self.last_z_ear: float = 0.0
        self.last_z_mar: float = 0.0

    def load(self) -> Optional[ErrorEvent]:
        self._loaded = True
        self._health = "OK"
        return None

    def health(self) -> Health:
        return self._health if self._loaded else "DOWN"

    def set_scenario(self, mapping: Dict[int, FrameScenarioInput]) -> None:
        self._scenario = dict(mapping)

    def infer(self, pkt: SignalPacket) -> Union[DetectionResult, ErrorEvent]:
        """Thuc thi suy luan 2 tang: YOLO truoc, chi bat MediaPipe khi nghi ngo dat nguong."""
        self.total_inferences += 1
        sc = self._scenario.get(
            pkt.frame_id,
            FrameScenarioInput(
                yolo_label="Attentive eye",
                yolo_conf=0.92,
                target_ear=self.ear_mean,
                target_mar=self.mar_mean,
            ),
        )

        # Cap nhat goc dau 3D qua EMA (alpha=0.3)
        self.smooth_yaw = ema_smooth(self.smooth_yaw, sc.yaw_deg, 0.3)
        self.smooth_pitch = ema_smooth(self.smooth_pitch, sc.pitch_deg, 0.3)
        self.smooth_roll = ema_smooth(self.smooth_roll, sc.roll_deg, 0.3)

        is_yolo_eye_closed = sc.yolo_label in ("Eyeclosed", "closed", "Drowsy eye")
        is_yolo_yawn = sc.yolo_label in ("Yawn", "yawn", "Open-Mouth")

        mp_triggered = False

        # --- Nhanh 1: Xu ly Drowsiness (Mat nham) ---
        if is_yolo_eye_closed:
            if self._eye_onset_ts_ms is None:
                self._eye_onset_ts_ms = pkt.ts_ms
                self._drowsy_votes.clear()
            dur_ms = max(0, pkt.ts_ms - self._eye_onset_ts_ms)

            if dur_ms < self.mediapipe_eye_trigger_ms:
                self.drowsy_state = HybridAlertState.WATCHING
            else:
                # Kich hoat MediaPipe Tang 2 de xac minh EAR & Z-score
                mp_triggered = True
                eye_pts = build_synthetic_eye_landmarks(sc.target_ear)
                ear_val = calculate_ear(eye_pts)
                z_ear = calculate_z_score(ear_val, self.ear_mean, self.ear_std)
                self.last_ear = round(ear_val, 4)
                self.last_z_ear = round(z_ear, 2)

                is_mp_closed = (ear_val < self.ear_threshold) or (z_ear < -2.0)
                self._drowsy_votes.append(is_mp_closed)
                ratio = sum(1 for v in self._drowsy_votes if v) / len(self._drowsy_votes)

                if ratio < self.vote_threshold and len(self._drowsy_votes) >= 2:
                    self.drowsy_state = HybridAlertState.CONFLICT
                    self.conflicts_rejected += 1
                elif dur_ms < self.drowsy_confirm_ms:
                    self.drowsy_state = HybridAlertState.VERIFYING
                else:
                    if len(self._drowsy_votes) >= self.min_valid_samples and ratio >= self.vote_threshold:
                        self.drowsy_state = HybridAlertState.CONFIRMED
                    else:
                        self.drowsy_state = HybridAlertState.SUSPECTED
        else:
            self._eye_onset_ts_ms = None
            self._drowsy_votes.clear()
            self.drowsy_state = HybridAlertState.IDLE
            self.last_ear = self.ear_mean
            self.last_z_ear = 0.0

        # --- Nhanh 2: Xu ly Yawning (Ngap) ---
        if is_yolo_yawn:
            if self._yawn_onset_ts_ms is None:
                self._yawn_onset_ts_ms = pkt.ts_ms
                self._yawn_votes.clear()
            yawn_dur_ms = max(0, pkt.ts_ms - self._yawn_onset_ts_ms)

            if yawn_dur_ms < self.mediapipe_yawn_trigger_ms:
                self.yawning_state = HybridAlertState.WATCHING
            else:
                mp_triggered = True
                m_left, m_right, m_top, m_bot = build_synthetic_mouth_landmarks(sc.target_mar)
                mar_val = calculate_mar(m_left, m_right, m_top, m_bot)
                z_mar = calculate_z_score(mar_val, self.mar_mean, self.mar_std)
                self.last_mar = round(mar_val, 4)
                self.last_z_mar = round(z_mar, 2)

                is_mp_open = (mar_val > self.mar_threshold) or (z_mar > 2.0)
                self._yawn_votes.append(is_mp_open)

                if yawn_dur_ms < self.yawn_confirm_ms:
                    self.yawning_state = HybridAlertState.VERIFYING
                else:
                    if len(self._yawn_votes) >= 3:
                        ratio = sum(1 for v in self._yawn_votes if v) / len(self._yawn_votes)
                        if ratio >= self.vote_threshold:
                            self.yawning_state = HybridAlertState.CONFIRMED
                        else:
                            self.yawning_state = HybridAlertState.CONFLICT
                            self.conflicts_rejected += 1
                    else:
                        self.yawning_state = HybridAlertState.SUSPECTED
        else:
            self._yawn_onset_ts_ms = None
            self._yawn_votes.clear()
            self.yawning_state = HybridAlertState.IDLE
            self.last_mar = self.mar_mean
            self.last_z_mar = 0.0

        if mp_triggered:
            self.mediapipe_activations += 1

        # Neu MediaPipe phat hien CONFLICT (YOLO bao nham mat nhung EAR chung minh mat mo),
        # ha nhan ve Attentive eye de ngan bao dong gia truoc khi sang RulesEngine!
        final_label = sc.yolo_label
        if self.drowsy_state == HybridAlertState.CONFLICT:
            final_label = "Attentive eye"
        elif self.yawning_state == HybridAlertState.CONFLICT:
            final_label = "noYawn"

        det = Detection(label=final_label, conf=sc.yolo_conf, box=(120, 90, 200, 220))
        return DetectionResult(
            trace_id=pkt.trace_id,
            ts_ms=pkt.ts_ms,
            source="process.ai",
            frame_id=pkt.frame_id,
            items=(det,),
        )

    @property
    def mediapipe_activation_ratio(self) -> float:
        if self.total_inferences == 0:
            return 0.0
        return round((self.mediapipe_activations / self.total_inferences) * 100.0, 2)

    def self_test(self) -> Tuple[bool, str]:
        self.load()
        sig = SignalPacket(
            trace_id="hyb-selftest",
            ts_ms=1000,
            source="signal",
            frame_id=1,
            width=640,
            height=480,
            driver_id="drv-test",
        )
        res = self.infer(sig)
        if isinstance(res, ErrorEvent):
            return False, f"{res.code}: {res.message}"
        return True, (
            f"HybridTwoStageAdapter OK (ear_th={self.ear_threshold}, mar_th={self.mar_threshold})"
        )


def _now_ms() -> int:
    return int(time.time() * 1000)
