"""Edge Alert Escalation & 3-Mode Presence Monitoring Policy for PI_AI (Pi 4 Tier-1).

Two deterministic, thread-safe, pure-Python state machines (zero external deps):
  1. DrowsyEscalationController:
     - Tracks consecutive distinct DROWSY episodes within a rolling 2-4 min window (default 180s).
     - Strikes 1 & 2: single immediate cabin warning (VOICE:1 + pip-pip) for road safety.
     - Strike >= 3 within window: latches Level-3 continuous alarm (ALARM:ON) on ESP32 #1
       until the driver taps "Tắt loa cảnh báo" (DISMISS_DROWSY_ALARM) on Driver App.
     - After dismissal: enters 2-4 min PROBATION window. If driver stays awake -> resets to 0.
       If driver dozes off again during PROBATION -> triggers CONTINUOUS_RELAPSE (ALARM:CONT).
  2. MonitoringModeController:
     - ALWAYS boots in AUTO (in-memory only -> Pi power-off / reboot always resets to AUTO!).
     - Mode 1 (AUTO): monitors presence (DRIVER_PRESENT / NO_DRIVER). If NO_DRIVER continuously
       for absence_alert_s (default 20s, 15-30s), emits DRIVER_ABSENT to App/Server and
       automatically transitions to TEMP_PAUSE_30M to prevent alert spam.
     - Mode 2 (TEMP_PAUSE_30M): suppresses NO_DRIVER absence alerts for 30 min (auto-reverts
       to AUTO when timer expires), BUT if camera sees a driver (face_detected=True), ALL
       normal violation alerts still fire!
     - Mode 3 (REST_SLEEP_OFF): manual rest/sleep-in-cabin mode. Camera stays ON & reports
       presence, while ALL cabin alerts & alarms are silenced. Auto-resumes AUTO when valid
       GPS speed >= auto_resume_speed_kmh (default 15.0 km/h) continuously for >= 3.0s, and
       emits MONITORING_AUTO_RESUMED_BY_SPEED to App & Server.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

VALID_MONITORING_MODES = ("AUTO", "TEMP_PAUSE_30M", "REST_SLEEP_OFF")
VALID_DROWSY_STATES = ("IDLE", "COUNTING", "ALARM_LATCHED", "PROBATION", "CONTINUOUS_RELAPSE")


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


@dataclass(frozen=True)
class DrowsyAction:
    esp_cmd: Optional[str] = None          # None | VOICE:1 | ALARM:ON | ALARM:CONT | ALARM:OFF
    ble_notify: Optional[str] = None       # e.g. DROWSY|... or DROWSY_LEVEL3|... or DROWSY_RELAPSE|...
    server_incident: Optional[str] = None  # None | DROWSY_LEVEL3_ALARM | DROWSY_RELAPSE_CONTINUOUS
    count_incremented: bool = False
    state: str = "IDLE"
    count: int = 0


class DrowsyEscalationController:
    """3-strike drowsiness escalation + post-dismiss 2-4 min relapse probation."""

    def __init__(
        self,
        window_s: float = 180.0,
        escalation_limit: int = 3,
        episode_gap_s: float = 6.0,
        probation_s: Optional[float] = None,
    ):
        self._lock = threading.Lock()
        self.window_s = _clamp(window_s, 120.0, 240.0)
        self.escalation_limit = max(2, min(5, int(escalation_limit)))
        self.episode_gap_s = max(2.0, min(30.0, float(episode_gap_s)))
        self.probation_s = _clamp(probation_s if probation_s is not None else self.window_s, 120.0, 240.0)

        self._state: str = "IDLE"
        self._count: int = 0
        self._strike_timestamps: List[float] = []
        self._in_episode: bool = False
        self._awake_since_mono: Optional[float] = None
        self._probation_until_mono: float = 0.0
        self._last_dismiss_source: str = ""

    def configure(
        self,
        *,
        window_s: Optional[float] = None,
        escalation_limit: Optional[int] = None,
        probation_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if window_s is not None:
                self.window_s = _clamp(window_s, 120.0, 240.0)
                if probation_s is None:
                    self.probation_s = self.window_s
            if probation_s is not None:
                self.probation_s = _clamp(probation_s, 120.0, 240.0)
            if escalation_limit is not None:
                self.escalation_limit = max(2, min(5, int(escalation_limit)))
            return {
                "drowsy_window_s": self.window_s,
                "drowsy_escalation_limit": self.escalation_limit,
                "drowsy_probation_s": self.probation_s,
            }

    def reset(self) -> None:
        with self._lock:
            self._state = "IDLE"
            self._count = 0
            self._strike_timestamps.clear()
            self._in_episode = False
            self._awake_since_mono = None
            self._probation_until_mono = 0.0

    def _prune_unlocked(self, now_mono: float) -> None:
        cutoff = now_mono - self.window_s
        self._strike_timestamps = [t for t in self._strike_timestamps if t >= cutoff]
        if self._state == "COUNTING":
            self._count = len(self._strike_timestamps)
            if self._count == 0:
                self._state = "IDLE"
        elif self._state == "PROBATION":
            if now_mono >= self._probation_until_mono:
                self._state = "IDLE"
                self._count = 0
                self._strike_timestamps.clear()
                self._probation_until_mono = 0.0

    def update(
        self,
        drowsy_active: bool,
        now_mono: float,
        *,
        suppressed: bool = False,
    ) -> DrowsyAction:
        now_m = float(now_mono)
        with self._lock:
            if suppressed:
                self._in_episode = False
                self._awake_since_mono = None
                was_alarm = self._state in ("ALARM_LATCHED", "CONTINUOUS_RELAPSE")
                self._state = "IDLE"
                self._count = 0
                self._strike_timestamps.clear()
                self._probation_until_mono = 0.0
                return DrowsyAction(
                    esp_cmd="ALARM:OFF" if was_alarm else None,
                    state=self._state,
                    count=self._count,
                )

            self._prune_unlocked(now_m)

            if not drowsy_active:
                if self._in_episode:
                    if self._awake_since_mono is None:
                        self._awake_since_mono = now_m
                    elif (now_m - self._awake_since_mono) >= self.episode_gap_s:
                        self._in_episode = False
                        self._awake_since_mono = None
                return DrowsyAction(state=self._state, count=self._count)

            # drowsy_active is True
            self._awake_since_mono = None

            # Already in latched alarm or continuous relapse: keep alarm active (no double-count)
            if self._state in ("ALARM_LATCHED", "CONTINUOUS_RELAPSE"):
                self._in_episode = True
                return DrowsyAction(state=self._state, count=self._count)

            # Still inside the same ongoing drowsy episode -> do NOT increment strike counter again
            if self._in_episode:
                return DrowsyAction(state=self._state, count=self._count)

            # New distinct drowsy episode!
            self._in_episode = True

            # Relapse during 2-4 min post-dismiss probation -> IMMEDIATE CONTINUOUS ALARM!
            if self._state == "PROBATION" and now_m < self._probation_until_mono:
                self._state = "CONTINUOUS_RELAPSE"
                self._count = max(self._count + 1, self.escalation_limit + 1)
                self._strike_timestamps.append(now_m)
                return DrowsyAction(
                    esp_cmd="ALARM:CONT",
                    ble_notify=f"DROWSY_RELAPSE|Tai pham buon ngu trong {int(self.probation_s // 60)}p - BAO DONG LIEN TUC!",
                    server_incident="DROWSY_RELAPSE_CONTINUOUS",
                    count_incremented=True,
                    state=self._state,
                    count=self._count,
                )

            # Normal rolling window counting (Strikes 1..limit)
            self._strike_timestamps.append(now_m)
            self._count = len(self._strike_timestamps)
            if self._count >= self.escalation_limit:
                self._state = "ALARM_LATCHED"
                return DrowsyAction(
                    esp_cmd="ALARM:ON",
                    ble_notify=f"DROWSY_LEVEL3|Canh bao buon ngu lan {self._count}/{self.escalation_limit} - Bam Tat loa tren App",
                    server_incident="DROWSY_LEVEL3_ALARM",
                    count_incremented=True,
                    state=self._state,
                    count=self._count,
                )

            self._state = "COUNTING"
            return DrowsyAction(
                esp_cmd="VOICE:1",
                ble_notify=f"DROWSY|Buon ngu lan {self._count}/{self.escalation_limit}",
                count_incremented=True,
                state=self._state,
                count=self._count,
            )

    def dismiss_alarm(self, now_mono: float, source: str = "DRIVER_APP") -> Tuple[bool, str, Optional[str]]:
        """Driver/Admin silences Level-3/Relapse alarm -> stops speaker & enters 2-4 min probation."""
        now_m = float(now_mono)
        with self._lock:
            self._prune_unlocked(now_m)
            self._last_dismiss_source = str(source or "DRIVER_APP")
            if self._state in ("ALARM_LATCHED", "CONTINUOUS_RELAPSE"):
                prev = self._state
                self._state = "PROBATION"
                self._probation_until_mono = now_m + self.probation_s
                # Allow next distinct drowsy event after brief gap (or immediately if eyes reopen >= gap)
                self._in_episode = True
                self._awake_since_mono = None
                return (
                    True,
                    f"ALARM_DISMISSED_PROBATION_{int(self.probation_s)}S_FROM_{prev}",
                    "ALARM:OFF",
                )
            # Even if not latched, still allow stopping any playing ESP audio cleanly
            return True, "ALARM_STOP_ACK", "ALARM:OFF"

    def get_status(self, now_mono: Optional[float] = None) -> Dict[str, Any]:
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            self._prune_unlocked(now_m)
            prob_rem = (
                round(max(0.0, self._probation_until_mono - now_m), 1)
                if self._state == "PROBATION"
                else 0.0
            )
            return {
                "count": int(self._count),
                "limit": int(self.escalation_limit),
                "window_s": round(float(self.window_s), 1),
                "state": self._state,
                "alarm_active": self._state in ("ALARM_LATCHED", "CONTINUOUS_RELAPSE"),
                "probation_remaining_s": prob_rem,
            }


@dataclass(frozen=True)
class MonitoringTickResult:
    mode: str                                 # AUTO | TEMP_PAUSE_30M | REST_SLEEP_OFF
    presence: str                             # DRIVER_PRESENT | NO_DRIVER
    alerts_suppressed: bool                   # True ONLY in REST_SLEEP_OFF
    absence_suppressed: bool                  # True in TEMP_PAUSE_30M and REST_SLEEP_OFF
    event: Optional[str] = None               # DRIVER_ABSENT | MONITORING_AUTO_RESUMED_BY_SPEED | PAUSE_EXPIRED_AUTO
    event_detail: str = ""


class MonitoringModeController:
    """3-Mode Presence & Cabin Monitoring Controller (always boots in AUTO)."""

    def __init__(
        self,
        absence_alert_s: float = 20.0,
        pause_duration_s: float = 1800.0,
        auto_resume_speed_kmh: float = 15.0,
        speed_sustain_s: float = 3.0,
    ):
        self._lock = threading.Lock()
        self.absence_alert_s = _clamp(absence_alert_s, 15.0, 30.0)
        self.pause_duration_s = _clamp(pause_duration_s, 60.0, 3600.0)
        self.auto_resume_speed_kmh = _clamp(auto_resume_speed_kmh, 5.0, 40.0)
        self.speed_sustain_s = max(1.0, min(10.0, float(speed_sustain_s)))

        # Strictly in-memory: reboot / service restart ALWAYS starts in AUTO!
        self._mode: str = "AUTO"
        self._presence: str = "NO_DRIVER"
        self._no_face_since_mono: Optional[float] = None
        self._pause_until_mono: float = 0.0
        self._high_speed_since_mono: Optional[float] = None
        self._last_resume_reason: str = "BOOT_AUTO"

    def configure(
        self,
        *,
        absence_alert_s: Optional[float] = None,
        pause_duration_min: Optional[float] = None,
        auto_resume_speed_kmh: Optional[float] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if absence_alert_s is not None:
                self.absence_alert_s = _clamp(absence_alert_s, 15.0, 30.0)
            if pause_duration_min is not None:
                self.pause_duration_s = _clamp(float(pause_duration_min) * 60.0, 60.0, 3600.0)
            if auto_resume_speed_kmh is not None:
                self.auto_resume_speed_kmh = _clamp(auto_resume_speed_kmh, 5.0, 40.0)
            return {
                "absence_alert_s": self.absence_alert_s,
                "pause_duration_min": round(self.pause_duration_s / 60.0, 1),
                "auto_resume_speed_kmh": self.auto_resume_speed_kmh,
            }

    def set_mode(
        self,
        mode: str,
        now_mono: Optional[float] = None,
        source: str = "USER",
        duration_s: Optional[float] = None,
    ) -> Tuple[bool, str]:
        m = str(mode or "").strip().upper()
        if m not in VALID_MONITORING_MODES:
            return False, f"INVALID_MODE:{m}"
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            self._mode = m
            self._no_face_since_mono = None
            self._high_speed_since_mono = None
            if m == "TEMP_PAUSE_30M":
                dur = _clamp(duration_s if duration_s is not None else self.pause_duration_s, 60.0, 3600.0)
                self._pause_until_mono = now_m + dur
                self._last_resume_reason = f"MANUAL_PAUSE_{source}"
            elif m == "REST_SLEEP_OFF":
                self._pause_until_mono = 0.0
                self._last_resume_reason = f"MANUAL_REST_OFF_{source}"
            else:
                self._pause_until_mono = 0.0
                self._last_resume_reason = f"MANUAL_AUTO_{source}"
            return True, f"MODE_SET_{m}"

    def update(
        self,
        *,
        face_detected: bool,
        camera_online: bool,
        gps_valid: bool,
        speed_kmh: float,
        now_mono: float,
    ) -> MonitoringTickResult:
        now_m = float(now_mono)
        with self._lock:
            self._presence = "DRIVER_PRESENT" if face_detected else "NO_DRIVER"
            event: Optional[str] = None
            event_detail = ""

            # Mode 3 (REST_SLEEP_OFF): check GPS speed auto-resume (>= 15 km/h sustained >= 3s)
            if self._mode == "REST_SLEEP_OFF":
                if gps_valid and float(speed_kmh) >= self.auto_resume_speed_kmh:
                    if self._high_speed_since_mono is None:
                        self._high_speed_since_mono = now_m
                    elif (now_m - self._high_speed_since_mono) >= self.speed_sustain_s:
                        self._mode = "AUTO"
                        self._high_speed_since_mono = None
                        self._no_face_since_mono = None
                        self._pause_until_mono = 0.0
                        self._last_resume_reason = f"AUTO_SPEED_{float(speed_kmh):.1f}KMH"
                        event = "MONITORING_AUTO_RESUMED_BY_SPEED"
                        event_detail = (
                            f"Xe di chuyen {float(speed_kmh):.1f} km/h (>= {self.auto_resume_speed_kmh:.0f} km/h) "
                            f"-> tu dong bat lai che do giam sat AUTO"
                        )
                else:
                    self._high_speed_since_mono = None

            # Mode 2 (TEMP_PAUSE_30M): auto-revert to AUTO when 30-min timer expires
            elif self._mode == "TEMP_PAUSE_30M":
                if self._pause_until_mono > 0.0 and now_m >= self._pause_until_mono:
                    self._mode = "AUTO"
                    self._pause_until_mono = 0.0
                    self._no_face_since_mono = None
                    self._last_resume_reason = "PAUSE_30M_EXPIRED"
                    event = "PAUSE_EXPIRED_AUTO"
                    event_detail = "Het 30 phut tam tat -> quay lai che do giam sat AUTO"

            # Mode 1 (AUTO): monitor continuous absence (> 15-30s) -> alert & auto-enter TEMP_PAUSE_30M
            if self._mode == "AUTO":
                if not camera_online or face_detected:
                    self._no_face_since_mono = None
                else:
                    if self._no_face_since_mono is None:
                        self._no_face_since_mono = now_m
                    elif (now_m - self._no_face_since_mono) >= self.absence_alert_s:
                        abs_s = round(now_m - self._no_face_since_mono, 1)
                        self._mode = "TEMP_PAUSE_30M"
                        self._pause_until_mono = now_m + self.pause_duration_s
                        self._no_face_since_mono = None
                        self._last_resume_reason = f"AUTO_ABSENCE_{int(abs_s)}S"
                        event = "DRIVER_ABSENT"
                        event_detail = (
                            f"Camera khong thay tai xe {abs_s:.0f}s -> da bao App/Server "
                            f"va tu dong tam tat canh bao vang mat {int(self.pause_duration_s // 60)} phut"
                        )

            return MonitoringTickResult(
                mode=self._mode,
                presence=self._presence,
                alerts_suppressed=(self._mode == "REST_SLEEP_OFF"),
                absence_suppressed=(self._mode in ("TEMP_PAUSE_30M", "REST_SLEEP_OFF")),
                event=event,
                event_detail=event_detail,
            )

    def get_status(self, now_mono: Optional[float] = None) -> Dict[str, Any]:
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            pause_rem = (
                round(max(0.0, self._pause_until_mono - now_m), 1)
                if self._mode == "TEMP_PAUSE_30M" and self._pause_until_mono > 0.0
                else 0.0
            )
            abs_s = (
                round(max(0.0, now_m - self._no_face_since_mono), 1)
                if self._mode == "AUTO" and self._no_face_since_mono is not None
                else 0.0
            )
            return {
                "mode": self._mode,
                "presence": self._presence,
                "pause_remaining_s": pause_rem,
                "absence_seconds": abs_s,
                "auto_resume_speed_kmh": round(float(self.auto_resume_speed_kmh), 1),
                "last_resume_reason": self._last_resume_reason,
            }
