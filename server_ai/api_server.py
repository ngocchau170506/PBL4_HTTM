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
import ipaddress
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from server_ai.server_core import (
    analytics,
    auth,
    bluetooth_hub,
    commands as cmds,
    config,
    database,
    geo_engine,
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
        try:
            await asyncio.to_thread(analytics.check_offline_watchdog)
        except Exception as exc:
            print(f"[WATCHDOG offline err] {exc}")
        try:
            await asyncio.to_thread(analytics.check_idle_trips)
        except Exception as exc:
            print(f"[WATCHDOG trips err] {exc}")
        if (time.monotonic() - last_cleanup) >= config.CLEANUP_INTERVAL_S:
            try:
                await asyncio.to_thread(reports.cleanup_old_data)
            except Exception as exc:
                print(f"[WATCHDOG cleanup err] {exc}")
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
        broker.shutdown()
        pbl4_bridge.stop()
        verifier_engine.stop()
        database.close_all_connections()


app = FastAPI(title="PI_AI Server of AI", version="2.0.0", lifespan=lifespan)


async def _read_bounded_body(request: Request, limit: int) -> bytes:
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=f"Payload vuot gioi han {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_json_dict(request: Request, limit: int = config.MAX_JSON_BODY_BYTES, allow_empty: bool = False) -> dict:
    raw = await _read_bounded_body(request, limit)
    if allow_empty and not raw.strip():
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON khong hop le")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Payload JSON phai la object")
    return data


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
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|[a-zA-Z0-9-]+\.local|[a-zA-Z0-9-]+\.trycloudflare\.com)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "X-Auth-Token", "Content-Type"],
)


