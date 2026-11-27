"""Bluetooth-Phone Hub with OTP-validated pairing (fixes viewer->driver_phone escalation) and real relay ingestion."""
from __future__ import annotations

import hmac
import secrets
import time
import uuid
from typing import Optional

from fastapi import HTTPException

from server_ai.server_core import database
from server_ai.server_core.sse_broker import broker

ACTION_TYPES = {"ACK_AWAKE", "MUTE_ALERT", "REST_BREAK_START", "REST_BREAK_END", "SOS"}
OTP_TTL_S = 300.0


def ingest_bluetooth_block(vehicle_id: str, bt: dict) -> None:
    if not bt:
        return
    phone_connected = bool(bt.get("phone_connected") or bt.get("hub_connected"))
    database.upsert_vehicle(
        vehicle_id,
        bt_phone_connected=1 if phone_connected else 0,
        current_driver_id=bt.get("driver_id"),
        current_driver_name=bt.get("driver_name"),
    )
    if phone_connected:
        _upsert_session(vehicle_id, bt)
    gps = bt.get("phone_gps") or {}
    if gps.get("is_valid") and gps.get("lat") is not None and gps.get("lon") is not None:
        database.upsert_vehicle(vehicle_id, xyz_lat=gps["lat"], xyz_lon=gps["lon"], xyz_is_sample_anchor=0)
    broker.publish("bluetooth_update", {"vehicle_id": vehicle_id, "bluetooth_phone": bt})


def _upsert_session(vehicle_id: str, bt: dict) -> None:
    conn = database.get_conn()
    device_id = bt.get("device_id") or "UNKNOWN_BT"
    row = conn.execute(
        "SELECT id FROM bluetooth_sessions WHERE vehicle_id=? AND phone_device_id=? AND status='CONNECTED'",
        (vehicle_id, device_id),
    ).fetchone()
    with database.tx() as c:
        if row:
            c.execute(
                "UPDATE bluetooth_sessions SET last_rssi_dbm=?, battery_pct=?, heart_rate_bpm=? WHERE id=?",
                (bt.get("rssi_dbm"), bt.get("phone_battery_pct"), bt.get("heart_rate_bpm"), row["id"]),
            )
        else:
            c.execute(
                """INSERT INTO bluetooth_sessions
                   (vehicle_id, driver_id, driver_name, phone_device_id, phone_name, transport,
                    last_rssi_dbm, battery_pct, heart_rate_bpm, connected_at, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'CONNECTED')""",
                (
                    vehicle_id,
                    bt.get("driver_id"),
                    bt.get("driver_name"),
                    device_id,
                    bt.get("phone_name"),
                    bt.get("transport", "BLE"),
                    bt.get("rssi_dbm"),
                    bt.get("phone_battery_pct"),
                    bt.get("heart_rate_bpm"),
                    time.time(),
                ),
            )


def disconnect_session(vehicle_id: str, device_id: str) -> None:
    with database.tx() as conn:
        conn.execute(
            "UPDATE bluetooth_sessions SET status='DISCONNECTED', disconnected_at=? "
            "WHERE vehicle_id=? AND phone_device_id=? AND status='CONNECTED'",
            (time.time(), vehicle_id, device_id),
        )
    database.upsert_vehicle(vehicle_id, bt_phone_connected=0)


def get_bluetooth_detail(vehicle_id: str) -> dict:
    conn = database.get_conn()
    sessions = database.rows_to_list(
        conn.execute(
            "SELECT * FROM bluetooth_sessions WHERE vehicle_id=? ORDER BY connected_at DESC LIMIT 50",
            (vehicle_id,),
        ).fetchall()
    )
    vehicle = database.get_vehicle(vehicle_id) or {}
    return {
        "vehicle_id": vehicle_id,
        "bt_phone_connected": bool(vehicle.get("bt_phone_connected")),
        "sessions": sessions,
    }


def issue_pair_otp(vehicle_id: str, driver_id: Optional[str], issued_by: str) -> dict:
    """Operator/Admin creates a 5-minute single-use OTP for phone pairing."""
    pair_code = secrets.token_hex(4).upper()
    expires_at = time.time() + OTP_TTL_S
    with database.tx() as conn:
        conn.execute(
            "INSERT INTO phone_pair_otps (pair_code, vehicle_id, driver_id, issued_by, expires_at, used) VALUES (?,?,?,?,?,0)",
            (pair_code, vehicle_id, driver_id, issued_by, expires_at),
        )
    return {"vehicle_id": vehicle_id, "pair_code": pair_code, "expires_at": expires_at}


