"""Server Intelligence Engine: Trip & Safety Score (Haversine + real speed==0 idle),
Watchdog & Anti-Tamper (compatible with real Pi/ESP32 schema), Anti-Storm Incident Deduplication.
"""
from __future__ import annotations

import math
import time
import uuid
from typing import Optional

from server_ai.server_core import config, database
from server_ai.server_core.sse_broker import broker
from server_ai.server_core.webhooks import send_webhook


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2.0 * r * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------------------
# Incidents (with cooldown/deduplication to prevent Incident Storms!)
# ---------------------------------------------------------------------------
def raise_incident(
    vehicle_id: str,
    severity: str,
    category: str,
    title: str,
    detail: dict,
    bypass_cooldown: bool = False,
) -> Optional[dict]:
    now = time.time()
    conn = database.get_conn()
    if not bypass_cooldown:
        # 1. Skip if an identical OPEN incident already exists within cooldown window
        cd = conn.execute(
            "SELECT last_raised_at FROM incident_cooldowns WHERE vehicle_id=? AND category=?",
            (vehicle_id, category),
        ).fetchone()
        if cd and (now - float(cd["last_raised_at"])) < config.INCIDENT_COOLDOWN_S:
            return None

    incident_id = f"INC_{int(now * 1000)}_{uuid.uuid4().hex[:6]}"
    with database.tx() as c:
        c.execute(
            """INSERT INTO incidents (incident_id, vehicle_id, severity, category, title, detail_json, status)
               VALUES (?,?,?,?,?,?,'OPEN')""",
            (incident_id, vehicle_id, severity, category, title, database.dumps(detail)),
        )
        c.execute(
            """INSERT INTO incident_cooldowns (vehicle_id, category, last_raised_at)
               VALUES (?,?,?)
               ON CONFLICT(vehicle_id, category) DO UPDATE SET last_raised_at=excluded.last_raised_at""",
            (vehicle_id, category, now),
        )
    payload = {
        "incident_id": incident_id,
        "vehicle_id": vehicle_id,
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
    }
    broker.publish("incident", payload)
    send_webhook(title, {"vehicle_id": vehicle_id, "category": category, **detail})
    return payload


def ack_incident(incident_id: str, ack_by: str, note: str = "") -> dict:
    with database.tx() as conn:
        conn.execute(
            "UPDATE incidents SET status='ACKNOWLEDGED', ack_by=?, note=? WHERE incident_id=?",
            (ack_by, note, incident_id),
        )
    broker.publish("incident_update", {"incident_id": incident_id, "status": "ACKNOWLEDGED"})
    return {"incident_id": incident_id, "status": "ACKNOWLEDGED"}


def resolve_incident(incident_id: str, note: str = "") -> dict:
    with database.tx() as conn:
        conn.execute(
            "UPDATE incidents SET status='RESOLVED', note=?, resolved_at=? WHERE incident_id=?",
            (note, time.time(), incident_id),
        )
    broker.publish("incident_update", {"incident_id": incident_id, "status": "RESOLVED"})
    return {"incident_id": incident_id, "status": "RESOLVED"}


def list_incidents(status: Optional[str] = None, vehicle_id: Optional[str] = None) -> list:
    conn = database.get_conn()
    q = "SELECT * FROM incidents WHERE 1=1"
    params = []
    if status:
        q += " AND status=?"
        params.append(status)
    if vehicle_id:
        q += " AND vehicle_id=?"
        params.append(vehicle_id)
    q += " ORDER BY created_at DESC LIMIT 500"
    rows = database.rows_to_list(conn.execute(q, params).fetchall())
    for r in rows:
        r["detail"] = database.loads(r.pop("detail_json", None))
    return rows


# ---------------------------------------------------------------------------
# Field extractors compatible with BOTH real Pi/ESP32 AND simulator payloads
# ---------------------------------------------------------------------------
def extract_co2_ppm(telemetry: dict) -> Optional[float]:
    mq = telemetry.get("mq135") or {}
    val = mq.get("co2_ppm")
    if val is None:
        val = mq.get("mq135_ppm")
    if val is None:
        val = mq.get("ppm")
    return float(val) if val is not None else None


def extract_pi_temp_c(telemetry: dict) -> Optional[float]:
    pi = telemetry.get("pi") or {}
    hw = telemetry.get("hardware") or {}
    for src in (pi, hw):
        for k in ("cpu_temp_c", "soc_temp_c", "temp_c", "temp", "temperature_c"):
            if src.get(k) is not None:
                return float(src[k])
    return None