def _sanitize_stream_url(raw_url: Optional[str]) -> Optional[str]:
    """Allow ONLY private LAN / loopback HTTP(S) /stream URLs (blocks token exfil to public hosts!)."""
    if not raw_url:
        return None
    s = str(raw_url).strip()
    if not s:
        return None
    try:
        parsed = urlparse(s)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    host = (parsed.hostname or "").strip()
    if not host:
        return None
    if host.lower() != "localhost":
        try:
            ip = ipaddress.ip_address(host)
            if not (ip.is_private or ip.is_loopback or ip.is_link_local):
                return None
        except ValueError:
            return None
    path = parsed.path or "/stream"
    if not path.startswith("/stream"):
        return None
    port_part = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port_part}{path}"


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
    v = database.get_vehicle(vehicle_id)
    if not v:
        return {"lat": None, "lon": None, "ecef": None, "is_sample_anchor": False, "fix_status": "NO_FIX"}
    pos = geo_engine.resolve_vehicle_position(v)
    return {
        "lat": pos["lat"],
        "lon": pos["lon"],
        "ecef": pos["ecef"],
        "fix_status": pos["fix_status"],
        "is_sample_anchor": False,
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
    is_b1 = str(ver_name).startswith("b1-pretrained:")
    ver_mode = "CNN_ONNX" if is_real_cnn else ("PRETRAINED_B1" if is_b1 else "HEURISTIC_BASELINE")
    return {
        "ok": True,
        "service": config.SERVICE_NAME,
        "env_profile": config.ENV_PROFILE,
        "binary_video": True,
        "violation_burst": True,
        "deep_cnn_verifier": ver_name,
        "deep_verifier_is_real_cnn": is_real_cnn,
        "deep_verifier_mode": ver_mode,
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
    """Validates token (rate-limited) and issues HttpOnly SameSite=Lax cookie so /evidence/* media loads safely."""
    ip = auth.client_ip_of(request)
    if not auth.check_rate_limit(f"login:{ip}", limit=20, window_s=60.0):
        auth.audit(request, 429, detail="login_rate_limited")
        raise HTTPException(status_code=429, detail="Qua nhieu lan thu dang nhap, vui long cho 1 phut")
    body = await _read_json_dict(request)
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


@app.post("/api/v1/auth/logout")
def auth_logout(request: Request):
    """Clear HttpOnly web_token session cookie cleanly."""
    auth.audit(request, 200, detail="logout")
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("web_token", path="/")
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
    raw_stream_url = access_info.get("stream_url") or camera.get("stream_url")
    stream_url = _sanitize_stream_url(raw_stream_url)
    explicit_no_stream = (
        ("stream_url" in access_info and access_info.get("stream_url") is None)
        or (raw_stream_url is not None and stream_url is None)
    )

    now_s = time.time()
    gps_res = geo_engine.process_hardware_gps(prev, gps, now_s=now_s)
    gps_lat = gps_res["lat"]
    gps_lon = gps_res["lon"]
    gps_valid = bool(gps_res["is_valid"]) and gps_lat is not None and gps_lon is not None
    speed_kmh = float(gps_res["speed_kmh"] or 0.0)
    co2_ppm = analytics.extract_co2_ppm(payload)
    pi_temp_c = analytics.extract_pi_temp_c(payload)
    thermal_level = int(pi.get("thermal_level") or (payload.get("hardware") or {}).get("thermal_level") or 0)

    status_val = "SURVIVAL_ESP2" if via_esp_bridge else "ONLINE"
    update_fields = dict(
        vehicle_name=payload.get("vehicle_name"),
        current_driver_id=driver_id,
        current_driver_name=driver_name,
        local_ip=access_info.get("local_ip"),
        web_port=access_info.get("web_port"),
        stream_url=stream_url,
        status=status_val,
        last_seen=now_s,
        thermal_level=thermal_level,
        last_telemetry_json=database.dumps(payload),
    )
    if gps_valid:
        update_fields.update(
            gps_lat=float(gps_lat),
            gps_lon=float(gps_lon),
            gps_valid=1,
            gps_alt_m=gps_res["altitude_m"],
            gps_heading_deg=gps_res["heading_deg"],
            gps_speed_kmh=speed_kmh,
            gps_hdop=gps_res["hdop"],
            gps_accuracy_m=gps_res["accuracy_m"],
            gps_satellites=gps_res["satellites"],
            gps_source=gps_res["source"],
            gps_fix_at=now_s,
        )
    elif "gps" in payload:
        update_fields.update(
            gps_valid=0,
            gps_satellites=gps_res["satellites"],
            gps_speed_kmh=0.0,
        )

    clear_cols = ("stream_url",) if explicit_no_stream else None
    database.upsert_vehicle(vehicle_id, clear_fields=clear_cols, **update_fields)

    if via_esp_bridge:
        # LCK-05 v3: ESP32 #2 survival mode means Pi 4 stopped heartbeating >=3.5s -> raise incident & reflect BLE status!
        esp_blk = payload.get("esp") or {}
        if "ble_connected" in esp_blk:
            database.upsert_vehicle(vehicle_id, bt_phone_connected=1 if esp_blk.get("ble_connected") else 0)
        analytics.raise_incident(
            vehicle_id,
            "HIGH",
            "PI_OFFLINE_SURVIVAL_MODE",
            f"Pi 4 mat tin hieu — ESP32 #2 dang phat cuu sinh cho xe {vehicle_id}",
            {"esp": esp_blk, "gps_valid": gps_valid},
        )

    with database.tx() as conn:
        conn.execute(
            "INSERT INTO telemetry_history (vehicle_id, ts, co2_ppm, speed_kmh, pi_temp_c, heart_rate_bpm, lat, lon, alt_m, heading_deg, accuracy_m, gps_source, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                vehicle_id,
                now_s,
                co2_ppm,
                speed_kmh,
                pi_temp_c,
                (payload.get("bluetooth_phone") or {}).get("heart_rate_bpm"),
                gps_lat if gps_valid else None,
                gps_lon if gps_valid else None,
                gps_res["altitude_m"] if gps_valid else None,
                gps_res["heading_deg"] if gps_valid else None,
                gps_res["accuracy_m"] if gps_valid else None,
                gps_res["source"] if gps_valid else None,
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
            dedup_window_s=5.0,
        )
    elif isinstance(hw_events, (list, tuple)) and "MUTE_ACTIVE" in hw_events:
        mute_tracker.record_dismissal(
            vehicle_id=vehicle_id,
            dismiss_source="PHYSICAL_MUTE_BUTTON",
            dismissed_alert_type="DROWSY",
            driver_id=driver_id,
            gps=gps,
            dedup_window_s=5.0,
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
    raw = await _read_bounded_body(request, config.MAX_JSON_BODY_BYTES)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON khong hop le")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload JSON phai la object")
    result = await asyncio.to_thread(_handle_telemetry, payload, False)
    auth.audit(request, 200, _vehicle_id_of(payload))
    return result


@app.post("/api/v1/esp/bridge_telemetry")
@app.post("/api/v1/esp/telemetry")
async def esp_bridge_telemetry(request: Request, role: str = Depends(auth.require_role("device"))):
    raw = await _read_bounded_body(request, config.MAX_JSON_BODY_BYTES)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON khong hop le")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload JSON phai la object")
    result = await asyncio.to_thread(_handle_telemetry, payload, True)
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
    """Zero-dependency stdlib parser: handles multipart/form-data AND 0-frame urlencoded/JSON bursts (SOS/CO2/Brownout!)."""
    ct_low = (content_type or "").lower()
    if "application/x-www-form-urlencoded" in ct_low:
        from urllib.parse import parse_qs
        qs = parse_qs(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)
        meta_vals = qs.get("meta")
        return (meta_vals[0] if meta_vals else None), []
    if "application/json" in ct_low:
        return raw_body.decode("utf-8", errors="replace"), []
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


def _process_violation_burst_sync(ct: str, raw_body: bytes, header_idem: Optional[str]) -> Tuple[dict, str, str]:
    """Blocking multipart parse + JPEG disk save + SQLite insert executed off the AsyncIO loop."""
    meta_raw, frames_in = _parse_multipart_stdlib(ct, raw_body)
    if not meta_raw:
        raise HTTPException(status_code=400, detail="Thieu truong 'meta' JSON trong multipart form")
    try:
        payload = json.loads(meta_raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Truong 'meta' khong phai JSON hop le")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Truong 'meta' phai la JSON object")

    vehicle_id = _vehicle_id_of(payload)
    event_id = str(payload.get("event_id") or f"EVT_{_now_ms()}_{uuid.uuid4().hex[:6]}")
    payload["event_id"] = event_id
    idem = header_idem or payload.get("idempotency_key") or event_id

    existing = database.get_conn().execute(
        "SELECT event_id, image_url, video_url, cnn_status FROM violations WHERE event_id=? OR idempotency_key=?",
        (event_id, idem),
    ).fetchone()
    if existing:
        return (
            {
                "ok": True,
                "duplicate": True,
                "event_id": existing["event_id"],
                "image_url": existing["image_url"],
                "video_url": existing["video_url"],
                "cnn_status": existing["cnn_status"],
            },
            vehicle_id,
            "idempotent_burst_hit",
        )

    burst_dir: Optional[str] = None
    saved_rels: List[str] = []
    if frames_in:
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
        alert_up = str(payload.get("alert_type") or "").upper()
        if alert_up == "SOS":
            analytics.raise_incident(
                vehicle_id,
                "CRITICAL",
                "SOS_PRESSED",
                f"Tai xe xe {vehicle_id} bam nut SOS khan cap",
                {"event_id": event_id, "gps": payload.get("gps") or {}, "detail": payload.get("behavior_detail", "")},
                bypass_cooldown=True,
            )
        elif alert_up == "DROWSY_LEVEL3_ALARM":
            analytics.raise_incident(
                vehicle_id,
                "CRITICAL",
                "DROWSY_LEVEL3_ALARM",
                f"Tai xe xe {vehicle_id} buon ngu 3 lan lien tiep — da bat bao dong khoa tren ESP32 #1",
                {"event_id": event_id, "gps": payload.get("gps") or {}, "detail": payload.get("behavior_detail", "")},
                bypass_cooldown=True,
            )
            bluetooth_hub.push_notification(vehicle_id, payload.get("behavior_detail") or "Canh bao buon ngu lan 3/3!", level="CRITICAL")
        elif alert_up == "DROWSY_RELAPSE_CONTINUOUS":
            analytics.raise_incident(
                vehicle_id,
                "CRITICAL",
                "DROWSY_RELAPSE_CONTINUOUS",
                f"Tai xe xe {vehicle_id} TAI PHAM BUON NGU sau khi tat loa — dang phat bao dong lien tuc!",
                {"event_id": event_id, "gps": payload.get("gps") or {}, "detail": payload.get("behavior_detail", "")},
                bypass_cooldown=True,
            )
            bluetooth_hub.push_notification(vehicle_id, payload.get("behavior_detail") or "TAI PHAM BUON NGU - BAO DONG LIEN TUC!", level="CRITICAL")
        elif alert_up == "DRIVER_ABSENT":
            analytics.raise_incident(
                vehicle_id,
                "MEDIUM",
                "DRIVER_ABSENT",
                f"Camera xe {vehicle_id} khong thay tai xe — da tam tat canh bao vang mat 30 phut",
                {"event_id": event_id, "gps": payload.get("gps") or {}, "detail": payload.get("behavior_detail", "")},
                bypass_cooldown=False,
            )
            bluetooth_hub.push_notification(vehicle_id, payload.get("behavior_detail") or "Camera khong thay tai xe — tam tat vang mat 30 phut", level="WARN")
        elif alert_up == "MONITORING_AUTO_RESUMED_BY_SPEED":
            analytics.raise_incident(
                vehicle_id,
                "MEDIUM",
                "MONITORING_AUTO_RESUMED_BY_SPEED",
                f"Xe {vehicle_id} di chuyen >= 15 km/h — he thong tu dong bat lai giam sat AUTO",
                {"event_id": event_id, "gps": payload.get("gps") or {}, "detail": payload.get("behavior_detail", "")},
                bypass_cooldown=True,
            )
            bluetooth_hub.push_notification(vehicle_id, payload.get("behavior_detail") or "Da tu dong bat lai che do giam sat AUTO do xe di chuyen >= 15 km/h", level="INFO")
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

    return (
        {
            "ok": True,
            "event_id": event_id,
            "burst_dir": burst_dir,
            "frame_count": len(saved_rels),
            "image_url": image_url,
            "cnn_status": "PENDING",
            "downlink_xyz": _xyz_downlink(vehicle_id),
        },
        vehicle_id,
        f"burst_frames={len(saved_rels)}",
    )


@app.post("/api/v1/violation_burst")
async def upload_violation_burst(request: Request, role: str = Depends(auth.require_role("device"))):
    """Preferred PI_AI ingest: multipart/form-data with 'meta' JSON + 'frames' (10-20 JPEGs)."""
    ct = request.headers.get("content-type", "")
    raw_body = await _read_bounded_body(request, config.MAX_UPLOAD_BODY_BYTES)
    res, vehicle_id, detail = await asyncio.to_thread(
        _process_violation_burst_sync, ct, raw_body, request.headers.get("idempotency-key")
    )
    await asyncio.to_thread(auth.audit, request, 200, vehicle_id, detail)
    return res


@app.post("/api/v1/violation")
@app.post("/api/v1/violations")
async def violation(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await _read_json_dict(request, limit=config.MAX_UPLOAD_BODY_BYTES)
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
        raw = await _read_bounded_body(request, config.MAX_UPLOAD_BODY_BYTES)
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

    payload = await _read_json_dict(request, limit=config.MAX_UPLOAD_BODY_BYTES)
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
@app.head("/evidence/{rel_path:path}")
def get_evidence(rel_path: str, request: Request, role: str = Depends(auth.require_role("viewer"))):
    if request.method == "HEAD":
        path = media.resolve_evidence_path(rel_path)
        suf = path.suffix.lower()
        ctype = "video/webm" if suf == ".webm" else ("video/mp4" if suf == ".mp4" else "image/jpeg")
        return Response(
            status_code=200,
            media_type=ctype,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(path.stat().st_size)},
        )
    return media.serve_evidence_file(rel_path, request.headers.get("range"))


# ---------------------------------------------------------------------------
# Commands & Remote Config
# ---------------------------------------------------------------------------
@app.post("/api/v1/server/command")
@app.post("/api/v1/command")
async def post_command(request: Request, role: str = Depends(auth.require_role("operator"))):
    payload = await _read_json_dict(request)
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
    payload = await _read_json_dict(request)
    result = cmds.ack_command(payload.get("cmd_uid"), payload.get("result", "OK"), payload.get("detail"))
    auth.audit(request, 200, payload.get("vehicle_id"), detail=f"ack={payload.get('cmd_uid')}")
    return result


@app.get("/api/v1/commands")
def get_commands(vehicle_id: Optional[str] = None, role: str = Depends(auth.require_role("viewer"))):
    return {"commands": cmds.list_commands(vehicle_id)}


@app.post("/api/v1/config/{vehicle_id}")
async def set_config(vehicle_id: str, request: Request, role: str = Depends(auth.require_role("admin"))):
    patch = await _read_json_dict(request)
    res = cmds.set_remote_config(vehicle_id, patch)
    auth.audit(request, 200, vehicle_id, detail="remote_config_update")
    return res


@app.get("/api/v1/config/{vehicle_id}")
def get_config(vehicle_id: str, role: str = Depends(auth.require_role("viewer"))):
    return cmds.get_remote_config(vehicle_id)


# ---------------------------------------------------------------------------
# Real GNSS Positioning & Metric Track API (WGS-84 / ECEF / UTM / ENU — ZERO sample coords!)
# ---------------------------------------------------------------------------
@app.get("/api/v1/fleet/positions")
def get_fleet_positions(role: str = Depends(auth.require_role("viewer"))):
    return {"positions": geo_engine.list_fleet_positions()}


@app.get("/api/v1/vehicles/{vehicle_id}/location")
def get_vehicle_location(vehicle_id: str, role: str = Depends(auth.require_role("viewer"))):
    v = database.get_vehicle(vehicle_id)
    if not v:
        raise HTTPException(status_code=404, detail="Khong tim thay xe")
    return geo_engine.resolve_vehicle_position(v)


@app.get("/api/v1/vehicles/{vehicle_id}/track")
def get_vehicle_track(
    vehicle_id: str,
    limit: int = 300,
    since_s: Optional[float] = None,
    role: str = Depends(auth.require_role("viewer")),
):
    return geo_engine.get_vehicle_track(vehicle_id, limit=limit, since_s=since_s)


@app.get("/api/v1/geo/xyz")
def get_geo_xyz(vehicle_id: str, role: str = Depends(auth.require_role("viewer"))):
    v = database.get_vehicle(vehicle_id)
    if not v:
        return {"lat": None, "lon": None, "ecef": None, "is_sample_anchor": False, "fix_status": "NO_FIX"}
    pos = geo_engine.resolve_vehicle_position(v)
    return {
        "lat": pos["lat"],
        "lon": pos["lon"],
        "altitude_m": pos["altitude_m"],
        "ecef": pos["ecef"],
        "utm": pos["utm"],
        "dms": pos["dms"],
        "accuracy_m": pos["accuracy_m"],
        "fix_status": pos["fix_status"],
        "source": pos["source"],
        "is_sample_anchor": False,
    }


@app.post("/api/v1/geo/inject_xyz")
async def inject_geo_xyz(request: Request, role: str = Depends(auth.require_role("operator"))):
    raise HTTPException(
        status_code=410,
        detail="Da vo hieu hoa toa do mau (sample anchor). He thong chi tinh vi tri tu GNSS that (ESP32 #2 UART2 hoac Phone GNSS).",
    )


# ---------------------------------------------------------------------------
# Alert dismissal / Mute tracking
# ---------------------------------------------------------------------------
@app.post("/api/v1/events/alert_dismissed")
async def alert_dismissed(request: Request, role: str = Depends(auth.require_role("device"))):
    payload = await _read_json_dict(request)
    vehicle_id = payload.get("vehicle_id")
    if not vehicle_id:
        raise HTTPException(status_code=400, detail="Can vehicle_id")
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
        dedup_window_s=5.0,
    )
    if payload.get("driver_still_drowsy_after_5s"):
        mute_tracker.mark_still_risky(record["event_id"])
    auth.audit(request, 200, vehicle_id, detail="alert_dismissed")
    return {"ok": True, **record}