def pair_phone_with_otp(vehicle_id: str, pair_code: str, driver_id: Optional[str]) -> dict:
    """Validates single-use OTP issued by operator/admin before issuing driver_phone token."""
    if not vehicle_id or not pair_code:
        raise HTTPException(status_code=400, detail="Can vehicle_id va pair_code OTP hop le")
    conn = database.get_conn()
    rows = conn.execute(
        "SELECT pair_code, vehicle_id, driver_id, expires_at, used FROM phone_pair_otps WHERE vehicle_id=? AND used=0",
        (vehicle_id,),
    ).fetchall()
    matched = None
    now_s = time.time()
    for r in rows:
        if hmac.compare_digest(str(r["pair_code"]), str(pair_code).strip().upper()):
            if float(r["expires_at"]) >= now_s:
                matched = r
                break
    if not matched:
        raise HTTPException(status_code=403, detail="pair_code khong hop le hoac da het han")

    token = secrets.token_hex(24)
    eff_driver = driver_id or matched["driver_id"]
    with database.tx() as c:
        c.execute("UPDATE phone_pair_otps SET used=1 WHERE pair_code=?", (matched["pair_code"],))
        c.execute(
            "INSERT INTO phone_pairings (vehicle_id, pair_code, token, driver_id, status) VALUES (?,?,?,?,'ACTIVE')",
            (vehicle_id, matched["pair_code"], token, eff_driver),
        )
    return {"vehicle_id": vehicle_id, "driver_id": eff_driver, "driver_phone_token": token, "role": "driver_phone"}


def phone_telemetry(vehicle_id: str, payload: dict) -> dict:
    gps = payload.get("phone_gps") or {}
    if gps.get("is_valid") and gps.get("lat") is not None and gps.get("lon") is not None:
        database.upsert_vehicle(vehicle_id, xyz_lat=gps["lat"], xyz_lon=gps["lon"], xyz_is_sample_anchor=0)
    broker.publish("phone_telemetry", {"vehicle_id": vehicle_id, **payload})
    relayed = payload.get("relay_packet")
    relayed_stored = False
    if isinstance(relayed, dict) and relayed:
        with database.tx() as conn:
            conn.execute(
                "INSERT INTO cabin_sensor_events (vehicle_id, ts, sensor_type, payload_json) VALUES (?,?,?,?)",
                (vehicle_id, time.time(), "PHONE_RELAY", database.dumps(relayed)),
            )
        relayed_stored = True
        broker.publish("phone_relay", {"vehicle_id": vehicle_id, "relay_packet": relayed})
    return {"ok": True, "received_at": time.time(), "relayed_stored": relayed_stored}


def phone_action(vehicle_id: str, action: str, detail: Optional[dict] = None) -> dict:
    if action not in ACTION_TYPES:
        raise HTTPException(status_code=400, detail=f"action khong hop le, cho phep: {sorted(ACTION_TYPES)}")
    detail = detail or {}
    if action == "SOS":
        from server_ai.server_core.analytics import raise_incident

        raise_incident(
            vehicle_id,
            "CRITICAL",
            "SOS_PRESSED",
            f"Tai xe xe {vehicle_id} bam nut SOS tu dien thoai",
            detail,
            bypass_cooldown=True,
        )
    elif action == "MUTE_ALERT":
        from server_ai.server_core.mute_tracker import record_dismissal

        record_dismissal(
            vehicle_id=vehicle_id,
            dismiss_source="PHONE_BLE",
            dismissed_alert_type=detail.get("alert_type", "DROWSY"),
            driver_id=detail.get("driver_id"),
            gps=detail.get("gps"),
        )
    else:
        with database.tx() as conn:
            conn.execute(
                "INSERT INTO cabin_sensor_events (vehicle_id, ts, sensor_type, payload_json) VALUES (?,?,?,?)",
                (vehicle_id, time.time(), f"PHONE_{action}", database.dumps(detail)),
            )
    broker.publish("phone_action", {"vehicle_id": vehicle_id, "action": action, "detail": detail})
    return {"ok": True, "action": action}


_notifications: dict[str, list] = {}


def push_notification(vehicle_id: str, message: str, level: str = "INFO") -> None:
    _notifications.setdefault(vehicle_id, []).append(
        {"id": uuid.uuid4().hex, "message": message, "level": level, "ts": time.time()}
    )


def pull_notifications(vehicle_id: str) -> list:
    return _notifications.pop(vehicle_id, [])
