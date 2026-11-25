"""Optional Asynchronous Bridge from PI_AI server_ai -> PBL4_HTTM Node.js/Prisma Backend (:3000).

Option A2 (Cherry-picked & hardened from feat/pbl4-bridge 2a97e17):
  - DISABLED by default when PBL4_BACKEND_URL="" (0 background threads, 0 outbound sockets).
  - HTTP Keep-Alive via requests.Session (stdlib urllib fallback) + bounded retry on 5xx/network error.
  - Video size guard (<= 25 MB) before multipart framing so memory stays bounded.
  - Respects MAIN CO2 non-penalty policy: live co2_ppm is forwarded in /api/v1/edge/telemetry every 3s,
    while NON_PENALTY_ALERT_TYPES (AUTO_CO2_*, SOS, ESP_BROWNOUT) are NOT sent to punitive
    /api/v1/edge/violations unless PBL4_FORWARD_NON_PENALTY=1 is explicitly enabled.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from server_ai.server_core import config

MAX_BRIDGE_VIDEO_BYTES = 25 * 1024 * 1024  # 25 MB RAM safety cap

ALERT_TO_PBL4_CODE = {
    "DROWSY": "MICROSLEEP",
    "DROWSY_ALERT": "MICROSLEEP",
    "EYE_CLOSED": "MICROSLEEP",
    "MICROSLEEP": "MICROSLEEP",
    "YAWNING": "YAWNING_FATIGUE",
    "YAWNING_ALERT": "YAWNING_FATIGUE",
    "MOUTH_OPEN": "YAWNING_FATIGUE",
    "YAWNING_FATIGUE": "YAWNING_FATIGUE",
    "PHONE": "PHONE_DISTRACTION",
    "PHONE_DISTRACTION": "PHONE_DISTRACTION",
    "DISTRACTED": "PHONE_DISTRACTION",
    "DISTRACTED_ALERT": "PHONE_DISTRACTION",
    "DISTRACTED HEAD": "PHONE_DISTRACTION",
    "CO2_CRIT": "HIGH_CO2",
    "AUTO_CO2_CRIT": "HIGH_CO2",
    "CO2_WARN": "HIGH_CO2",
    "AUTO_CO2_WARN": "HIGH_CO2",
    "HIGH_CO2": "HIGH_CO2",
    "TAMPER": "DEVICE_TAMPERING",
    "CAMERA_TAMPER_SUSPECTED": "DEVICE_TAMPERING",
    "DEVICE_TAMPERING": "DEVICE_TAMPERING",
}


def map_alert_to_pbl4_code(alert_type: Optional[str]) -> str:
    raw = str(alert_type or "").strip().upper()
    if raw in ("SMOKING",):
        return config.PBL4_SMOKING_CODE or "PHONE_DISTRACTION"
    if raw in ALERT_TO_PBL4_CODE:
        return ALERT_TO_PBL4_CODE[raw]
    if "DROWS" in raw or "SLEEP" in raw or "EYE" in raw:
        return "MICROSLEEP"
    if "YAWN" in raw or "MOUTH" in raw:
        return "YAWNING_FATIGUE"
    if "CO2" in raw:
        return "HIGH_CO2"
    if "TAMPER" in raw or "CAMERA" in raw:
        return "DEVICE_TAMPERING"
    return "PHONE_DISTRACTION"


def build_pbl4_telemetry_body(vehicle_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build JSON matching PBL4_HTTM backend/src/modules/iot/iot.service.js:processIotDataPacket."""
    gps = payload.get("gps") or {}
    mq = payload.get("mq135") or {}
    body: Dict[str, Any] = {
        "device_id": config.PBL4_DEVICE_ID or vehicle_id,
        "toc_do_kmh": float(gps.get("speed_kmh") or 0.0),
        "co2_ppm": float(mq.get("co2_ppm") if mq.get("co2_ppm") is not None else (mq.get("ppm") or 400.0)),
        # Keep aiViolationFlag null here so Node.js won't insert a placeholder sample video;
        # real stitched violations are sent via /api/v1/edge/violations once MP4 is ready!
        "aiViolationFlag": None,
    }
    if gps.get("is_valid") and gps.get("lat") is not None and gps.get("lon") is not None:
        body["vi_do_gps"] = float(gps["lat"])
        body["kinh_do_gps"] = float(gps["lon"])
    if config.PBL4_TRIP_ID:
        try:
            body["trip_id"] = int(config.PBL4_TRIP_ID)
        except ValueError:
            pass
    return body