@app.post("/api/v1/server/command/dismiss")
async def remote_operator_dismiss(request: Request, role: str = Depends(auth.require_role("operator"))):
    payload = await _read_json_dict(request)
    vid = payload.get("vehicle_id")
    if not vid:
        raise HTTPException(status_code=400, detail="Can vehicle_id")
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
    payload = await _read_json_dict(request)
    vehicle_id = payload.get("vehicle_id")
    evt = payload.get("evt") or payload.get("event")
    if evt == "PHONE_DISCONNECTED":
        bluetooth_hub.disconnect_session(vehicle_id, payload.get("device_id", ""))
    else:
        bluetooth_hub.ingest_bluetooth_block(vehicle_id, payload.get("bluetooth_phone", payload))
    auth.audit(request, 200, vehicle_id, detail=f"bt={evt}")
    return {"ok": True, "evt": evt}


@app.post("/api/v1/phone/pair_otp")
@app.post("/api/v1/phone/pair/otp")
async def create_phone_pair_otp(request: Request, role: str = Depends(auth.require_role("operator"))):
    """Only operator/admin can mint a short-lived single-use OTP for phone pairing."""
    payload = await _read_json_dict(request)
    vid = payload.get("vehicle_id")
    if not vid:
        raise HTTPException(status_code=400, detail="Can vehicle_id")
    res = bluetooth_hub.issue_pair_otp(vid, payload.get("driver_id"), issued_by=role)
    auth.audit(request, 200, vid, detail="pair_otp_issued")
    return res


