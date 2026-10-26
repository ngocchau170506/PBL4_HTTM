"""Independent, timestamp-based decisions; missing data is never a negative vote."""
from dataclasses import dataclass


@dataclass
class Decision:
    state: str
    active: bool
    duration: float
    source: str


class SensorFusion:
    def __init__(self, eye_seconds=2.0, yawn_seconds=1.0, lost_seconds=1.5):
        self.thresholds = {"eye": eye_seconds, "mouth": yawn_seconds}
        self.lost_seconds = lost_seconds
        self.started = {}
        self.last_seen = None
        self.first_time = None

    def update(self, now, yolo, mediapipe, face_seen):
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
            active = not conflict and (a is True or b is True)
            # A missing observation breaks continuity; never carry old evidence across a gap.
            if active:
                self.started.setdefault(channel, now)
            else:
                self.started.pop(channel, None)
            duration = now - self.started[channel] if active else 0.0
            state = "CONFLICT" if conflict else "IDLE"
            if active:
                state = "VERIFYING"
                if duration >= self.thresholds[channel]:
                    state = "CONFIRMED" if source == "BOTH" else "SUSPECTED"
            elif source == "NONE" and lost:
                state = "TRACKING_LOST"
            decisions[channel] = Decision(state, active, duration, source)
        return decisions, lost