def build_pbl4_heartbeat_body(vehicle_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build JSON matching PBL4_HTTM backend/src/modules/iot/iot.service.js:processDeviceHeartbeat."""
    pi = payload.get("pi") or {}
    cam = payload.get("camera") or {}
    cam_status = str(cam.get("status") or "ONLINE").upper()
    conn_state = "ERROR_CAMERA" if cam_status == "OFFLINE" else "ONLINE"
    body: Dict[str, Any] = {
        "ma_thiet_bi": config.PBL4_DEVICE_ID or vehicle_id,
        "trang_thai_ket_noi": conn_state,
        "ai_model_version": "PI_AI-YOLO-Tier1",
    }
    if pi.get("cpu_temp_c") is not None:
        body["cpu_temp_c"] = float(pi["cpu_temp_c"])
    return body


def build_multipart_video_upload(
    payload_obj: Dict[str, Any],
    video_path: Optional[Path],
) -> Tuple[str, bytes]:
    """Encode multipart/form-data with 'payload' JSON string + optional bounded 'video' binary field."""
    boundary = f"----PIAI{uuid.uuid4().hex}"
    chunks = []
    payload_bytes = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
    chunks.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="payload"\r\n'
        f"Content-Type: application/json; charset=utf-8\r\n\r\n".encode("utf-8")
        + payload_bytes
        + b"\r\n"
    )
    if video_path is not None and video_path.exists() and video_path.is_file():
        if video_path.stat().st_size <= MAX_BRIDGE_VIDEO_BYTES:
            suf = video_path.suffix.lower()
            ctype = "video/webm" if suf == ".webm" else "video/mp4"
            fname = video_path.name
            chunks.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="video"; filename="{fname}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
                + video_path.read_bytes()
                + b"\r\n"
            )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)


class PBL4BridgeClient:
    """Thread-safe async forwarder to PBL4_HTTM Node.js backend (0 overhead when disabled)."""

    def __init__(
        self,
        sender: Optional[Callable[[str, bytes, Dict[str, str], float], Tuple[int, str]]] = None,
        max_retries: int = 3,
        retry_backoff_s: float = 0.4,
    ) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_telem_mono: Dict[str, float] = {}
        self._last_hb_mono: Dict[str, float] = {}
        self._last_cam_offline: Dict[str, bool] = {}
        self._session = None
        if sender is None:
            try:
                import requests  # reuse HTTP Keep-Alive connections across bursts
                self._session = requests.Session()
            except Exception:
                self._session = None
        self._sender = sender or self._http_post
        self._max_retries = max(1, int(max_retries))
        self._retry_backoff_s = max(0.0, float(retry_backoff_s))
        self.forwarded_ok = 0
        self.forwarded_err = 0
        self.last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(config.PBL4_BACKEND_URL)

    def start(self) -> None:
        with self._lock:
            if not self.enabled or (self._thread is not None and self._thread.is_alive()):
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker, name="pbl4-bridge", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        with self._lock:
            t = self._thread
            self._thread = None
        if t is not None:
            t.join(timeout=2.0)
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass

    def _auth_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h: Dict[str, str] = {"User-Agent": "PI_AI_Bridge/1.1"}
        tok = config.PBL4_DEVICE_TOKEN
        if tok:
            h["x-device-token"] = tok
            h["Authorization"] = f"Bearer {tok}"
        if extra:
            h.update(extra)
        return h

    def _http_post(self, url: str, body: bytes, headers: Dict[str, str], timeout_s: float) -> Tuple[int, str]:
        if self._session is not None:
            resp = self._session.post(url, data=body, headers=headers, timeout=timeout_s)
            return int(resp.status_code), resp.text[:512]
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return int(resp.status), resp.read(512).decode("utf-8", errors="ignore")

    def _enqueue(self, job: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            return False

    def forward_telemetry(self, vehicle_id: str, payload: Dict[str, Any], now_mono: Optional[float] = None) -> None:
        if not self.enabled:
            return
        now_m = time.monotonic() if now_mono is None else float(now_mono)
        with self._lock:
            last_t = self._last_telem_mono.get(vehicle_id, -999.0)
            due_telem = (now_m - last_t) >= config.PBL4_TELEMETRY_INTERVAL_S
            if due_telem:
                self._last_telem_mono[vehicle_id] = now_m

            cam_off = str((payload.get("camera") or {}).get("status") or "ONLINE").upper() == "OFFLINE"
            prev_off = self._last_cam_offline.get(vehicle_id, False)
            last_hb = self._last_hb_mono.get(vehicle_id, -999.0)
            due_hb = (now_m - last_hb) >= config.PBL4_HEARTBEAT_INTERVAL_S or (cam_off and not prev_off)
            if due_hb:
                self._last_hb_mono[vehicle_id] = now_m
            self._last_cam_offline[vehicle_id] = cam_off

        if due_telem:
            self._enqueue({
                "kind": "telemetry",
                "vehicle_id": vehicle_id,
                "body": build_pbl4_telemetry_body(vehicle_id, payload),
            })
        if due_hb:
            self._enqueue({
                "kind": "heartbeat",
                "vehicle_id": vehicle_id,
                "body": build_pbl4_heartbeat_body(vehicle_id, payload),
            })

    def forward_violation(
        self,
        *,
        event_id: str,
        idempotency_key: str,
        vehicle_id: str,
        alert_type: str,
        confidence: float,
        detail: str,
        video_rel_name: Optional[str] = None,
    ) -> bool:
        if not self.enabled:
            return False
        norm = str(alert_type or "").strip().upper()
        # Respect MAIN CO2/SOS non-penalty policy unless explicitly overridden
        if norm in config.NON_PENALTY_ALERT_TYPES and not config.PBL4_FORWARD_NON_PENALTY:
            return False
        vid_path = (config.EVIDENCE_DIR / video_rel_name) if video_rel_name else None
        device_code = config.PBL4_DEVICE_ID or vehicle_id
        pbl4_payload: Dict[str, Any] = {
            "client_event_id": idempotency_key or event_id,
            "device_id": device_code,
            "ma_thiet_bi": device_code,
            "ma_loai": map_alert_to_pbl4_code(alert_type),
            "edge_confidence": round(float(confidence), 3),
            "ai_confidence_score": round(float(confidence), 3),
            "mo_ta": f"[{vehicle_id} | {alert_type}] {detail}".strip()[:300],
        }
        if config.PBL4_TRIP_ID:
            try:
                pbl4_payload["trip_id"] = int(config.PBL4_TRIP_ID)
            except ValueError:
                pass
        return self._enqueue({
            "kind": "violation",
            "idempotency_key": idempotency_key or event_id,
            "payload": pbl4_payload,
            "video_path": str(vid_path) if vid_path else None,
        })

    def process_job_sync(self, job: Dict[str, Any]) -> bool:
        base = config.PBL4_BACKEND_URL.rstrip("/")
        kind = job.get("kind")
        if kind == "telemetry":
            url = f"{base}/api/v1/edge/telemetry"
            raw = json.dumps(job["body"], ensure_ascii=False).encode("utf-8")
            hdrs = self._auth_headers({"Content-Type": "application/json; charset=utf-8"})
            timeout_s = 4.0
        elif kind == "heartbeat":
            url = f"{base}/api/v1/edge/heartbeat"
            raw = json.dumps(job["body"], ensure_ascii=False).encode("utf-8")
            hdrs = self._auth_headers({"Content-Type": "application/json; charset=utf-8"})
            timeout_s = 4.0
        elif kind == "violation":
            url = f"{base}/api/v1/edge/violations"
            vp = Path(job["video_path"]) if job.get("video_path") else None
            ct, raw = build_multipart_video_upload(job["payload"], vp)
            hdrs = self._auth_headers({
                "Content-Type": ct,
                "Idempotency-Key": str(job.get("idempotency_key") or ""),
            })
            timeout_s = 15.0
        else:
            return False

        # Retry transient network / 5xx failures (never retry permanent 4xx errors)
        attempts = self._max_retries if kind == "violation" else min(2, self._max_retries)
        for attempt in range(attempts):
            try:
                code, _ = self._sender(url, raw, hdrs, timeout_s)
                if 200 <= code < 300:
                    self.forwarded_ok += 1
                    self.last_error = ""
                    return True
                self.last_error = f"HTTP {code} ({kind})"
                if 400 <= code < 500:
                    break
            except Exception as exc:
                self.last_error = f"{kind}: {exc}"
            if attempt + 1 < attempts and not self._stop.is_set() and self._retry_backoff_s > 0:
                time.sleep(self._retry_backoff_s * (attempt + 1))

        self.forwarded_err += 1
        return False

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                self._queue.task_done()
                break
            try:
                self.process_job_sync(job)
            finally:
                self._queue.task_done()


pbl4_bridge = PBL4BridgeClient()