def extract_face_detected(telemetry: dict) -> Optional[bool]:
    cam = telemetry.get("camera") or {}
    if isinstance(cam.get("face_detected"), bool):
        return cam["face_detected"]
    if cam.get("driver_state") is not None:
        return cam.get("driver_state") != "NO_FACE"
    ds = telemetry.get("driver_state") or {}
    if isinstance(ds.get("face_detected"), bool):
        return ds["face_detected"]
    # If neither field is present, return None (unknown) rather than falsely assuming tamper!
    return None


# ---------------------------------------------------------------------------
# Watchdog / Anti-Tamper
# ---------------------------------------------------------------------------
def run_watchdog_checks(vehicle_id: str, telemetry: dict) -> None:
    pi = telemetry.get("pi") or {}
    hw = telemetry.get("hardware") or {}
    gps = telemetry.get("gps") or {}
    esp = telemetry.get("esp") or {}
    speed_kmh = float(gps.get("speed_kmh") or 0.0)
    now = time.time()

    # 1) Camera tamper check: ONLY triggers when face_detected is explicitly False while moving >= 15 km/h
    face_detected = extract_face_detected(telemetry)
    if speed_kmh >= config.TAMPER_MIN_SPEED_KMH and face_detected is not None:
        vehicle = database.get_vehicle(vehicle_id) or {}
        last_face_seen_at = vehicle.get("last_face_seen_at") or now
        if face_detected:
            database.upsert_vehicle(vehicle_id, last_face_seen_at=now)
        else:
            if (now - last_face_seen_at) > config.CAMERA_NO_FACE_TAMPER_S:
                raise_incident(
                    vehicle_id,
                    "HIGH",
                    "CAMERA_TAMPER_SUSPECTED",
                    f"Nghi ngo che/xoay camera tren xe {vehicle_id} khi dang chay",
                    {"speed_kmh": speed_kmh, "seconds_without_face": round(now - last_face_seen_at, 1)},
                )
    else:
        database.upsert_vehicle(vehicle_id, last_face_seen_at=now)

    # 2) ESP32 brownout
    hw_events = esp.get("hw_events") or telemetry.get("hw_events") or []
    if esp.get("brownout") or esp.get("event") == "ESP_BROWNOUT" or "ESP_BROWNOUT" in hw_events:
        raise_incident(
            vehicle_id,
            "MEDIUM",
            "ESP_BROWNOUT",
            f"Sut ap / ESP32 reset bat thuong tren xe {vehicle_id}",
            {"esp": esp},
        )

    # 3) Pi Overheat / Throttled
    temp = extract_pi_temp_c(telemetry)
    thermal_level = int(pi.get("thermal_level") or hw.get("thermal_level") or 0)
    throttled = pi.get("throttled") if pi.get("throttled") is not None else hw.get("throttled")
    throttled_active = throttled not in (None, "", "0x0", "0", 0, False)
    if (temp is not None and temp >= config.PI_THERMAL_CRIT2_C) or thermal_level >= 2 or throttled_active:
        raise_incident(
            vehicle_id,
            "HIGH",
            "PI_OVERHEAT",
            f"Raspberry Pi tren xe {vehicle_id} qua nhiet hoac bi bop xung",
            {"temp_c": temp, "thermal_level": thermal_level, "throttled": throttled},
        )

    # 4) CO2 critical
    co2 = extract_co2_ppm(telemetry)
    if co2 is not None and co2 >= config.MQ135_CRIT_PPM:
        raise_incident(
            vehicle_id,
            "HIGH",
            "CO2_CRIT",
            f"Nong do CO2 nguy hiem tren xe {vehicle_id}",
            {"co2_ppm": co2},
        )


def check_offline_watchdog() -> list:
    conn = database.get_conn()
    now = time.time()
    rows = database.rows_to_list(
        conn.execute("SELECT vehicle_id, last_seen, gps_lat, gps_lon, xyz_lat, xyz_lon, status, is_muted, mute_until FROM vehicles").fetchall()
    )
    newly_offline = []
    for v in rows:
        # Auto-clear expired mute flag
        if v.get("is_muted") and v.get("mute_until") and now >= float(v["mute_until"]):
            database.upsert_vehicle(v["vehicle_id"], is_muted=0)

        last_seen = float(v.get("last_seen") or 0.0)
        if (now - last_seen) > config.PI_OFFLINE_WATCHDOG_S and v.get("status") != "OFFLINE":
            database.upsert_vehicle(v["vehicle_id"], status="OFFLINE")
            raise_incident(
                v["vehicle_id"],
                "HIGH",
                "VEHICLE_OFFLINE",
                f"Mat tin hieu xe {v['vehicle_id']}",
                {
                    "last_seen_s_ago": round(now - last_seen, 1),
                    "last_lat": v.get("gps_lat") or v.get("xyz_lat"),
                    "last_lon": v.get("gps_lon") or v.get("xyz_lon"),
                },
            )
            newly_offline.append(v["vehicle_id"])
    return newly_offline


