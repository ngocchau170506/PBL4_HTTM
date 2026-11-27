"""AI Central Server (Server of AI) — FastAPI application for PI_AI.

Fixes all 9 Pi<->Server contract mismatches + all 5 P0 server bugs, and adds:
  - Tier-2 DeepVerifier async CNN verification & server-side MP4 stitching
  - Multipart JPEG burst ingest (/api/v1/violation_burst)
  - Strict boot RBAC + OTP-protected phone pairing + full 401/403 audit logs
  - Anti-storm Watchdog, real Haversine Trip distance, speed==0 Trip idle close
  - Body size cap middleware (anti-OOM DoS)
"""
from __future__ import annotations

import asyncio
import contextlib
from email.parser import BytesParser
from email.policy import default as email_policy_default
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from server_ai.server_core import (
    analytics,
    auth,
    bluetooth_hub,
    commands as cmds,
    config,
    database,
    media,
    mute_tracker,
    reports,
)
from server_ai.server_core.deep_verifier import verifier_engine
from server_ai.server_core.pbl4_bridge import pbl4_bridge
from server_ai.server_core.sse_broker import broker


async def _watchdog_loop() -> None:
    last_cleanup = time.monotonic()
    while True:
        await asyncio.sleep(5.0)
        with contextlib.suppress(Exception):
            analytics.check_offline_watchdog()
            analytics.check_idle_trips()
            if (time.monotonic() - last_cleanup) >= config.CLEANUP_INTERVAL_S:
                reports.cleanup_old_data()
                last_cleanup = time.monotonic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    auth.validate_boot_tokens()
    broker.bind_loop(asyncio.get_running_loop())
    verifier_engine.start()
    pbl4_bridge.start()
    task = asyncio.create_task(_watchdog_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        pbl4_bridge.stop()
        verifier_engine.stop()
        database.close_all_connections()


app = FastAPI(title="PI_AI Server of AI", version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def enforce_body_size_limit(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            n = int(cl)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Content-Length khong hop le"})
        if n < 0:
            return JSONResponse(status_code=400, content={"detail": "Content-Length am"})
        is_upload = request.url.path in ("/api/v1/video", "/api/v1/violation_burst", "/api/v1/violation", "/api/v1/violations")
        limit = config.MAX_UPLOAD_BODY_BYTES if is_upload else config.MAX_JSON_BODY_BYTES
        if n > limit:
            return JSONResponse(status_code=413, content={"detail": f"Payload vuot gioi han {limit} bytes"})
    return await call_next(request)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _vehicle_id_of(payload: dict) -> str:
    vid = (
        payload.get("vehicle_id")
        or (payload.get("access_info") or {}).get("vehicle_id")
        or (payload.get("pi") or {}).get("vehicle_id")
        or "UNKNOWN"
    )
    return str(vid)


def _xyz_downlink(vehicle_id: str) -> dict:
    v = database.get_vehicle(vehicle_id) or {}
    return {
        "lat": v.get("xyz_lat"),
        "lon": v.get("xyz_lon"),
        "is_sample_anchor": bool(v.get("xyz_is_sample_anchor", True)),
    }


# ---------------------------------------------------------------------------
# Health & Auth Session (Login / WhoAmI)
# ---------------------------------------------------------------------------
@app.get("/api/v1/health")
def health():
    vehicles = database.list_vehicles()
    conn = database.get_conn()
    violation_count = conn.execute("SELECT COUNT(*) c FROM violations").fetchone()["c"]
    ver_name = verifier_engine._verifier.model_version
    is_real_cnn = str(ver_name).startswith("onnx-cnn:")
    return {
        "ok": True,
        "service": config.SERVICE_NAME,
        "env_profile": config.ENV_PROFILE,
        "binary_video": True,
        "violation_burst": True,
        "deep_cnn_verifier": ver_name,
        "deep_verifier_is_real_cnn": is_real_cnn,
        "deep_verifier_mode": "CNN_ONNX" if is_real_cnn else "HEURISTIC_BASELINE",
        "pbl4_bridge_enabled": pbl4_bridge.enabled,
        "live_sse": True,
        "rbac_enforcement": True,
        "vehicle_count": len(vehicles),
        "violation_count": violation_count,
        "ts": time.time(),
    }


@app.get("/api/v1/auth/me")
def auth_me(request: Request, role: str = Depends(auth.require_role("viewer"))):
    tok = getattr(request.state, "token", "") or ""
    return {"ok": True, "role": role, "token_suffix": tok[-6:]}


@app.post("/api/v1/auth/login")
async def auth_login(request: Request):
    """Validates token and issues HttpOnly SameSite=Lax cookie so /evidence/* media loads safely."""
    body = await request.json()
    token = str(body.get("token") or "").strip()
    tokens = auth._load_tokens()
    role = auth._lookup_role_constant_time(token, tokens) if token else None
    if not role:
        auth.audit(request, 401, detail="login_failed")
        raise HTTPException(status_code=401, detail="Token khong hop le")
    request.state.role = role
    request.state.token = token
    auth.audit(request, 200, detail="login_ok")
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
    )
    resp = JSONResponse({"ok": True, "role": role, "token_suffix": token[-6:]})
    resp.set_cookie("web_token", token, httponly=True, secure=is_https, samesite="lax", max_age=86400)
    return resp


# ---------------------------------------------------------------------------
# Telemetry (Pi HTTP primary + ESP32 survival bridge)
# ---------------------------------------------------------------------------
def _handle_telemetry(payload: dict, via_esp_bridge: bool = False) -> dict:
    vehicle_id = _vehicle_id_of(payload)
    prev = database.get_vehicle(vehicle_id) or {}
    gps = payload.get("gps") or {}
    access_info = payload.get("access_info") or {}
    camera = payload.get("camera") or {}
    pi = payload.get("pi") or {}

    driver_id = payload.get("driver_id") or access_info.get("driver_id")
    driver_name = payload.get("driver_name") or access_info.get("driver_name")
    stream_url = access_info.get("stream_url") or camera.get("stream_url")

    gps_lat = gps.get("lat")
    gps_lon = gps.get("lon")
    gps_valid = bool(gps.get("is_valid")) and gps_lat is not None and gps_lon is not None
    speed_kmh = float(gps.get("speed_kmh") or 0.0)
    co2_ppm = analytics.extract_co2_ppm(payload)
    pi_temp_c = analytics.extract_pi_temp_c(payload)
    thermal_level = int(pi.get("thermal_level") or (payload.get("hardware") or {}).get("thermal_level") or 0)

    update_fields = dict(
        vehicle_name=payload.get("vehicle_name"),
        current_driver_id=driver_id,
        current_driver_name=driver_name,
        local_ip=access_info.get("local_ip"),
        web_port=access_info.get("web_port"),
        stream_url=stream_url,
        status="ONLINE",
        last_seen=time.time(),
        thermal_level=thermal_level,
        last_telemetry_json=database.dumps(payload),
    )
    if gps_valid:
        update_fields.update(gps_lat=float(gps_lat), gps_lon=float(gps_lon), gps_valid=1)

    database.upsert_vehicle(vehicle_id, **update_fields)

    with database.tx() as conn:
        conn.execute(
            "INSERT INTO telemetry_history (vehicle_id, ts, co2_ppm, speed_kmh, pi_temp_c, heart_rate_bpm, lat, lon, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                vehicle_id,
                time.time(),
                co2_ppm,
                speed_kmh,
                pi_temp_c,
                (payload.get("bluetooth_phone") or {}).get("heart_rate_bpm"),
                gps_lat if gps_valid else None,
                gps_lon if gps_valid else None,
                database.dumps(payload),
            ),
        )

    trip_id = analytics.get_or_start_trip(vehicle_id, driver_id, driver_name)
    analytics.update_trip_motion(
        vehicle_id=vehicle_id,
        trip_id=trip_id,
        prev_lat=prev.get("gps_lat"),
        prev_lon=prev.get("gps_lon"),
        prev_valid=bool(prev.get("gps_valid")),
        lat=gps_lat,
        lon=gps_lon,
        gps_valid=gps_valid,
        speed_kmh=speed_kmh,
    )

    bt = payload.get("bluetooth_phone")
    if bt:
        bluetooth_hub.ingest_bluetooth_block(vehicle_id, bt)

    cabin = payload.get("cabin_sensors")
    if cabin:
        with database.tx() as conn:
            conn.execute(
                "INSERT INTO cabin_sensor_events (vehicle_id, ts, sensor_type, payload_json) VALUES (?,?,?,?)",
                (vehicle_id, time.time(), cabin.get("sensor_type", "MIXED"), database.dumps(cabin)),
            )

    mute_state = payload.get("mute_state") or {}
    hw_events = payload.get("hw_events") or (payload.get("esp") or {}).get("hw_events") or []
    if mute_state.get("active") and mute_state.get("newly_triggered"):
        mute_tracker.record_dismissal(
            vehicle_id=vehicle_id,
            dismiss_source="PHYSICAL_MUTE_BUTTON",
            dismissed_alert_type=mute_state.get("alert_type", "DROWSY"),
            driver_id=driver_id,
            gps=gps,
            mute_duration_s=mute_state.get("mute_duration_s", config.MUTE_DURATION_DEFAULT_S),
        )
    elif isinstance(hw_events, (list, tuple)) and "MUTE_ACTIVE" in hw_events:
        mute_tracker.record_dismissal(
            vehicle_id=vehicle_id,
            dismiss_source="PHYSICAL_MUTE_BUTTON",
            dismissed_alert_type="DROWSY",
            driver_id=driver_id,
            gps=gps,
        )

    analytics.run_watchdog_checks(vehicle_id, payload)
    pbl4_bridge.forward_telemetry(vehicle_id, payload)
    broker.publish("telemetry", {"vehicle_id": vehicle_id, "via_esp_bridge": via_esp_bridge, "payload": payload})

    # Always include cmd_uid so Pi can ACK every delivered command (fixes S1!)
    pending = cmds.pending_commands_for(vehicle_id, mark_sent=True)
    return {
        "ok": True,
        "ack": "TELEMETRY_RECEIVED",
        "trip_id": trip_id,
        "downlink_xyz": _xyz_downlink(vehicle_id),
        "commands": [{"cmd_uid": p["cmd_uid"], "cmd": p["command"]} for p in pending],
        "remote_config": cmds.get_remote_config(vehicle_id),
    }


@app.post("/api/v1/telemetry")
@app.post("/api/v1/pi/telemetry")
async def telemetry(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await request.json()
    result = _handle_telemetry(payload, via_esp_bridge=False)
    auth.audit(request, 200, _vehicle_id_of(payload))
    return result


@app.post("/api/v1/esp/bridge_telemetry")
@app.post("/api/v1/esp/telemetry")
async def esp_bridge_telemetry(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await request.json()
    result = _handle_telemetry(payload, via_esp_bridge=True)
    auth.audit(request, 200, _vehicle_id_of(payload))
    return result


# ---------------------------------------------------------------------------
# Violation Ingest: Path A (Preferred) — Multipart JPEG Burst -> Tier-2 CNN
# ---------------------------------------------------------------------------
def _insert_violation_row(
    payload: dict,
    image_url: Optional[str],
    burst_dir: Optional[str],
    burst_frame_count: int,
    idempotency_key: Optional[str],
) -> Tuple[str, Optional[str], bool]:
    vehicle_id = _vehicle_id_of(payload)
    event_id = str(payload.get("event_id") or f"EVT_{_now_ms()}_{uuid.uuid4().hex[:6]}")
    idem = str(idempotency_key or payload.get("idempotency_key") or event_id)
    gps = payload.get("gps") or {}
    co2_ppm = payload.get("co2_ppm")
    if co2_ppm is None:
        co2_ppm = analytics.extract_co2_ppm(payload)

    conn = database.get_conn()
    trip_row = conn.execute(
        "SELECT trip_id FROM trips WHERE vehicle_id=? AND status='ACTIVE' ORDER BY started_at DESC LIMIT 1",
        (vehicle_id,),
    ).fetchone()
    if trip_row:
        trip_id = trip_row["trip_id"]
    else:
        database.upsert_vehicle(
            vehicle_id,
            current_driver_id=payload.get("driver_id"),
            current_driver_name=payload.get("driver_name"),
        )
        trip_id = analytics.get_or_start_trip(vehicle_id, payload.get("driver_id"), payload.get("driver_name"))

    with database.tx() as c:
        cur = c.execute(
            """INSERT OR IGNORE INTO violations
               (event_id, idempotency_key, vehicle_id, trip_id, driver_id, driver_name,
                edge_alert_state, verification_mode, alert_type, behavior_detail,
                started_at, confirmed_at, duration_seconds, segment_index,
                gps_lat, gps_lon, speed_kmh, co2_ppm, image_url, video_url,
                burst_dir, burst_frame_count, cnn_status, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,'PENDING',?)""",
            (
                event_id,
                idem,
                vehicle_id,
                trip_id,
                payload.get("driver_id"),
                payload.get("driver_name"),
                payload.get("alert_state", "EDGE_TRIGGERED"),
                payload.get("verification_mode", "SINGLE_MODEL_YOLO"),
                payload.get("alert_type"),
                payload.get("behavior_detail"),
                str(payload.get("started_at") or ""),
                str(payload.get("confirmed_at") or ""),
                payload.get("duration_seconds"),
                payload.get("segment_index"),
                gps.get("lat"),
                gps.get("lon"),
                gps.get("speed_kmh"),
                co2_ppm,
                image_url,
                burst_dir,
                burst_frame_count,
                database.dumps(payload),
            ),
        )
        inserted = cur.rowcount > 0
    return event_id, trip_id, inserted


def _parse_multipart_stdlib(content_type: str, raw_body: bytes) -> Tuple[Optional[str], List[Tuple[str, bytes]]]:
    """Zero-dependency stdlib multipart/form-data parser (no python-multipart required)."""
    header_block = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1", errors="ignore")
    msg = BytesParser(policy=email_policy_default).parsebytes(header_block + raw_body)
    meta_raw: Optional[str] = None
    frames_in: List[Tuple[str, bytes]] = []
    if not msg.is_multipart():
        return None, []
    for part in msg.iter_parts():
        disp_name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload_bytes = part.get_payload(decode=True) or b""
        if disp_name == "meta":
            meta_raw = payload_bytes.decode("utf-8", errors="replace")
        elif disp_name == "frames" or filename:
            frames_in.append((filename or "frame.jpg", payload_bytes))
    return meta_raw, frames_in


@app.post("/api/v1/violation_burst")
async def upload_violation_burst(request: Request, role: str = Depends(auth.require_role("device"))):
    """Preferred PI_AI ingest: multipart/form-data with 'meta' JSON + 'frames' (10-20 JPEGs)."""
    ct = request.headers.get("content-type", "")
    raw_body = await request.body()
    if len(raw_body) > config.MAX_UPLOAD_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Tong dung luong burst vuot gioi han")
    meta_raw, frames_in = _parse_multipart_stdlib(ct, raw_body)
    if not meta_raw:
        raise HTTPException(status_code=400, detail="Thieu truong 'meta' JSON trong multipart form")
    try:
        payload = json.loads(meta_raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Truong 'meta' khong phai JSON hop le")

    vehicle_id = _vehicle_id_of(payload)
    event_id = str(payload.get("event_id") or f"EVT_{_now_ms()}_{uuid.uuid4().hex[:6]}")
    payload["event_id"] = event_id
    idem = request.headers.get("idempotency-key") or payload.get("idempotency_key") or event_id

    # Check idempotency BEFORE writing duplicate burst files
    existing = database.get_conn().execute(
        "SELECT event_id, image_url, video_url, cnn_status FROM violations WHERE event_id=? OR idempotency_key=?",
        (event_id, idem),
    ).fetchone()
    if existing:
        auth.audit(request, 200, vehicle_id, detail="idempotent_burst_hit")
        return {
            "ok": True,
            "duplicate": True,
            "event_id": existing["event_id"],
            "image_url": existing["image_url"],
            "video_url": existing["video_url"],
            "cnn_status": existing["cnn_status"],
        }

    burst_dir, saved_rels = media.save_burst_frames(vehicle_id, event_id, frames_in)
    image_url = ("/evidence/" + saved_rels[len(saved_rels) // 2]) if saved_rels else None

    event_id, trip_id, inserted = _insert_violation_row(
        payload=payload,
        image_url=image_url,
        burst_dir=burst_dir,
        burst_frame_count=len(saved_rels),
        idempotency_key=idem,
    )
    if inserted:
        broker.publish(
            "violation",
            {
                "event_id": event_id,
                "vehicle_id": vehicle_id,
                "alert_type": payload.get("alert_type"),
                "alert_state": payload.get("alert_state", "EDGE_TRIGGERED"),
                "cnn_status": "PENDING",
                "image_url": image_url,
                "frame_count": len(saved_rels),
            },
        )
        verifier_engine.enqueue(event_id, vehicle_id, trip_id, burst_dir, payload)

    auth.audit(request, 200, vehicle_id, detail=f"burst_frames={len(saved_rels)}")
    return {
        "ok": True,
        "event_id": event_id,
        "burst_dir": burst_dir,
        "frame_count": len(saved_rels),
        "image_url": image_url,
        "cnn_status": "PENDING",
        "downlink_xyz": _xyz_downlink(vehicle_id),
    }


@app.post("/api/v1/violation")
@app.post("/api/v1/violations")
async def violation(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await request.json()
    vehicle_id = _vehicle_id_of(payload)
    event_id = str(payload.get("event_id") or f"EVT_{_now_ms()}_{uuid.uuid4().hex[:6]}")
    payload["event_id"] = event_id
    idem = request.headers.get("idempotency-key") or payload.get("idempotency_key") or event_id

    image_url = None
    evidence_b64 = payload.get("evidence_image")
    if evidence_b64:
        image_url = "/evidence/" + media.save_base64_image(evidence_b64, vehicle_id, event_id)

    event_id, trip_id, inserted = _insert_violation_row(
        payload=payload,
        image_url=image_url,
        burst_dir=None,
        burst_frame_count=1 if image_url else 0,
        idempotency_key=idem,
    )
    if inserted:
        broker.publish(
            "violation",
            {
                "event_id": event_id,
                "vehicle_id": vehicle_id,
                "alert_type": payload.get("alert_type"),
                "cnn_status": "PENDING",
                "image_url": image_url,
            },
        )
        verifier_engine.enqueue(event_id, vehicle_id, trip_id, None, payload)

    auth.audit(request, 200, vehicle_id)
    return {
        "ok": True,
        "event_id": event_id,
        "image_url": image_url,
        "cnn_status": "PENDING",
        "downlink_xyz": _xyz_downlink(vehicle_id),
    }


@app.post("/api/v1/video")
async def upload_video(request: Request, role: str = Depends(auth.require_role("device"))):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("video/mp4"):
        vehicle_id = request.headers.get("x-vehicle-id", "UNKNOWN")
        event_id = request.headers.get("x-event-id") or f"EVT_{_now_ms()}"
        filename_hint = request.headers.get("x-video-filename")
        raw = await request.body()
        fname = media.save_binary_video(raw, vehicle_id, event_id, filename_hint)
        video_url = "/evidence/" + fname
        with database.tx() as conn:
            cur = conn.execute("UPDATE violations SET video_url=? WHERE event_id=?", (video_url, event_id))
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO violations (event_id, idempotency_key, vehicle_id, video_url, cnn_status) VALUES (?,?,?,?,'PENDING')",
                    (event_id, event_id, vehicle_id, video_url),
                )
        broker.publish("video_uploaded", {"vehicle_id": vehicle_id, "event_id": event_id, "video_url": video_url})
        auth.audit(request, 200, vehicle_id)
        return {"ok": True, "event_id": event_id, "video_url": video_url}

    payload = await request.json()
    vehicle_id = _vehicle_id_of(payload)
    event_id = str(payload.get("event_id") or f"EVT_{_now_ms()}")
    video_url = None
    if payload.get("video_mp4_base64"):
        fname = media.save_base64_video(payload["video_mp4_base64"], vehicle_id, event_id)
        video_url = "/evidence/" + fname
        with database.tx() as conn:
            cur = conn.execute("UPDATE violations SET video_url=? WHERE event_id=?", (video_url, event_id))
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT OR IGNORE INTO violations (event_id, idempotency_key, vehicle_id, video_url, cnn_status) VALUES (?,?,?,?,'PENDING')",
                    (event_id, event_id, vehicle_id, video_url),
                )
    image_url = None
    if payload.get("evidence_image"):
        image_url = "/evidence/" + media.save_base64_image(payload["evidence_image"], vehicle_id, event_id)
        with database.tx() as conn:
            conn.execute("UPDATE violations SET image_url=? WHERE event_id=?", (image_url, event_id))
    auth.audit(request, 200, vehicle_id)
    return {"ok": True, "event_id": event_id, "video_url": video_url, "image_url": image_url}


@app.get("/evidence/{rel_path:path}")
def get_evidence(rel_path: str, request: Request, role: str = Depends(auth.require_role("viewer"))):
    return media.serve_evidence_file(rel_path, request.headers.get("range"))


# ---------------------------------------------------------------------------
# Commands & Remote Config
# ---------------------------------------------------------------------------
@app.post("/api/v1/server/command")
@app.post("/api/v1/command")
async def post_command(request: Request, role: str = Depends(auth.require_role("operator"))):
    payload = await request.json()
    vehicle_id = payload.get("vehicle_id") or payload.get("vid")
    cmd = payload.get("cmd") or payload.get("command")
    if not vehicle_id or not cmd:
        raise HTTPException(status_code=400, detail="Can vehicle_id va cmd/command")
    result = cmds.enqueue_command(vehicle_id, cmd, source="dashboard", actor_role=role)
    auth.audit(request, 200, vehicle_id, detail=f"cmd={cmd}")
    return result


@app.get("/api/v1/pi/pull_commands")
@app.get("/api/v1/commands/pull")
def pull_commands(vehicle_id: str, request: Request, role: str = Depends(auth.require_role("device"))):
    rows = cmds.pending_commands_for(vehicle_id, mark_sent=True)
    auth.audit(request, 200, vehicle_id)
    return {"ok": True, "vehicle_id": vehicle_id, "commands": [{"cmd": r["command"], "cmd_uid": r["cmd_uid"]} for r in rows]}


@app.post("/api/v1/commands/ack")
async def commands_ack(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await request.json()
    result = cmds.ack_command(payload.get("cmd_uid"), payload.get("result", "OK"), payload.get("detail"))
    auth.audit(request, 200, payload.get("vehicle_id"), detail=f"ack={payload.get('cmd_uid')}")
    return result


@app.get("/api/v1/commands")
def get_commands(vehicle_id: Optional[str] = None, role: str = Depends(auth.require_role("viewer"))):
    return {"commands": cmds.list_commands(vehicle_id)}


@app.post("/api/v1/config/{vehicle_id}")
async def set_config(vehicle_id: str, request: Request, role: str = Depends(auth.require_role("admin"))):
    patch = await request.json()
    res = cmds.set_remote_config(vehicle_id, patch)
    auth.audit(request, 200, vehicle_id, detail="remote_config_update")
    return res


@app.get("/api/v1/config/{vehicle_id}")
def get_config(vehicle_id: str, role: str = Depends(auth.require_role("viewer"))):
    return cmds.get_remote_config(vehicle_id)


# ---------------------------------------------------------------------------
# Geo XYZ
# ---------------------------------------------------------------------------
@app.get("/api/v1/geo/xyz")
def get_geo_xyz(vehicle_id: str, role: str = Depends(auth.require_role("viewer"))):
    return _xyz_downlink(vehicle_id)


@app.post("/api/v1/geo/inject_xyz")
async def inject_geo_xyz(request: Request, role: str = Depends(auth.require_role("operator"))):
    payload = await request.json()
    vehicle_id = payload.get("vehicle_id")
    if not vehicle_id:
        raise HTTPException(status_code=400, detail="Can vehicle_id")
    database.upsert_vehicle(
        vehicle_id,
        xyz_lat=payload.get("lat"),
        xyz_lon=payload.get("lon"),
        xyz_is_sample_anchor=0 if payload.get("confirmed") else 1,
    )
    auth.audit(request, 200, vehicle_id, detail="inject_xyz")
    return {"ok": True, "downlink_xyz": _xyz_downlink(vehicle_id)}


# ---------------------------------------------------------------------------
# Alert dismissal / Mute tracking
# ---------------------------------------------------------------------------
@app.post("/api/v1/events/alert_dismissed")
async def alert_dismissed(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await request.json()
    vehicle_id = payload.get("vehicle_id")
    gps = payload.get("gps") or {}
    record = mute_tracker.record_dismissal(
        vehicle_id=vehicle_id,
        dismiss_source=payload.get("dismiss_source", "PHYSICAL_MUTE_BUTTON"),
        dismissed_alert_type=payload.get("dismissed_alert_type", "DROWSY"),
        driver_id=payload.get("driver_id"),
        related_violation_id=payload.get("related_violation_id"),
        alert_started_at_ms=payload.get("alert_started_at_ms"),
        dismissed_at_ms=payload.get("dismissed_at_ms"),
        mute_duration_s=payload.get("mute_duration_s", config.MUTE_DURATION_DEFAULT_S),
        gps=gps,
        event_id=payload.get("event_id"),
    )
    if payload.get("driver_still_drowsy_after_5s"):
        mute_tracker.mark_still_risky(record["event_id"])
    auth.audit(request, 200, vehicle_id, detail="alert_dismissed")
    return {"ok": True, **record}


@app.post("/api/v1/server/command/dismiss")
async def remote_operator_dismiss(request: Request, role: str = Depends(auth.require_role("operator"))):
    payload = await request.json()
    vid = payload.get("vehicle_id")
    record = mute_tracker.record_dismissal(
        vehicle_id=vid,
        dismiss_source="REMOTE_OPERATOR",
        dismissed_alert_type=payload.get("alert_type", "DROWSY"),
    )
    auth.audit(request, 200, vid, detail="remote_operator_dismiss")
    return {"ok": True, **record}


# ---------------------------------------------------------------------------
# Bluetooth / Phone (OTP-protected pairing!)
# ---------------------------------------------------------------------------
@app.post("/api/v1/bluetooth/event")
async def bluetooth_event(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await request.json()
    vehicle_id = payload.get("vehicle_id")
    evt = payload.get("evt") or payload.get("event")
    if evt == "PHONE_DISCONNECTED":
        bluetooth_hub.disconnect_session(vehicle_id, payload.get("device_id", ""))
    else:
        bluetooth_hub.ingest_bluetooth_block(vehicle_id, payload.get("bluetooth_phone", payload))
    auth.audit(request, 200, vehicle_id, detail=f"bt={evt}")
    return {"ok": True, "evt": evt}


@app.post("/api/v1/phone/pair_otp")
async def create_phone_pair_otp(request: Request, role: str = Depends(auth.require_role("operator"))):
    """Only operator/admin can mint a short-lived single-use OTP for phone pairing."""
    payload = await request.json()
    vid = payload.get("vehicle_id")
    if not vid:
        raise HTTPException(status_code=400, detail="Can vehicle_id")
    res = bluetooth_hub.issue_pair_otp(vid, payload.get("driver_id"), issued_by=role)
    auth.audit(request, 200, vid, detail="pair_otp_issued")
    return res


@app.post("/api/v1/phone/pair")
async def phone_pair(request: Request, role: str = Depends(auth.require_role("viewer"))):
    """Requires valid single-use OTP issued by operator/admin (blocks privilege escalation!)."""
    payload = await request.json()
    vid = payload.get("vehicle_id")
    result = bluetooth_hub.pair_phone_with_otp(vid, payload.get("pair_code", ""), payload.get("driver_id"))
    auth.issue_token(result["driver_phone_token"], "driver_phone")
    auth.audit(request, 200, vid, detail="phone_paired")
    return result


@app.post("/api/v1/phone/telemetry")
async def phone_telemetry(request: Request, role: str = Depends(auth.require_role("driver_phone"))):
    payload = await request.json()
    result = bluetooth_hub.phone_telemetry(payload.get("vehicle_id"), payload)
    auth.audit(request, 200, payload.get("vehicle_id"))
    return result


@app.post("/api/v1/phone/action")
async def phone_action(request: Request, role: str = Depends(auth.require_role("driver_phone"))):
    payload = await request.json()
    result = bluetooth_hub.phone_action(payload.get("vehicle_id"), payload.get("action"), payload.get("detail"))
    auth.audit(request, 200, payload.get("vehicle_id"), detail=f"phone_action={payload.get('action')}")
    return result


@app.get("/api/v1/phone/notifications")
def phone_notifications(vehicle_id: str, role: str = Depends(auth.require_role("driver_phone"))):
    return {"notifications": bluetooth_hub.pull_notifications(vehicle_id)}


@app.get("/api/v1/vehicles/{vehicle_id}/bluetooth")
def vehicle_bluetooth(vehicle_id: str, role: str = Depends(auth.require_role("viewer"))):
    return bluetooth_hub.get_bluetooth_detail(vehicle_id)


# ---------------------------------------------------------------------------
# Incidents, Vehicles, Trips, Violations, Dismissals, Audit, Reports, Admin
# ---------------------------------------------------------------------------
@app.get("/api/v1/incidents")
def get_incidents(status: Optional[str] = None, vehicle_id: Optional[str] = None, role: str = Depends(auth.require_role("viewer"))):
    return {"incidents": analytics.list_incidents(status, vehicle_id)}


@app.post("/api/v1/incidents/{incident_id}/ack")
async def ack_incident(incident_id: str, request: Request, role: str = Depends(auth.require_role("operator"))):
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    res = analytics.ack_incident(incident_id, payload.get("ack_by", role), payload.get("note", ""))
    auth.audit(request, 200, detail=f"ack_incident={incident_id}")
    return res


@app.post("/api/v1/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str, request: Request, role: str = Depends(auth.require_role("operator"))):
    raw = await request.body()
    payload = json.loads(raw) if raw else {}
    res = analytics.resolve_incident(incident_id, payload.get("note", ""))
    auth.audit(request, 200, detail=f"resolve_incident={incident_id}")
    return res


@app.get("/api/v1/vehicles")
def get_vehicles(role: str = Depends(auth.require_role("viewer"))):
    return {"vehicles": database.list_vehicles()}


@app.get("/api/v1/vehicles/{vehicle_id}")
def get_vehicle_detail(vehicle_id: str, role: str = Depends(auth.require_role("viewer"))):
    v = database.get_vehicle(vehicle_id)
    if not v:
        raise HTTPException(status_code=404, detail="Khong tim thay xe")
    return v


@app.get("/api/v1/trips")
def get_trips(vehicle_id: Optional[str] = None, role: str = Depends(auth.require_role("viewer"))):
    return {"trips": analytics.list_trips(vehicle_id)}


@app.get("/api/v1/violations")
def get_violations(vehicle_id: Optional[str] = None, role: str = Depends(auth.require_role("viewer"))):
    conn = database.get_conn()
    if vehicle_id:
        rows = conn.execute("SELECT * FROM violations WHERE vehicle_id=? ORDER BY created_at DESC LIMIT 200", (vehicle_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM violations ORDER BY created_at DESC LIMIT 200").fetchall()
    return {"violations": database.rows_to_list(rows)}


@app.get("/api/v1/violations/{event_id}/frames")
def get_violation_frames(event_id: str, role: str = Depends(auth.require_role("viewer"))):
    conn = database.get_conn()
    row = conn.execute("SELECT * FROM violations WHERE event_id=?", (event_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Khong tim thay vi pham")
    v = dict(row)
    frames = media.list_burst_frame_urls(v.get("burst_dir"))
    if not frames and v.get("image_url"):
        frames = [v["image_url"]]
    return {
        "event_id": event_id,
        "vehicle_id": v.get("vehicle_id"),
        "alert_type": v.get("alert_type"),
        "video_url": v.get("video_url"),
        "frames": frames,
        "cnn_status": v.get("cnn_status"),
        "human_review_status": v.get("human_review_status") or "PENDING_REVIEW",
        "human_review_note": v.get("human_review_note") or "",
    }


@app.post("/api/v1/violations/{event_id}/review")
async def review_violation(event_id: str, request: Request, role: str = Depends(auth.require_role("operator"))):
    """Human-in-the-Loop review: confirm violation or mark false positive (refunds Safety Score + archives hard negative)."""
    payload = await request.json()
    decision = str(payload.get("decision") or "").strip().upper()
    note = str(payload.get("note") or "").strip()[:300]
    if decision not in ("HUMAN_CONFIRMED", "HUMAN_FALSE_POSITIVE"):
        raise HTTPException(status_code=400, detail="decision phai la HUMAN_CONFIRMED hoac HUMAN_FALSE_POSITIVE")

    conn = database.get_conn()
    row = conn.execute("SELECT * FROM violations WHERE event_id=?", (event_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Khong tim thay vi pham")
    v = dict(row)
    already_penalized = bool(v.get("penalty_applied"))
    trip_id = v.get("trip_id")
    alert_type = v.get("alert_type") or ""
    new_score = None
    new_penalty_flag = 1 if already_penalized else 0

    if decision == "HUMAN_FALSE_POSITIVE":
        if already_penalized:
            new_score = analytics.refund_violation_penalty(trip_id, alert_type)
            new_penalty_flag = 0
        # Archive burst into hard_negatives/ with operator feedback so future AI retraining learns from human review
        bdir = v.get("burst_dir")
        if bdir:
            try:
                import shutil as _shutil
                src = config.EVIDENCE_DIR / bdir
                dst = config.HARD_NEGATIVES_DIR / f"human_fp_{bdir}"
                if src.exists() and not dst.exists():
                    _shutil.copytree(src, dst)
                if dst.exists():
                    (dst / "human_review.json").write_text(
                        json.dumps(
                            {"event_id": event_id, "decision": decision, "reviewed_by": role, "note": note, "ts": time.time()},
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
            except Exception:
                pass
    elif decision == "HUMAN_CONFIRMED":
        if not already_penalized:
            new_score = analytics.apply_violation_penalty(trip_id, alert_type)
            new_penalty_flag = 1

    now_s = time.time()
    with database.tx() as c:
        c.execute(
            "UPDATE violations SET human_review_status=?, human_reviewed_by=?, human_review_note=?, "
            "human_reviewed_at=?, penalty_applied=? WHERE event_id=?",
            (decision, role, note, now_s, new_penalty_flag, event_id),
        )

    broker.publish(
        "violation_reviewed",
        {
            "event_id": event_id,
            "vehicle_id": v.get("vehicle_id"),
            "decision": decision,
            "reviewed_by": role,
            "note": note,
            "safety_score": new_score,
        },
    )
    auth.audit(request, 200, v.get("vehicle_id"), detail=f"human_review={decision}:{event_id}")
    return {
        "ok": True,
        "event_id": event_id,
        "human_review_status": decision,
        "reviewed_by": role,
        "note": note,
        "penalty_applied": bool(new_penalty_flag),
        "safety_score": new_score,
    }


@app.get("/api/v1/drivers/summary")
def get_drivers_summary(role: str = Depends(auth.require_role("viewer"))):
    return {"drivers": analytics.get_drivers_human_summary()}


@app.get("/api/v1/admin/security_health")
def get_security_health(role: str = Depends(auth.require_role("admin"))):
    return {"ok": True, "token_health": auth.inspect_token_store_health()}


@app.get("/api/v1/dismissals")
def get_dismissals(vehicle_id: Optional[str] = None, role: str = Depends(auth.require_role("viewer"))):
    conn = database.get_conn()
    if vehicle_id:
        rows = conn.execute("SELECT * FROM alert_dismissals WHERE vehicle_id=? ORDER BY created_at DESC LIMIT 200", (vehicle_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM alert_dismissals ORDER BY created_at DESC LIMIT 200").fetchall()
    return {"dismissals": database.rows_to_list(rows)}


@app.get("/api/v1/audit")
def get_audit_logs(limit: int = 100, role: str = Depends(auth.require_role("admin"))):
    limit = max(1, min(500, int(limit)))
    rows = database.get_conn().execute("SELECT * FROM audit_logs ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return {"audit_logs": database.rows_to_list(rows)}


@app.get("/api/v1/reports/export")
def export_report(
    kind: str,
    fmt: str = "csv",
    vehicle_id: Optional[str] = None,
    driver_id: Optional[str] = None,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
    role: str = Depends(auth.require_role("operator")),
):
    exporters = {
        "violations": reports.export_violations,
        "dismissals": reports.export_dismissals,
        "trips": reports.export_trips,
    }
    fn = exporters.get(kind)
    if not fn:
        raise HTTPException(status_code=400, detail="kind phai la violations|dismissals|trips")
    data, name = fn(fmt, vehicle_id, driver_id, start_ts, end_ts)
    media_type = "application/json" if fmt == "json" else "text/csv"
    return Response(content=data, media_type=media_type, headers={"Content-Disposition": f"attachment; filename={name}.{fmt}"})


@app.post("/api/v1/reports/cleanup")
def run_cleanup(request: Request, role: str = Depends(auth.require_role("admin"))):
    res = reports.cleanup_old_data()
    auth.audit(request, 200, detail="reports_cleanup")
    return res


@app.get("/api/v1/admin/tokens")
def admin_list_tokens(role: str = Depends(auth.require_role("admin"))):
    return {"tokens": auth.list_token_summaries()}


@app.post("/api/v1/admin/tokens")
async def admin_issue_token(request: Request, role: str = Depends(auth.require_role("admin"))):
    payload = await request.json()
    token = payload.get("token") or uuid.uuid4().hex
    target_role = payload.get("role", "viewer")
    try:
        auth.issue_token(token, target_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auth.audit(request, 200, detail=f"issue_token={target_role}")
    return {"ok": True, "token": token, "role": target_role}


@app.delete("/api/v1/admin/tokens/{token}")
def admin_revoke_token(token: str, request: Request, role: str = Depends(auth.require_role("admin"))):
    existed = auth.revoke_token(token)
    auth.audit(request, 200, detail=f"revoke_token_existed={existed}")
    return {"ok": True, "revoked": existed}


@app.get("/api/v1/stream/events")
async def stream_events(request: Request, role: str = Depends(auth.require_role("viewer"))):
    return StreamingResponse(
        broker.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/")
@app.get("/login")
def dashboard_index():
    return FileResponse(str(config.STATIC_DIR / "index.html"))
