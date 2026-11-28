"""Alert Dismissal & Mute Abuse Tracking (fixed active trip_id, mute_count, reaction grading)."""
from __future__ import annotations

import time
import uuid
from typing import Optional

from server_ai.server_core import config, database
from server_ai.server_core.sse_broker import broker

# Positive driver status & mode actions (via BLE/App) are NOT alarm mutes and MUST NOT trigger MUTE_ABUSE!
NON_MUTE_DRIVER_ACTIONS = frozenset({
    "ACK_AWAKE",
    "REST_BREAK_START",
    "REST_BREAK_END",
    "MODE_AUTO",
    "MODE_TEMP_PAUSE_30M",
    "MODE_REST_SLEEP_OFF",
    "MODE_PAUSE_30M",
    "MODE_REST_OFF",
})


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
    mute_duration_s: Optional[int] = config.MUTE_DURATION_DEFAULT_S,
    gps: Optional[dict] = None,
    event_id: Optional[str] = None,
    dedup_window_s: float = 0.0,
) -> dict:
    gps = gps or {}
    if dismiss_source not in config.DISMISS_SOURCES:
        dismiss_source = "PHYSICAL_MUTE_BUTTON"
    now_s = time.time()
    now_ms = int(now_s * 1000)
    dismissed_at_ms = dismissed_at_ms or now_ms
    alert_norm = str(dismissed_alert_type or "").strip().upper()
    is_non_mute_action = alert_norm in NON_MUTE_DRIVER_ACTIONS
    eff_mute_s = 0 if is_non_mute_action else int(config.MUTE_DURATION_DEFAULT_S if mute_duration_s is None else mute_duration_s)
    reaction_time_ms = max(0, dismissed_at_ms - int(alert_started_at_ms)) if alert_started_at_ms else None
    reaction_grade = _grade_reaction(reaction_time_ms)
    event_id = event_id or f"MUTE_{dismissed_at_ms}_{uuid.uuid4().hex[:6]}"

    inserted = False
    with database.tx() as c:
        # Optional deduplication for real MUTE events only (never swallow ACK_AWAKE / REST_BREAK_*)
        if dedup_window_s > 0 and not is_non_mute_action:
            recent = c.execute(
                "SELECT event_id, trip_id, dismiss_source, dismissed_alert_type, mute_duration_s "
                "FROM alert_dismissals WHERE vehicle_id=? AND created_at>=? "
                "AND UPPER(COALESCE(dismissed_alert_type,'')) NOT IN ('ACK_AWAKE','REST_BREAK_START','REST_BREAK_END') "
                "ORDER BY created_at DESC LIMIT 1",
                (vehicle_id, now_s - float(dedup_window_s)),
            ).fetchone()
            if recent:
                return {
                    "event_id": recent["event_id"],
                    "vehicle_id": vehicle_id,
                    "trip_id": recent["trip_id"],
                    "dismiss_source": recent["dismiss_source"],
                    "dismissed_alert_type": recent["dismissed_alert_type"],
                    "reaction_time_ms": reaction_time_ms,
                    "reaction_grade": reaction_grade,
                    "mute_duration_s": int(recent["mute_duration_s"] or eff_mute_s),
                    "deduplicated": True,
                }

        trip_row = c.execute(
            "SELECT trip_id, driver_id, driver_name FROM trips WHERE vehicle_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
            (vehicle_id,),
        ).fetchone()
        trip_id = trip_row["trip_id"] if trip_row else None
        # Inherit driver identity from active trip/vehicle so driver summary doesn't split into 2 rows (BUG-13!)
        eff_driver = driver_id
        if not eff_driver and trip_row:
            eff_driver = trip_row["driver_id"] or trip_row["driver_name"]
        if not eff_driver:
            vrow = c.execute(
                "SELECT current_driver_id, current_driver_name FROM vehicles WHERE vehicle_id=?",
                (vehicle_id,),
            ).fetchone()
            if vrow:
                eff_driver = vrow["current_driver_id"] or vrow["current_driver_name"]

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
                eff_driver,
                dismiss_source,
                dismissed_alert_type,
                related_violation_id,
                reaction_time_ms,
                reaction_grade,
                eff_mute_s,
                gps.get("lat"),
                gps.get("lon"),
                gps.get("speed_kmh"),
            ),
        )
        inserted = cur.rowcount > 0
        if inserted and trip_id and not is_non_mute_action:
            c.execute("UPDATE trips SET mute_count = mute_count + 1 WHERE trip_id=?", (trip_id,))
        if not is_non_mute_action:
            c.execute(
                "UPDATE vehicles SET is_muted=1, mute_until=? WHERE vehicle_id=?",
                (now_s + eff_mute_s, vehicle_id),
            )

    record = {
        "event_id": event_id,
        "vehicle_id": vehicle_id,
        "trip_id": trip_id,
        "dismiss_source": dismiss_source,
        "dismissed_alert_type": dismissed_alert_type,
        "reaction_time_ms": reaction_time_ms,
        "reaction_grade": reaction_grade,
        "mute_duration_s": eff_mute_s,
    }
    if inserted:
        broker.publish("alert_dismissed", record)
        # Never accuse driver of MUTE_ABUSE when a remote operator silences the speaker from Dashboard (F-027)
        if not is_non_mute_action and dismiss_source != "REMOTE_OPERATOR":
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
        "SELECT COUNT(*) AS c FROM alert_dismissals WHERE vehicle_id=? AND created_at>=? "
        "AND UPPER(COALESCE(dismiss_source,'')) != 'REMOTE_OPERATOR' "
        "AND UPPER(COALESCE(dismissed_alert_type,'')) NOT IN ('ACK_AWAKE','REST_BREAK_START','REST_BREAK_END')",
        (vehicle_id, now_s - config.MUTE_ABUSE_WINDOW_S),
    ).fetchone()["c"]
    recent_recur = conn.execute(
        "SELECT COUNT(*) AS c FROM alert_dismissals WHERE vehicle_id=? AND created_at>=? "
        "AND UPPER(COALESCE(dismiss_source,'')) != 'REMOTE_OPERATOR' "
        "AND UPPER(COALESCE(dismissed_alert_type,'')) NOT IN ('ACK_AWAKE','REST_BREAK_START','REST_BREAK_END')",
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