@app.post("/api/v1/phone/pair")
async def phone_pair(request: Request):
    """Redeems a single-use Operator-issued OTP for a driver_phone token (rate-limited; no prior login needed on /driver)."""
    payload = await _read_json_dict(request)
    vid = payload.get("vehicle_id")
    ip = auth.client_ip_of(request)
    if not auth.check_rate_limit(f"otp_pair:{ip}:{vid or ''}", limit=10, window_s=300.0):
        auth.audit(request, 429, vid, detail="otp_pair_rate_limited")
        raise HTTPException(status_code=429, detail="Qua nhieu lan thu ma OTP, vui long cho 5 phut")
    result = bluetooth_hub.pair_phone_with_otp(vid, payload.get("pair_code", ""), payload.get("driver_id"))
    auth.issue_token(result["driver_phone_token"], "driver_phone")
    auth.audit(request, 200, vid, detail="phone_paired")
    return result


@app.post("/api/v1/phone/telemetry")
async def phone_telemetry(request: Request, role: str = Depends(auth.require_role("driver_phone"))):
    payload = await _read_json_dict(request)
    vid = payload.get("vehicle_id")
    if role == "driver_phone":
        bluetooth_hub.verify_driver_phone_binding(getattr(request.state, "token", ""), vid)
    result = bluetooth_hub.phone_telemetry(vid, payload)
    auth.audit(request, 200, vid)
    return result