# ---------------------------------------------------------------------------
# Trip & Safety Score (with real Haversine distance + real speed==0 idle timeout)
# ---------------------------------------------------------------------------
def get_or_start_trip(vehicle_id: str, driver_id: Optional[str], driver_name: Optional[str]) -> str:
    conn = database.get_conn()
    row = conn.execute(
        "SELECT trip_id FROM trips WHERE vehicle_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
        (vehicle_id,),
    ).fetchone()
    if row:
        trip_id = row["trip_id"]
        database.upsert_vehicle(vehicle_id, current_trip_id=trip_id)
        return trip_id
    trip_id = f"TRIP_{vehicle_id}_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    with database.tx() as c:
        c.execute(
            "INSERT INTO trips (trip_id, vehicle_id, driver_id, driver_name, started_at, status) VALUES (?,?,?,?,?,'ACTIVE')",
            (trip_id, vehicle_id, driver_id, driver_name, time.time()),
        )
    database.upsert_vehicle(vehicle_id, current_trip_id=trip_id, last_moving_at=time.time())
    broker.publish("trip_started", {"trip_id": trip_id, "vehicle_id": vehicle_id})
    return trip_id


def update_trip_motion(
    vehicle_id: str,
    trip_id: str,
    prev_lat: Optional[float],
    prev_lon: Optional[float],
    prev_valid: bool,
    lat: Optional[float],
    lon: Optional[float],
    gps_valid: bool,
    speed_kmh: float,
) -> None:
    now = time.time()
    if speed_kmh >= config.TRIP_MOVING_SPEED_KMH:
        database.upsert_vehicle(vehicle_id, last_moving_at=now)
    if gps_valid and prev_valid and None not in (prev_lat, prev_lon, lat, lon):
        delta_km = _haversine_km(float(prev_lat), float(prev_lon), float(lat), float(lon))
        # Ignore GPS jump glitches (> 2 km in a single 1.5s tick) and micro-jitter (< 5m)
        if 0.005 <= delta_km <= 2.0:
            with database.tx() as conn:
                conn.execute(
                    "UPDATE trips SET distance_km = ROUND(distance_km + ?, 4) WHERE trip_id=?",
                    (delta_km, trip_id),
                )


def check_idle_trips() -> list:
    """Close active trips when vehicle is parked (speed < threshold) OR offline > TRIP_IDLE_TIMEOUT_MIN."""
    conn = database.get_conn()
    now = time.time()
    timeout_s = config.TRIP_IDLE_TIMEOUT_MIN * 60.0
    active = database.rows_to_list(conn.execute("SELECT trip_id, vehicle_id, started_at FROM trips WHERE status='ACTIVE'").fetchall())
    ended = []
    for t in active:
        v = database.get_vehicle(t["vehicle_id"]) or {}
        last_moving = float(v.get("last_moving_at") or t.get("started_at") or now)
        last_seen = float(v.get("last_seen") or now)
        if (now - last_moving) > timeout_s or (now - last_seen) > timeout_s:
            end_trip(t["trip_id"], t["vehicle_id"])
            ended.append(t["trip_id"])
    return ended


def end_trip(trip_id: str, vehicle_id: Optional[str] = None) -> None:
    with database.tx() as conn:
        conn.execute(
            "UPDATE trips SET status='ENDED', ended_at=? WHERE trip_id=? AND status='ACTIVE'",
            (time.time(), trip_id),
        )
        if vehicle_id:
            conn.execute(
                "UPDATE vehicles SET current_trip_id=NULL WHERE vehicle_id=? AND current_trip_id=?",
                (vehicle_id, trip_id),
            )
    broker.publish("trip_ended", {"trip_id": trip_id, "vehicle_id": vehicle_id})


def apply_violation_penalty(trip_id: Optional[str], alert_type: str) -> Optional[float]:
    if not trip_id:
        return None
    penalty = float(config.SAFETY_SCORE_PENALTY.get(alert_type, 2.0))
    with database.tx() as conn:
        conn.execute(
            "UPDATE trips SET safety_score = MAX(0, safety_score - ?), violation_count = violation_count + 1 WHERE trip_id=?",
            (penalty, trip_id),
        )
        row = conn.execute("SELECT safety_score FROM trips WHERE trip_id=?", (trip_id,)).fetchone()
    score = float(row["safety_score"]) if row else None
    broker.publish(
        "trip_score_update",
        {"trip_id": trip_id, "safety_score": score, "penalty": penalty, "alert_type": alert_type},
    )
    return score


