"""Pure-stdlib cross-system contract validator for PI_AI (Pi <-> ESP32 <-> Server of AI)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

SCHEMA_PATH = Path(__file__).resolve().parent / "pi_server_schema.json"


def load_contract() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_telemetry_payload(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return False, ["payload must be a JSON object"]
    contract = load_contract()["schemas"]["TelemetryPayload"]
    for req in contract["required"]:
        if req not in payload or payload[req] is None:
            errors.append(f"missing required field: {req}")

    cam = payload.get("camera")
    if isinstance(cam, dict):
        if "face_detected" not in cam or not isinstance(cam["face_detected"], bool):
            errors.append("camera.face_detected must be explicit bool (prevents false tamper alarms)")
        if "is_stale" not in cam or not isinstance(cam["is_stale"], bool):
            errors.append("camera.is_stale must be explicit bool")
    else:
        errors.append("camera must be a dict")

    mq = payload.get("mq135")
    if isinstance(mq, dict):
        if "co2_ppm" not in mq or mq["co2_ppm"] is None:
            errors.append("mq135.co2_ppm is required")
    else:
        errors.append("mq135 must be a dict")

    gps = payload.get("gps")
    if isinstance(gps, dict):
        if "is_valid" not in gps or not isinstance(gps["is_valid"], bool):
            errors.append("gps.is_valid must be bool")
        if "speed_kmh" not in gps or gps["speed_kmh"] is None:
            errors.append("gps.speed_kmh is required")
    else:
        errors.append("gps must be a dict")

    pi = payload.get("pi")
    if isinstance(pi, dict):
        if "thermal_level" not in pi:
            errors.append("pi.thermal_level is required")
        if "throttled" not in pi:
            errors.append("pi.throttled is required")
    else:
        errors.append("pi must be a dict")

    ds = payload.get("driver_state")
    if isinstance(ds, dict):
        for k in ("alert_state", "alert_type", "verification_mode"):
            if not ds.get(k):
                errors.append(f"driver_state.{k} is required")
        cal = ds.get("calibration")
        if cal is not None:
            if not isinstance(cal, dict):
                errors.append("driver_state.calibration must be a dict when provided")
            else:
                def _is_num(v: Any) -> bool:
                    return isinstance(v, (int, float)) and not isinstance(v, bool)

                st = cal.get("state")
                if st is not None and st not in ("IDLE", "RUNNING", "DONE", "FAILED"):
                    errors.append(f"invalid driver_state.calibration.state: {st}")
                if "mode" in cal and cal["mode"] is not None and cal["mode"] not in ("BOOT", "APP_LOCK"):
                    errors.append(f"invalid driver_state.calibration.mode: {cal['mode']}")
                for nk in ("progress_s", "duration_s"):
                    if nk in cal and not _is_num(cal[nk]):
                        errors.append(f"driver_state.calibration.{nk} must be int/float (not bool)")
                for opt_nk in ("profile_ts", "ear_closed_threshold"):
                    if opt_nk in cal and cal[opt_nk] is not None and not _is_num(cal[opt_nk]):
                        errors.append(f"driver_state.calibration.{opt_nk} must be None or int/float (not bool)")
                if "locked" in cal and cal["locked"] is not None and not isinstance(cal["locked"], bool):
                    errors.append("driver_state.calibration.locked must be bool")
                if "glare_mode" in cal and not isinstance(cal["glare_mode"], bool):
                    errors.append("driver_state.calibration.glare_mode must be bool")

        def _is_num_v(v: Any) -> bool:
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        d_esc = ds.get("drowsy_escalation")
        if d_esc is not None:
            if not isinstance(d_esc, dict):
                errors.append("driver_state.drowsy_escalation must be a dict when provided")
            else:
                st = d_esc.get("state")
                if st is not None and st not in ("IDLE", "COUNTING", "ALARM_LATCHED", "PROBATION", "CONTINUOUS_RELAPSE"):
                    errors.append(f"invalid driver_state.drowsy_escalation.state: {st}")
                for ik in ("count", "limit"):
                    if ik in d_esc and (not isinstance(d_esc[ik], int) or isinstance(d_esc[ik], bool)):
                        errors.append(f"driver_state.drowsy_escalation.{ik} must be int")
                for nk in ("window_s", "probation_remaining_s"):
                    if nk in d_esc and d_esc[nk] is not None and not _is_num_v(d_esc[nk]):
                        errors.append(f"driver_state.drowsy_escalation.{nk} must be int/float")
                if "alarm_active" in d_esc and not isinstance(d_esc["alarm_active"], bool):
                    errors.append("driver_state.drowsy_escalation.alarm_active must be bool")

        mon = ds.get("monitoring")
        if mon is not None:
            if not isinstance(mon, dict):
                errors.append("driver_state.monitoring must be a dict when provided")
            else:
                mode = mon.get("mode")
                if mode is not None and mode not in ("AUTO", "TEMP_PAUSE_30M", "REST_SLEEP_OFF"):
                    errors.append(f"invalid driver_state.monitoring.mode: {mode}")
                pres = mon.get("presence")
                if pres is not None and pres not in ("DRIVER_PRESENT", "NO_DRIVER"):
                    errors.append(f"invalid driver_state.monitoring.presence: {pres}")
                for nk in ("pause_remaining_s", "absence_seconds", "auto_resume_speed_kmh"):
                    if nk in mon and mon[nk] is not None and not _is_num_v(mon[nk]):
                        errors.append(f"driver_state.monitoring.{nk} must be int/float")
    else:
        errors.append("driver_state must be a dict")

    return len(errors) == 0, errors


def validate_violation_burst_meta(meta: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(meta, dict):
        return False, ["meta must be a JSON object"]
    spec = load_contract()["schemas"]["ViolationBurstMeta"]
    for req in spec["required"]:
        if req not in meta or meta[req] in (None, ""):
            errors.append(f"missing required field: {req}")
    if meta.get("alert_state") not in spec["allowed_alert_states"]:
        errors.append(f"invalid alert_state: {meta.get('alert_state')}")
    if meta.get("verification_mode") not in spec["allowed_verification_modes"]:
        errors.append(f"invalid verification_mode: {meta.get('verification_mode')}")
    return len(errors) == 0, errors