@app.post("/api/v1/phone/action")
async def phone_action(request: Request, role: str = Depends(auth.require_role("driver_phone"))):
    payload = await _read_json_dict(request)
    vid = payload.get("vehicle_id")
    if role == "driver_phone":
        bluetooth_hub.verify_driver_phone_binding(getattr(request.state, "token", ""), vid)
    result = bluetooth_hub.phone_action(vid, payload.get("action"), payload.get("detail"), actor_role=role)
    auth.audit(request, 200, vid, detail=f"phone_action={payload.get('action')}")
    return result


@app.get("/api/v1/phone/notifications")
def phone_notifications(request: Request, vehicle_id: str, role: str = Depends(auth.require_role("driver_phone"))):
    if role == "driver_phone":
        bluetooth_hub.verify_driver_phone_binding(getattr(request.state, "token", ""), vehicle_id)
    return {"notifications": bluetooth_hub.pull_notifications(vehicle_id)}


@app.get("/api/v1/phone/status")
def phone_status(request: Request, vehicle_id: str, role: str = Depends(auth.require_role("driver_phone"))):
    bluetooth_hub.verify_driver_phone_binding(getattr(request.state, "token", ""), vehicle_id)
    v = database.get_vehicle(vehicle_id)
    raw_t = (v or {}).get("last_telemetry_json")
    t: dict = {}
    if isinstance(raw_t, str) and raw_t.strip():
        try:
            parsed = json.loads(raw_t)
            if isinstance(parsed, dict):
                t = parsed
        except Exception:
            t = {}
    elif isinstance(raw_t, dict):
        t = raw_t
    ds = t.get("driver_state") or {}
    if not isinstance(ds, dict):
        ds = {}
    return {
        "ok": True,
        "source": "server_http",
        "vehicle_id": vehicle_id,
        "vehicle_status": (v or {}).get("status", "OFFLINE"),
        "last_seen": (v or {}).get("last_seen"),
        "camera": t.get("camera") or {},
        "driver_state": ds,
        "calibration": ds.get("calibration") or {},
        "drowsy_escalation": ds.get("drowsy_escalation") or {},
        "monitoring": ds.get("monitoring") or {},
        "esp": t.get("esp") or {},
        "gps": t.get("gps") or {},
        "pi": t.get("pi") or {},
        "remote_config": cmds.get_remote_config(vehicle_id),
    }


DISPUTE_REASON_LABELS = {
    "FALSE_DETECTION": "AI nhận diện nhầm hành vi",
    "SENSOR_ERROR": "Nhiễu cảm biến MQ-135 / GPS",
    "NOT_ME": "Đã đổi tài nhưng chưa kịp cập nhật",
    "OTHER": "Lý do khác",
}
DISPUTE_WINDOW_SECONDS = 48 * 3600