def refund_violation_penalty(trip_id: Optional[str], alert_type: str) -> Optional[float]:
    """Refund Safety Score penalty when human operator marks a violation as HUMAN_FALSE_POSITIVE."""
    if not trip_id:
        return None
    penalty = float(config.SAFETY_SCORE_PENALTY.get(alert_type, 2.0))
    with database.tx() as conn:
        conn.execute(
            "UPDATE trips SET safety_score = MIN(?, safety_score + ?), "
            "violation_count = MAX(0, violation_count - 1) WHERE trip_id=?",
            (float(config.SAFETY_SCORE_BASE), penalty, trip_id),
        )
        row = conn.execute("SELECT safety_score FROM trips WHERE trip_id=?", (trip_id,)).fetchone()
    score = float(row["safety_score"]) if row else None
    broker.publish(
        "trip_score_update",
        {"trip_id": trip_id, "safety_score": score, "refund": penalty, "alert_type": alert_type},
    )
    return score


def get_drivers_human_summary() -> list:
    """Human-centric driver coaching & fairness summary (trips, safety score, AI vs human review, MUTE reactions)."""
    conn = database.get_conn()
    trips = database.rows_to_list(conn.execute("SELECT * FROM trips ORDER BY started_at DESC LIMIT 500").fetchall())
    viols = database.rows_to_list(conn.execute("SELECT * FROM violations ORDER BY created_at DESC LIMIT 1000").fetchall())
    dism = database.rows_to_list(conn.execute("SELECT * FROM alert_dismissals ORDER BY created_at DESC LIMIT 1000").fetchall())

    by_driver: dict = {}

    def _slot(did: Optional[str], dname: Optional[str], vid: Optional[str]) -> dict:
        key = (did or dname or vid or "UNKNOWN").strip()
        if key not in by_driver:
            by_driver[key] = {
                "driver_key": key,
                "driver_name": dname or did or key,
                "vehicles": set(),
                "trip_count": 0,
                "scores": [],
                "ai_confirmed": 0,
                "ai_rejected": 0,
                "human_confirmed": 0,
                "human_false_positive": 0,
                "pending_human_review": 0,
                "mute_count": 0,
                "reaction_ms": [],
            }
        if vid:
            by_driver[key]["vehicles"].add(vid)
        return by_driver[key]

    for t in trips:
        s = _slot(t.get("driver_id"), t.get("driver_name"), t.get("vehicle_id"))
        s["trip_count"] += 1
        if t.get("safety_score") is not None:
            s["scores"].append(float(t["safety_score"]))

    for v in viols:
        s = _slot(v.get("driver_id"), v.get("driver_name"), v.get("vehicle_id"))
        cnn = v.get("cnn_status") or "PENDING"
        hr = v.get("human_review_status") or "PENDING_REVIEW"
        if cnn == "SERVER_CNN_CONFIRMED":
            s["ai_confirmed"] += 1
        elif cnn == "SERVER_CNN_REJECTED":
            s["ai_rejected"] += 1
        if hr == "HUMAN_CONFIRMED":
            s["human_confirmed"] += 1
        elif hr == "HUMAN_FALSE_POSITIVE":
            s["human_false_positive"] += 1
        elif cnn == "SERVER_CNN_CONFIRMED":
            s["pending_human_review"] += 1

    for d in dism:
        s = _slot(d.get("driver_id"), None, d.get("vehicle_id"))
        s["mute_count"] += 1
        if d.get("reaction_time_ms") is not None:
            s["reaction_ms"].append(float(d["reaction_time_ms"]))

    out = []
    for s in by_driver.values():
        avg_score = round(sum(s["scores"]) / len(s["scores"]), 1) if s["scores"] else 100.0
        avg_react = round(sum(s["reaction_ms"]) / len(s["reaction_ms"]), 0) if s["reaction_ms"] else None
        if avg_score >= 85.0:
            coaching = "AN_TOAN"
        elif avg_score >= 65.0:
            coaching = "CAN_NHAC_NHO"
        else:
            coaching = "NGUY_CO_CAO"
        out.append(
            {
                "driver_key": s["driver_key"],
                "driver_name": s["driver_name"],
                "vehicles": sorted(s["vehicles"]),
                "trip_count": s["trip_count"],
                "avg_safety_score": avg_score,
                "ai_confirmed": s["ai_confirmed"],
                "ai_rejected": s["ai_rejected"],
                "human_confirmed": s["human_confirmed"],
                "human_false_positive": s["human_false_positive"],
                "pending_human_review": s["pending_human_review"],
                "mute_count": s["mute_count"],
                "avg_reaction_ms": avg_react,
                "coaching_level": coaching,
            }
        )
    out.sort(key=lambda x: x["avg_safety_score"])
    return out


def list_trips(vehicle_id: Optional[str] = None, limit: int = 100) -> list:
    conn = database.get_conn()
    if vehicle_id:
        rows = conn.execute(
            "SELECT * FROM trips WHERE vehicle_id=? ORDER BY started_at DESC LIMIT ?", (vehicle_id, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM trips ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return database.rows_to_list(rows)
