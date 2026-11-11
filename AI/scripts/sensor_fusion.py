"""Independent, timestamp-based decisions; missing data is never a negative vote."""
from dataclasses import dataclass


@dataclass
class Decision:
    state: str
    active: bool
    duration: float
    source: str


class SensorFusion:
    def __init__(self, eye_seconds=2.0, yawn_seconds=1.0, lost_seconds=1.5, max_gap_seconds=0.6):
        self.thresholds = {"eye": eye_seconds, "mouth": yawn_seconds}
        self.lost_seconds = lost_seconds
        self.max_gap_seconds = float(max_gap_seconds)
        self.started = {}
        self.last_true = {}
        self.last_seen = None
        self.first_time = None

    def update(self, now, yolo, mediapipe, face_seen, single_model_mode=False):
        if self.first_time is None:
            self.first_time = now
        if face_seen:
            self.last_seen = now
        lost = now - (self.last_seen if self.last_seen is not None else self.first_time) >= self.lost_seconds
        decisions = {}
        for channel in ("eye", "mouth"):
            a, b = yolo[channel], mediapipe[channel]
            source = "BOTH" if a is not None and b is not None else "YOLO" if a is not None else "MEDIAPIPE" if b is not None else "NONE"
            conflict = a is not None and b is not None and a != b
            explicit_normal = (a is False or b is False) and not conflict
            active = not conflict and (a is True or b is True)
            if active:
                self.started.setdefault(channel, now)
                self.last_true[channel] = now
            elif (
                single_model_mode
                and not explicit_normal
                and source == "NONE"
                and channel in self.started
                and (now - self.last_true.get(channel, now)) <= self.max_gap_seconds
            ):
                # Chịu khuyết hộp phát hiện ngắn (<= max_gap_seconds, mặc định 0.6s khớp sustained.py)
                # khi chạy 1 mô hình độc lập (YOLO-only sau pha MediaPipe) mà chưa thấy nhãn mở mắt/bình thường.
                active = True
                source = "YOLO_GAP_HOLD"
            else:
                self.started.pop(channel, None)
                self.last_true.pop(channel, None)
            duration = now - self.started[channel] if active else 0.0
            state = "CONFLICT" if conflict else "IDLE"
            if active:
                state = "VERIFYING"
                if duration >= self.thresholds[channel]:
                    state = "CONFIRMED" if (source == "BOTH" or single_model_mode) else "SUSPECTED"
            elif source == "NONE" and lost:
                state = "TRACKING_LOST"
            decisions[channel] = Decision(state, active, duration, source)
        return decisions, lost