@app.get("/api/v1/phone/score_summary")
def phone_score_summary(request: Request, vehicle_id: str, role: str = Depends(auth.require_role("driver_phone"))):
    """Driver Safety Score, 3-tier status (>65 / <=65 / <=20), trip score history & recent violations with disputes."""
    if role == "driver_phone":
        bluetooth_hub.verify_driver_phone_binding(getattr(request.state, "token", ""), vehicle_id)
    conn = database.get_conn()
    v = database.get_vehicle(vehicle_id) or {}
    trips = database.rows_to_list(
        conn.execute(
            "SELECT trip_id, driver_id, driver_name, started_at, ended_at, distance_km, safety_score, "
            "violation_count, mute_count, status FROM trips WHERE vehicle_id=? ORDER BY started_at DESC LIMIT 20",
            (vehicle_id,),
        ).fetchall()
    )
    violations = database.rows_to_list(
        conn.execute(
            "SELECT v.event_id, v.vehicle_id, v.trip_id, v.alert_type, v.behavior_detail, v.duration_seconds, "
            "v.cnn_status, v.penalty_applied, v.human_review_status, v.human_reviewed_by, v.human_review_note, "
            "v.image_url, v.video_url, v.created_at, "
            "d.dispute_id, d.status AS dispute_status, d.reason_code AS dispute_reason_code, "
            "d.explanation AS dispute_explanation, d.response_note AS dispute_response_note "
            "FROM violations v LEFT JOIN disputes d ON d.violation_event_id = v.event_id "
            "WHERE v.vehicle_id=? ORDER BY v.created_at DESC LIMIT 50",
            (vehicle_id,),
        ).fetchall()
    )
    now_s = time.time()
    for row in violations:
        at = str(row.get("alert_type") or "")
        row["penalty_points"] = float(config.SAFETY_SCORE_PENALTY.get(at, 0.0))
        created = float(row.get("created_at") or 0.0)
        within_48h = (now_s - created) <= DISPUTE_WINDOW_SECONDS if created > 0 else True
        is_penalized = bool(row.get("penalty_applied")) and row["penalty_points"] > 0
        row["can_dispute"] = bool(
            within_48h
            and is_penalized
            and not row.get("dispute_status")
            and str(row.get("human_review_status") or "") != "HUMAN_FALSE_POSITIVE"
        )
    active_trip = next((t for t in trips if t.get("status") == "ACTIVE"), None)
    if active_trip and active_trip.get("safety_score") is not None:
        current_score = round(float(active_trip["safety_score"]), 1)
    elif trips and trips[0].get("safety_score") is not None:
        current_score = round(float(trips[0]["safety_score"]), 1)
    else:
        current_score = float(config.SAFETY_SCORE_BASE)

    tier = "RED_LOCKED" if current_score <= 20 else ("YELLOW_WARN" if current_score <= 65 else "GREEN_GOOD")
    return {
        "ok": True,
        "vehicle_id": vehicle_id,
        "driver_name": v.get("current_driver_name") or v.get("current_driver_id") or "Tài xế",
        "current_score": current_score,
        "score_tier": tier,
        "trip_count": len(trips),
        "trips": trips,
        "violations": violations,
    }


@app.post("/api/v1/phone/disputes")
async def create_phone_dispute(request: Request, role: str = Depends(auth.require_role("driver_phone"))):
    """Driver submits a 48h dispute on a confirmed penalized violation (1 dispute per violation)."""
    payload = await _read_json_dict(request)
    vid = str(payload.get("vehicle_id") or "").strip()
    event_id = str(payload.get("violation_event_id") or payload.get("event_id") or "").strip()
    reason_code = str(payload.get("reason_code") or "FALSE_DETECTION").strip().upper()
    explanation = str(payload.get("explanation") or "").strip()[:500]
    if not vid or not event_id:
        raise HTTPException(status_code=400, detail="Can vehicle_id va violation_event_id")
    if role == "driver_phone":
        bluetooth_hub.verify_driver_phone_binding(getattr(request.state, "token", ""), vid)
    if reason_code not in DISPUTE_REASON_LABELS:
        raise HTTPException(status_code=400, detail=f"reason_code khong hop le: {sorted(DISPUTE_REASON_LABELS)}")
    if not explanation:
        raise HTTPException(status_code=400, detail="Vui long nhap noi dung giai trinh khieu nai")

    now_s = time.time()
    dispute_id = f"DSP_{int(now_s * 1000)}_{uuid.uuid4().hex[:6]}"
    with database.tx() as c:
        vrow = c.execute("SELECT * FROM violations WHERE event_id=?", (event_id,)).fetchone()
        if not vrow:
            raise HTTPException(status_code=404, detail="Khong tim thay vi pham de khieu nai")
        v = dict(vrow)
        if str(v.get("vehicle_id") or "") != vid:
            raise HTTPException(status_code=403, detail="Ban chi duoc khieu nai vi pham cua xe minh")
        if (now_s - float(v.get("created_at") or now_s)) > DISPUTE_WINDOW_SECONDS:
            raise HTTPException(status_code=400, detail="Da qua thoi han khieu nai 48 gio")
        if str(v.get("human_review_status") or "") == "HUMAN_FALSE_POSITIVE":
            raise HTTPException(status_code=400, detail="Vi pham nay da duoc hoan diem")
        dup = c.execute("SELECT dispute_id FROM disputes WHERE violation_event_id=?", (event_id,)).fetchone()
        if dup:
            raise HTTPException(status_code=409, detail="Vi pham nay da duoc gui khieu nai truoc do")
        c.execute(
            """INSERT INTO disputes
               (dispute_id, violation_event_id, vehicle_id, driver_id, driver_name,
                reason_code, reason_label, explanation, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,'OPEN',?)""",
            (
                dispute_id,
                event_id,
                vid,
                v.get("driver_id"),
                v.get("driver_name"),
                reason_code,
                DISPUTE_REASON_LABELS[reason_code],
                explanation,
                now_s,
            ),
        )
    broker.publish("dispute_created", {"dispute_id": dispute_id, "vehicle_id": vid, "violation_event_id": event_id})
    auth.audit(request, 200, vid, detail=f"dispute_created={dispute_id}")
    return {
        "ok": True,
        "dispute_id": dispute_id,
        "violation_event_id": event_id,
        "status": "OPEN",
        "reason_code": reason_code,
        "reason_label": DISPUTE_REASON_LABELS[reason_code],
    }


