"""SensorFusion & Temporal Alert Evaluator (fixes F-022 & blind-hold stacking > 2.0s!).

Key invariants (LCK-06):
  1. alert_state ('NORMAL' | 'EDGE_TRIGGERED') is strictly separated from
     verification_mode ('SINGLE_MODEL_YOLO' | 'YOLO_BLAZEFACE_SEQ' | 'DUAL_MODEL_FUSED').
  2. Total blind hold (cache_ttl + gap_hold) is HARD-CLAMPED to <= 0.8s, so a single
     transient false-positive frame can NEVER stack up past the 2.0s DROWSY or 1.8s YAWNING threshold.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple


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


class SmokingFusionChannel:
    """Sliding hit-window + MQ-135 rapid smoke-delta fusion for reliable smoking alerts.

    Cigarettes are tiny and intermittently occluded by fingers, so requiring an unbroken 1.2s
    continuous YOLO lock misses real smoking. This channel triggers when either:
      - >= 2 visual YOLO smoking hits occur within a rolling 2.5s window, OR
      - MQ-135 rises >= +100 ppm above its 30s rolling baseline (post-warmup) alongside >= 1 visual hit.
    """

    def __init__(
        self,
        window_s: float = 2.5,
        min_hits: int = 2,
        mq135_delta_ppm: float = 100.0,
        hold_s: float = 0.75,
    ):
        self.name = "smoking"
        self.alert_type = "SMOKING"
        self.window_s = max(1.0, float(window_s))
        self.min_hits = max(1, int(min_hits))
        self.mq135_delta_ppm = max(40.0, float(mq135_delta_ppm))
        self.max_blind_hold_s = max(0.05, min(0.8, float(hold_s)))
        self._hits: Deque[float] = deque(maxlen=16)
        self._mq_samples: Deque[Tuple[float, float]] = deque(maxlen=120)
        self._baseline_ppm: Optional[float] = None
        self._last_trigger_ts: Optional[float] = None

    def reset(self) -> None:
        self._hits.clear()
        self._last_trigger_ts = None

    def observe_mq135(self, co2_ppm: Optional[float], now_mono: float, warmup: bool = False) -> float:
        """Update 30s slow baseline of MQ-135 (post-warmup) and return current positive delta_ppm."""
        if warmup or co2_ppm is None:
            return 0.0
        val = float(co2_ppm)
        if val <= 50.0:
            return 0.0
        self._mq_samples.append((now_mono, val))
        cutoff = now_mono - 30.0
        while self._mq_samples and self._mq_samples[0][0] < cutoff:
            self._mq_samples.popleft()
        # Use lower quartile of last 30s as clean cabin baseline so a sudden puff shows clear delta
        vals = sorted(v for _, v in self._mq_samples)
        if len(vals) >= 4:
            self._baseline_ppm = vals[len(vals) // 4]
        elif self._baseline_ppm is None:
            self._baseline_ppm = val
        return max(0.0, val - float(self._baseline_ppm or val))

    def update(
        self,
        observed_positive: Optional[bool],
        now_mono: float,
        verification_mode: str,
        *,
        co2_ppm: Optional[float] = None,
        mq135_warmup: bool = False,
        visual_soft_hit: bool = False,
    ) -> FusionDecision:
        delta_ppm = self.observe_mq135(co2_ppm, now_mono, warmup=mq135_warmup)
        cutoff = now_mono - self.window_s
        while self._hits and self._hits[0] < cutoff:
            self._hits.popleft()

        if observed_positive is True:
            self._hits.append(now_mono)

        mq_smoke_spike = delta_ppm >= self.mq135_delta_ppm
        hit_count = len(self._hits)
        dur = round(max(0.0, self._hits[-1] - self._hits[0]), 3) if hit_count >= 2 else 0.0

        if hit_count >= self.min_hits:
            self._last_trigger_ts = now_mono
            reason = (
                f"SMOKING_YOLO+MQ135(hits={hit_count},+{delta_ppm:.0f}ppm)"
                if mq_smoke_spike
                else f"SMOKING_WINDOW(hits={hit_count}/{self.min_hits} in {self.window_s:.1f}s)"
            )
            return FusionDecision(
                channel="smoking",
                alert_state="EDGE_TRIGGERED",
                alert_type="SMOKING",
                verification_mode=verification_mode,
                duration_s=max(dur, 0.5),
                threshold_s=self.window_s,
                in_blind_hold=False,
                reason=reason,
            )

        if mq_smoke_spike and (observed_positive is True or visual_soft_hit or hit_count >= 1):
            self._last_trigger_ts = now_mono
            return FusionDecision(
                channel="smoking",
                alert_state="EDGE_TRIGGERED",
                alert_type="SMOKING",
                verification_mode=verification_mode,
                duration_s=1.0,
                threshold_s=self.window_s,
                in_blind_hold=False,
                reason=f"SMOKING_YOLO+MQ135(+{delta_ppm:.0f}ppm)",
            )

        # Short bounded hold <= 0.8s between inference ticks
        if observed_positive is None and self._last_trigger_ts is not None:
            gap = now_mono - self._last_trigger_ts
            if gap <= self.max_blind_hold_s and hit_count >= 1:
                return FusionDecision(
                    channel="smoking",
                    alert_state="EDGE_TRIGGERED",
                    alert_type="SMOKING",
                    verification_mode=verification_mode,
                    duration_s=max(dur, 0.5),
                    threshold_s=self.window_s,
                    in_blind_hold=True,
                    reason=f"SMOKING_HOLD({gap:.2f}s<={self.max_blind_hold_s:.2f}s)",
                )

        if observed_positive is False and hit_count == 0:
            self._last_trigger_ts = None

        return FusionDecision(
            channel="smoking",
            alert_state="NORMAL",
            alert_type="NORMAL",
            verification_mode=verification_mode,
            duration_s=dur,
            threshold_s=self.window_s,
            in_blind_hold=False,
            reason=f"SMOKING_WATCH(hits={hit_count}/{self.min_hits})" if hit_count > 0 else "NORMAL",
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
        self.smoking_channel = SmokingFusionChannel(window_s=2.5, min_hits=2, mq135_delta_ppm=100.0, hold_s=hold)
        self.channels: Dict[str, ChannelTimer] = {
            "eye": ChannelTimer("eye", "DROWSY", eye_threshold_s, hold),
            "mouth": ChannelTimer("mouth", "YAWNING", yawn_threshold_s, hold),
            "distracted": ChannelTimer("distracted", "DISTRACTED", distracted_threshold_s, hold),
            "smoking": ChannelTimer("smoking", "SMOKING", behavior_threshold_s, hold),
            "phone": ChannelTimer("phone", "PHONE", behavior_threshold_s, hold),
        }

    def reset_all(self) -> None:
        for ch in self.channels.values():
            ch.reset()
        self.smoking_channel.reset()

    def update(
        self,
        observations: Dict[str, Optional[bool]],
        now_mono: float,
        verification_mode: str = "SINGLE_MODEL_YOLO",
        *,
        co2_ppm: Optional[float] = None,
        mq135_warmup: bool = False,
        smoking_soft_hit: bool = False,
    ) -> Tuple[FusionDecision, Dict[str, FusionDecision]]:
        decisions: Dict[str, FusionDecision] = {}
        for key, timer in self.channels.items():
            if key == "smoking":
                decisions[key] = self.smoking_channel.update(
                    observations.get("smoking"),
                    now_mono,
                    verification_mode,
                    co2_ppm=co2_ppm,
                    mq135_warmup=mq135_warmup,
                    visual_soft_hit=smoking_soft_hit,
                )
            else:
                decisions[key] = timer.update(observations.get(key), now_mono, verification_mode)

        # Priority order: DROWSY > YAWNING > DISTRACTED > PHONE > SMOKING > NORMAL
        for key in ("eye", "mouth", "distracted", "phone", "smoking"):
            d = decisions[key]
            if d.alert_state == "EDGE_TRIGGERED":
                return d, decisions
        return decisions["eye"], decisions
