"""SensorFusion & Temporal Alert Evaluator (fixes F-022 & blind-hold stacking > 2.0s!).

Key invariants (LCK-06):
  1. alert_state ('NORMAL' | 'EDGE_TRIGGERED') is strictly separated from
     verification_mode ('SINGLE_MODEL_YOLO' | 'YOLO_BLAZEFACE_SEQ' | 'DUAL_MODEL_FUSED').
  2. Total blind hold (cache_ttl + gap_hold) is HARD-CLAMPED to <= 0.8s, so a single
     transient false-positive frame can NEVER stack up past the 2.0s DROWSY or 1.8s YAWNING threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class FusionDecision:
    channel: str
    alert_state: str          # NORMAL | EDGE_TRIGGERED
    alert_type: str           # NORMAL | DROWSY | YAWNING | DISTRACTED | SMOKING | PHONE
    verification_mode: str    # SINGLE_MODEL_YOLO | YOLO_BLAZEFACE_SEQ | DUAL_MODEL_FUSED
    duration_s: float
    threshold_s: float
    in_blind_hold: bool
    reason: str


class ChannelTimer:
    def __init__(
        self,
        name: str,
        alert_type: str,
        threshold_s: float,
        max_blind_hold_s: float = 0.75,
        neg_reset_hits: int = 2,
    ):
        self.name = name
        self.alert_type = alert_type
        self.threshold_s = max(0.1, float(threshold_s))
        # HARD CLAMP <= 0.8s (LCK-06)
        self.max_blind_hold_s = max(0.05, min(0.8, float(max_blind_hold_s)))
        self.neg_reset_hits = max(1, int(neg_reset_hits))
        self._active_since: Optional[float] = None
        self._last_positive_ts: Optional[float] = None
        self._positive_hits: int = 0
        self._neg_streak: int = 0

    def reset(self) -> None:
        self._active_since = None
        self._last_positive_ts = None
        self._positive_hits = 0
        self._neg_streak = 0

    def update(
        self,
        observed_positive: Optional[bool],
        now_mono: float,
        verification_mode: str,
        detail: str = "",
    ) -> FusionDecision:
        if observed_positive is True:
            self._neg_streak = 0
            if self._active_since is None:
                self._active_since = now_mono
                self._positive_hits = 1
            else:
                self._positive_hits += 1
            self._last_positive_ts = now_mono
            dur = max(0.0, now_mono - self._active_since)
            # Require at least 2 real positive observations for any sustained alert (prevents 1-frame spike!)
            triggered = dur >= self.threshold_s and self._positive_hits >= 2
            return FusionDecision(
                channel=self.name,
                alert_state="EDGE_TRIGGERED" if triggered else "NORMAL",
                alert_type=self.alert_type if triggered else "NORMAL",
                verification_mode=verification_mode,
                duration_s=round(dur, 3),
                threshold_s=self.threshold_s,
                in_blind_hold=False,
                reason=detail or f"{self.alert_type} {dur:.2f}s/{self.threshold_s:.1f}s (hits={self._positive_hits})",
            )

        if observed_positive is False:
            # 2-frame negative hysteresis ONLY after >=2 positive hits AND within strict <=0.8s blind hold (fixes F-018 vibration flicker!)
            if (
                self._positive_hits >= 2
                and self._active_since is not None
                and self._last_positive_ts is not None
                and (now_mono - self._last_positive_ts) <= self.max_blind_hold_s
            ):
                self._neg_streak += 1
                if self._neg_streak < self.neg_reset_hits:
                    dur = max(0.0, self._last_positive_ts - self._active_since)
                    triggered = dur >= self.threshold_s and self._positive_hits >= 2
                    return FusionDecision(
                        channel=self.name,
                        alert_state="EDGE_TRIGGERED" if triggered else "NORMAL",
                        alert_type=self.alert_type if triggered else "NORMAL",
                        verification_mode=verification_mode,
                        duration_s=round(dur, 3),
                        threshold_s=self.threshold_s,
                        in_blind_hold=True,
                        reason=f"HYSTERESIS_HOLD(neg={self._neg_streak}/{self.neg_reset_hits})",
                    )
            self.reset()
            return FusionDecision(
                channel=self.name,
                alert_state="NORMAL",
                alert_type="NORMAL",
                verification_mode=verification_mode,
                duration_s=0.0,
                threshold_s=self.threshold_s,
                in_blind_hold=False,
                reason="NORMAL",
            )

        # observed_positive is None (inference gap): hold ONLY within strict max_blind_hold_s <= 0.8s
        if self._active_since is not None and self._last_positive_ts is not None:
            gap = now_mono - self._last_positive_ts
            if gap <= self.max_blind_hold_s:
                # Note: duration freezes at last positive timestamp during blind hold (never accumulates fake time!)
                dur = max(0.0, self._last_positive_ts - self._active_since)
                triggered = dur >= self.threshold_s and self._positive_hits >= 2
                return FusionDecision(
                    channel=self.name,
                    alert_state="EDGE_TRIGGERED" if triggered else "NORMAL",
                    alert_type=self.alert_type if triggered else "NORMAL",
                    verification_mode=verification_mode,
                    duration_s=round(dur, 3),
                    threshold_s=self.threshold_s,
                    in_blind_hold=True,
                    reason=f"BLIND_HOLD({gap:.2f}s<={self.max_blind_hold_s:.2f}s)",
                )
        self.reset()
        return FusionDecision(
            channel=self.name,
            alert_state="NORMAL",
            alert_type="NORMAL",
            verification_mode=verification_mode,
            duration_s=0.0,
            threshold_s=self.threshold_s,
            in_blind_hold=False,
            reason="IDLE",
        )


class SensorFusion:
    """Multi-channel temporal fusion for DROWSY, YAWNING, DISTRACTED, SMOKING, PHONE."""

    def __init__(
        self,
        eye_threshold_s: float = 2.0,
        yawn_threshold_s: float = 1.8,
        distracted_threshold_s: float = 2.0,
        behavior_threshold_s: float = 1.2,
        max_blind_hold_s: float = 0.75,
    ):
        hold = max(0.05, min(0.8, float(max_blind_hold_s)))
        self.max_blind_hold_s = hold
        self.channels: Dict[str, ChannelTimer] = {
            "eye": ChannelTimer("eye", "DROWSY", eye_threshold_s, hold),
            "mouth": ChannelTimer("mouth", "YAWNING", yawn_threshold_s, hold),
            "distracted": ChannelTimer("distracted", "DISTRACTED", distracted_threshold_s, hold),
            "smoking": ChannelTimer("smoking", "SMOKING", behavior_threshold_s, hold),
            "phone": ChannelTimer("phone", "PHONE", behavior_threshold_s, hold),
        }

    def update(
        self,
        observations: Dict[str, Optional[bool]],
        now_mono: float,
        verification_mode: str = "SINGLE_MODEL_YOLO",
    ) -> Tuple[FusionDecision, Dict[str, FusionDecision]]:
        decisions: Dict[str, FusionDecision] = {}
        for key, timer in self.channels.items():
            decisions[key] = timer.update(observations.get(key), now_mono, verification_mode)

        # Priority order: DROWSY > YAWNING > DISTRACTED > PHONE > SMOKING > NORMAL
        for key in ("eye", "mouth", "distracted", "phone", "smoking"):
            d = decisions[key]
            if d.alert_state == "EDGE_TRIGGERED":
                return d, decisions
        return decisions["eye"], decisions


from typing import Tuple  # noqa: E402
