"""Alert Dismissal & Mute Abuse Tracking (fixed active trip_id, mute_count, reaction grading)."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from server_ai.server_core import config, database
from server_ai.server_core.sse_broker import broker


def _grade_reaction(reaction_time_ms: Optional[int]) -> Optional[str]:
    if reaction_time_ms is None:
        return None
    if reaction_time_ms <= config.REACTION_TIME_FAST_MS:
        return "FAST"
    if reaction_time_ms <= config.REACTION_TIME_SLOW_MS:
        return "NORMAL"
    return "SLOW"


def record_dismissal(
    vehicle_id: str,
    dismiss_source: str,
    dismissed_alert_type: str,
    driver_id: Optional[str] = None,
    related_violation_id: Optional[str] = None,
    alert_started_at_ms: Optional[int] = None,
    dismissed_at_ms: Optional[int] = None,
    mute_duration_s: int = config.MUTE_DURATION_DEFAULT_S,
    gps: Optional[dict] = None,
    event_id: Optional[str] = None,
) -> dict:
    gps = gps or {}
    if dismiss_source not in config.DISMISS_SOURCES:
        dismiss_source = "PHYSICAL_MUTE_BUTTON"
    now_s = time.time()
    now_ms = int(now_s * 1000)
    dismissed_at_ms = dismissed_at_ms or now_ms
    reaction_time_ms = max(0, dismissed_at_ms - int(alert_started_at_ms)) if alert_started_at_ms else None
    reaction_grade = _grade_reaction(reaction_time_ms)
    event_id = event_id or f"MUTE_{dismissed_at_ms}_{uuid.uuid4().hex[:6]}"

    # Lookup real active trip from trips table (fixes always-None bug!)
    conn = database.get_conn()
    trip_row = conn.execute(
        "SELECT trip_id FROM trips WHERE vehicle_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
        (vehicle_id,),
    ).fetchone()
    trip_id = trip_row["trip_id"] if trip_row else None

    inserted = False
    with database.tx() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO alert_dismissals
               (event_id, vehicle_id, trip_id, driver_id, dismiss_source, dismissed_alert_type,
                related_violation_id, reaction_time_ms, reaction_grade, mute_duration_s,
                still_risky_after, gps_lat, gps_lon, speed_kmh)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?)""",
            (
                event_id,
                vehicle_id,
                trip_id,
                driver_id,
                dismiss_source,
                dismissed_alert_type,
                related_violation_id,
                reaction_time_ms,
                reaction_grade,
                int(mute_duration_s),
                gps.get("lat"),
                gps.get("lon"),
                gps.get("speed_kmh"),
            ),
        )
        inserted = cur.rowcount > 0
        if inserted and trip_id:
            c.execute("UPDATE trips SET mute_count = mute_count + 1 WHERE trip_id=?", (trip_id,))
        c.execute(
            "UPDATE vehicles SET is_muted=1, mute_until=? WHERE vehicle_id=?",
            (now_s + int(mute_duration_s), vehicle_id),
        )

    record = {
        "event_id": event_id,
        "vehicle_id": vehicle_id,
        "trip_id": trip_id,
        "dismiss_source": dismiss_source,
        "dismissed_alert_type": dismissed_alert_type,
        "reaction_time_ms": reaction_time_ms,
        "reaction_grade": reaction_grade,
        "mute_duration_s": int(mute_duration_s),
    }
    if inserted:
        broker.publish("alert_dismissed", record)
        abuse = _check_mute_abuse(vehicle_id, trip_id, dismissed_alert_type, gps.get("speed_kmh"))
        if abuse:
            record["mute_abuse"] = abuse
    return record


def mark_still_risky(event_id: str) -> None:
    with database.tx() as conn:
        conn.execute("UPDATE alert_dismissals SET still_risky_after=1 WHERE event_id=?", (event_id,))


def _check_mute_abuse(
    vehicle_id: str, trip_id: Optional[str], alert_type: str, speed_kmh: Optional[float]
) -> Optional[dict]:
    conn = database.get_conn()
    now_s = time.time()
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM alert_dismissals WHERE vehicle_id=? AND created_at>=?",
        (vehicle_id, now_s - config.MUTE_ABUSE_WINDOW_S),
    ).fetchone()["c"]
    recent_recur = conn.execute(
        "SELECT COUNT(*) AS c FROM alert_dismissals WHERE vehicle_id=? AND created_at>=?",
        (vehicle_id, now_s - config.MUTE_ABUSE_RECURRENCE_WINDOW_S),
    ).fetchone()["c"]

    high_speed_critical = (
        speed_kmh is not None
        and float(speed_kmh) > config.MUTE_ABUSE_SPEED_THRESHOLD_KMH
        and alert_type in ("DROWSY", "DROWSY_ALERT", "CO2_CRIT", "AUTO_CO2_CRIT")
    )
    rapid_recurrence = recent_recur >= 2

    if count >= config.MUTE_ABUSE_COUNT_THRESHOLD or high_speed_critical or rapid_recurrence:
        from server_ai.server_core.analytics import raise_incident

        reason = (
            "count_threshold"
            if count >= config.MUTE_ABUSE_COUNT_THRESHOLD
            else ("rapid_recurrence" if rapid_recurrence else "high_speed_critical_dismiss")
        )
        detail = {
            "reason": reason,
            "dismiss_count_in_window": count,
            "alert_type": alert_type,
            "speed_kmh": speed_kmh,
        }
        raise_incident(
            vehicle_id=vehicle_id,
            severity="CRITICAL",
            category="MUTE_ABUSE_ESCALATION",
            title=f"Nghi ngo lam dung tat canh bao tren xe {vehicle_id}",
            detail=detail,
            bypass_cooldown=True,
        )
        if trip_id:
            with database.tx() as c:
                c.execute("UPDATE trips SET mute_abuse_count = mute_abuse_count + 1 WHERE trip_id=?", (trip_id,))
        return detail
    return None