@app.get("/api/v1/disputes")
def list_disputes(
    vehicle_id: Optional[str] = None,
    status: Optional[str] = None,
    role: str = Depends(auth.require_role("viewer")),
):
    conn = database.get_conn()
    q = (
        "SELECT d.*, v.alert_type, v.behavior_detail, v.image_url, v.video_url, v.penalty_applied, "
        "v.human_review_status, v.human_reviewed_by, v.trip_id "
        "FROM disputes d LEFT JOIN violations v ON v.event_id = d.violation_event_id WHERE 1=1"
    )
    params = []
    if vehicle_id:
        q += " AND d.vehicle_id=?"
        params.append(vehicle_id)
    if status:
        q += " AND d.status=?"
        params.append(status.upper())
    q += " ORDER BY d.created_at DESC LIMIT 200"
    rows = database.rows_to_list(conn.execute(q, params).fetchall())
    for r in rows:
        r["penalty_points"] = float(config.SAFETY_SCORE_PENALTY.get(str(r.get("alert_type") or ""), 0.0))
    return {"disputes": rows}


@app.post("/api/v1/disputes/{dispute_id}/resolve")
async def resolve_dispute(dispute_id: str, request: Request, role: str = Depends(auth.require_role("operator"))):
    """Admin/Operator resolves driver dispute (Four-Eyes Check: original operator reviewer cannot self-resolve)."""
    payload = await _read_json_dict(request)
    decision = str(payload.get("decision") or "").strip().upper()
    note = str(payload.get("response_note") or payload.get("note") or "").strip()[:500]
    if decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(status_code=400, detail="decision phai la APPROVED hoac REJECTED")
    if not note:
        raise HTTPException(status_code=400, detail="Bat buoc nhap noi dung phan hoi cho tai xe")

    now_s = time.time()
    need_refund = False
    trip_id = None
    alert_type = ""
    vid = ""
    event_id = ""
    with database.tx() as c:
        drow = c.execute("SELECT * FROM disputes WHERE dispute_id=?", (dispute_id,)).fetchone()
        if not drow:
            raise HTTPException(status_code=404, detail="Khong tim thay don khieu nai")
        d = dict(drow)
        if d.get("status") != "OPEN":
            raise HTTPException(status_code=400, detail="Don khieu nai da duoc xu ly truoc do")
        vid = str(d.get("vehicle_id") or "")
        event_id = str(d.get("violation_event_id") or "")
        vrow = c.execute("SELECT * FROM violations WHERE event_id=?", (event_id,)).fetchone()
        if vrow:
            v = dict(vrow)
            # Four-Eyes Check: non-admin operator who originally confirmed the penalty cannot self-resolve dispute
            if (
                role != "admin"
                and v.get("human_reviewed_by")
                and str(v.get("human_reviewed_by")).lower() == str(role).lower()
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Four-Eyes Check: Nguoi duyet phat ban dau khong duoc tu xu ly khieu nai (can Admin)",
                )
            trip_id = v.get("trip_id")
            alert_type = str(v.get("alert_type") or "")
            if decision == "APPROVED":
                if bool(v.get("penalty_applied")):
                    need_refund = True
                c.execute(
                    "UPDATE violations SET human_review_status='HUMAN_FALSE_POSITIVE', human_reviewed_by=?, "
                    "human_review_note=?, human_reviewed_at=?, penalty_applied=0 WHERE event_id=?",
                    (role, f"[Dispute APPROVED] {note}", now_s, event_id),
                )
        c.execute(
            "UPDATE disputes SET status=?, resolved_by=?, response_note=?, resolved_at=? WHERE dispute_id=?",
            (decision, role, note, now_s, dispute_id),
        )

    new_score = analytics.refund_violation_penalty(trip_id, alert_type) if need_refund else None
    msg = (
        f"Khiếu nại {dispute_id} ĐƯỢC CHẤP NHẬN (đã hoàn điểm): {note}"
        if decision == "APPROVED"
        else f"Khiếu nại {dispute_id} bị từ chối: {note}"
    )
    if vid:
        bluetooth_hub.push_notification(vid, msg, level="INFO" if decision == "APPROVED" else "WARN")
    broker.publish("dispute_resolved", {"dispute_id": dispute_id, "vehicle_id": vid, "decision": decision, "safety_score": new_score})
    auth.audit(request, 200, vid, detail=f"dispute_{decision}={dispute_id}")
    return {
        "ok": True,
        "dispute_id": dispute_id,
        "status": decision,
        "refunded": need_refund,
        "safety_score": new_score,
        "response_note": note,
    }


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
    payload = await _read_json_dict(request, allow_empty=True)
    res = analytics.ack_incident(incident_id, payload.get("ack_by", role), payload.get("note", ""))
    auth.audit(request, 200, detail=f"ack_incident={incident_id}")
    return res


@app.post("/api/v1/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str, request: Request, role: str = Depends(auth.require_role("operator"))):
    payload = await _read_json_dict(request, allow_empty=True)
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
    """Human-in-the-Loop review: atomically updates violation & refunds/applies Safety Score once (fixes BUG-08!)."""
    payload = await _read_json_dict(request)
    decision = str(payload.get("decision") or "").strip().upper()
    note = str(payload.get("note") or "").strip()[:300]
    if decision not in ("HUMAN_CONFIRMED", "HUMAN_FALSE_POSITIVE"):
        raise HTTPException(status_code=400, detail="decision phai la HUMAN_CONFIRMED hoac HUMAN_FALSE_POSITIVE")

    now_s = time.time()
    need_refund = False
    need_penalize = False
    with database.tx() as c:
        row = c.execute("SELECT * FROM violations WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Khong tim thay vi pham")
        v = dict(row)
        already_penalized = bool(v.get("penalty_applied"))
        trip_id = v.get("trip_id")
        alert_type = v.get("alert_type") or ""
        new_penalty_flag = 1 if already_penalized else 0
        non_penalty_types = config.NON_PENALTY_ALERT_TYPES
        if decision == "HUMAN_FALSE_POSITIVE" and already_penalized:
            need_refund = True
            new_penalty_flag = 0
        elif decision == "HUMAN_CONFIRMED" and not already_penalized and alert_type.strip().upper() not in non_penalty_types:
            need_penalize = True
            new_penalty_flag = 1
        c.execute(
            "UPDATE violations SET human_review_status=?, human_reviewed_by=?, human_review_note=?, "
            "human_reviewed_at=?, penalty_applied=? WHERE event_id=?",
            (decision, role, note, now_s, new_penalty_flag, event_id),
        )

    new_score = None
    if need_refund:
        new_score = analytics.refund_violation_penalty(trip_id, alert_type)
    elif need_penalize:
        new_score = analytics.apply_violation_penalty(trip_id, alert_type)

    if decision == "HUMAN_FALSE_POSITIVE":
        # Archive burst into hard_negatives/ with operator feedback so future AI retraining learns from human review
        bdir = v.get("burst_dir")
        if bdir:
            def _copy_hn():
                try:
                    import shutil as _shutil
                    src = config.EVIDENCE_DIR / bdir
                    dst = config.HARD_NEGATIVES_DIR / f"human_fp_{bdir}"
                    if src.exists() and not dst.exists():
                        _shutil.copytree(src, dst)
                    if dst.exists():
                        (dst / "human_review.json").write_text(
                            json.dumps(
                                {"event_id": event_id, "decision": decision, "reviewed_by": role, "note": note, "ts": now_s},
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                except Exception:
                    pass
            await asyncio.to_thread(_copy_hn)

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
    fmt_clean = (fmt or "csv").strip().lower()
    if fmt_clean not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="fmt phai la csv hoac json")
    exporters = {
        "violations": reports.export_violations,
        "dismissals": reports.export_dismissals,
        "trips": reports.export_trips,
    }
    fn = exporters.get(kind)
    if not fn:
        raise HTTPException(status_code=400, detail="kind phai la violations|dismissals|trips")
    data, name = fn(fmt_clean, vehicle_id, driver_id, start_ts, end_ts)
    media_type = "application/json" if fmt_clean == "json" else "text/csv"
    return Response(content=data, media_type=media_type, headers={"Content-Disposition": f"attachment; filename={name}.{fmt_clean}"})


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
    payload = await _read_json_dict(request)
    token = payload.get("token") or secrets.token_hex(24)
    target_role = payload.get("role", "viewer")
    try:
        auth.issue_token(token, target_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auth.audit(request, 200, detail=f"issue_token={target_role}")
    return {"ok": True, "token": token, "role": target_role, "fp": auth.token_fingerprint(token)}


@app.post("/api/v1/admin/tokens/revoke")
async def admin_revoke_token_safe(request: Request, role: str = Depends(auth.require_role("admin"))):
    """Safe revoke via JSON body (fingerprint or token) — avoids putting secrets into URLs or access logs."""
    payload = await _read_json_dict(request)
    fp = str(payload.get("fingerprint") or payload.get("fp") or "").strip()
    tok = str(payload.get("token") or "").strip()
    if fp:
        existed = auth.revoke_token_by_fingerprint(fp)
    elif tok:
        existed = auth.revoke_token(tok)
    else:
        raise HTTPException(status_code=400, detail="Can 'fingerprint' hoac 'token'")
    auth.audit(request, 200, detail=f"revoke_token_existed={existed}")
    return {"ok": True, "revoked": existed}


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

_APP_IOT_DIR = config.BASE_DIR.parent / "APP_IOT"
if _APP_IOT_DIR.exists():
    app.mount("/app_iot", StaticFiles(directory=str(_APP_IOT_DIR)), name="app_iot")


@app.get("/driver")
def driver_pwa_index():
    driver_html = _APP_IOT_DIR / "driver.html"
    if not driver_html.exists():
        raise HTTPException(status_code=404, detail="APP_IOT/driver.html chua duoc cai dat")
    return RedirectResponse(url="/app_iot/driver.html", status_code=307)


@app.get("/")
@app.get("/login")
def dashboard_index():
    return FileResponse(str(config.STATIC_DIR / "index.html"))
