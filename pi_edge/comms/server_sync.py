"""Server of AI HTTP Keep-Alive Sync Client for Pi Edge (LCK-04, LCK-05).

Guarantees:
  1. STRICT ZERO-EGRESS in RUN_MODE=local (worker never starts, send_* refuses immediately).
  2. Single requests.Session() HTTP Keep-Alive connection reuse (no TCP handshake every 1.5s!).
  3. 100% compliant with contracts/pi_server_schema.json (co2_ppm, cpu_temp_c, camera.face_detected, stream_url).
  4. Multipart /api/v1/violation_burst (NO 250KB JPEG Base64 or UTF-8 crammed into HTTP headers!).
  5. Full 2-way command lifecycle: executes whitelisted commands and posts /api/v1/commands/ack with cmd_uid.
  6. Applies remote_config safely via callback.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from pi_edge.core.access_control import is_allowed_control_command
from pi_edge.core.config import EdgeConfig
from pi_edge.evidence.spool_store import UnifiedSpoolStore


def _local_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class ServerSyncClient:
    def __init__(
        self,
        cfg: EdgeConfig,
        auth_token: str,
        spool: UnifiedSpoolStore,
        on_command: Optional[Callable[[str], Tuple[bool, str]]] = None,
        on_remote_config: Optional[Callable[[Dict[str, Any]], None]] = None,
        session: Optional[Any] = None,
    ):
        self.cfg = cfg
        self.enabled = bool(cfg.remote_enabled)  # False unconditionally in RUN_MODE=local!
        self.server_url = cfg.server_url.rstrip("/")
        self.auth_token = (auth_token or "").strip()
        self.spool = spool
        self.on_command = on_command
        self.on_remote_config = on_remote_config

        self._session = session if session is not None else requests.Session()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._latest_state: Dict[str, Any] = {}
        self._pending_hw_events: List[str] = []
        self._pending_mute_newly: bool = False
        self._seq = 0
        self._tx_seq = 0
        self.server_online = False
        self.last_error = ""
        self.worker_errors = 0
        self.egress_attempts = 0
        self.local_ip = _local_lan_ip()
        self._last_ip_refresh_mono = time.monotonic()

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h = {"User-Agent": f"PI_AI_Edge/{self.cfg.vehicle_id}"}
        if self.auth_token:
            h["Authorization"] = f"Bearer {self.auth_token}"
            h["X-Auth-Token"] = self.auth_token
        if extra:
            h.update(extra)
        return h

    def start(self) -> None:
        if not self.enabled:
            # LCK-04: RUN_MODE=local MUST NOT start network worker!
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="server-sync", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        try:
            self._session.close()
        except Exception:
            pass

    def update_state(self, state: Dict[str, Any]) -> None:
        """Called ONCE per tick from main loop; latches edge-triggered HW/MUTE events until uploaded (fixes BUG-09!)."""
        with self._lock:
            esp_blk = state.get("esp")
            if isinstance(esp_blk, dict):
                evts = esp_blk.get("hw_events") or []
                if evts:
                    for e in evts:
                        if len(self._pending_hw_events) < 50:
                            self._pending_hw_events.append(str(e))
            ms_blk = state.get("mute_state")
            if isinstance(ms_blk, dict) and ms_blk.get("newly_triggered"):
                self._pending_mute_newly = True
            self._latest_state = dict(state)

    def build_telemetry_payload(
        self,
        ai_state: Dict[str, Any],
        hw_snap: Dict[str, Any],
        fps_snap: Dict[str, float],
        esp_sensor: Dict[str, Any],
        esp_status: Dict[str, Any],
        mute_state: Dict[str, Any],
        camera_stale: bool = False,
        camera_error: str = "",
        bluetooth_phone: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Builds 100% contracts/pi_server_schema.json compliant telemetry."""
        self._seq += 1
        co2 = float(esp_sensor.get("co2_ppm") or 0.0)
        air_lv = "DANGER" if co2 >= 1500 else ("WARNING" if co2 >= 1000 else "GOOD")
        gps = esp_sensor.get("gps") or {"lat": None, "lon": None, "speed_kmh": 0.0, "satellites": 0, "is_valid": False}
        stream_url = f"http://{self.local_ip}:{self.cfg.web_port}/stream" if self.cfg.enable_live_stream else None
        driver_state_blk: Dict[str, Any] = {
            "alert_state": str(ai_state.get("alert_state", "NORMAL")),
            "alert_type": str(ai_state.get("alert_type", "NORMAL")),
            "verification_mode": str(ai_state.get("verification_mode", "SINGLE_MODEL_YOLO")),
            "alert_reason": str(ai_state.get("alert_reason", "")),
            "behavior_detail": str(ai_state.get("behavior_detail", "")),
            "detected_classes": list(ai_state.get("detected_classes") or []),
            "allow_smoking": bool(ai_state.get("allow_smoking", True)),
            "allow_phone": bool(ai_state.get("allow_phone", True)),
        }
        cal_blk = ai_state.get("calibration")
        if isinstance(cal_blk, dict):
            driver_state_blk["calibration"] = dict(cal_blk)
        d_esc_blk = ai_state.get("drowsy_escalation")
        if isinstance(d_esc_blk, dict):
            driver_state_blk["drowsy_escalation"] = dict(d_esc_blk)
        mon_blk = ai_state.get("monitoring")
        if isinstance(mon_blk, dict):
            driver_state_blk["monitoring"] = dict(mon_blk)

        payload = {
            "schema_version": "1.0.0",
            "vehicle_id": self.cfg.vehicle_id,
            "vehicle_name": self.cfg.vehicle_name,
            "driver_name": self.cfg.driver_name,
            "timestamp_ms": int(time.time() * 1000),
            "telemetry_seq": self._seq,
            "run_mode": self.cfg.run_mode,
            "access_info": {
                "local_ip": self.local_ip,
                "web_port": self.cfg.web_port,
                "stream_url": stream_url,
            },
            "camera": {
                "status": "OFFLINE" if camera_error else "ONLINE",
                "fps": float(fps_snap.get("camera_fps", 0.0)),
                "face_detected": bool(ai_state.get("face_detected", False)),
                "is_stale": bool(camera_stale),
                "resolution": f"{self.cfg.camera_width}x{self.cfg.camera_height}",
                "error": camera_error,
            },
            "gps": {
                "lat": gps.get("lat"),
                "lon": gps.get("lon"),
                "speed_kmh": float(gps.get("speed_kmh") or 0.0),
                "altitude_m": gps.get("altitude_m"),
                "heading_deg": gps.get("heading_deg"),
                "hdop": gps.get("hdop"),
                "accuracy_m": gps.get("accuracy_m"),
                "fix_quality": int(gps.get("fix_quality") or 0),
                "satellites": int(gps.get("satellites") or 0),
                "sats_in_view": gps.get("sats_in_view"),
                "gps_rx_bytes": gps.get("gps_rx_bytes"),
                "nmea_ok": gps.get("nmea_ok"),
                "wiring_status": gps.get("wiring_status"),
                "is_valid": bool(gps.get("is_valid", False)),
                "source": gps.get("source", "esp32_gps"),
            },
            "mq135": {
                "co2_ppm": co2,
                "raw_adc": int(esp_sensor.get("raw_adc") or 0),
                "air_quality_level": air_lv,
            },
            "pi": {
                "cpu_temp_c": hw_snap.get("temp_c"),
                "core_volt_v": hw_snap.get("core_volt_v"),
                "cpu_usage_pct": float(hw_snap.get("cpu_percent") or 0.0),
                "ram_used_mb": float(hw_snap.get("ram_mb") or 0.0),
                "thermal_level": int(ai_state.get("thermal_level", 0)),
                "throttled": hw_snap.get("throttled", "0x0"),
                "camera_fps": float(fps_snap.get("camera_fps", 0.0)),
                "loop_fps": float(fps_snap.get("loop_fps", 0.0)),
                "ai_fps": float(fps_snap.get("ai_fps", 0.0)),
                "stream_fps": float(fps_snap.get("stream_fps", 0.0)),
                "alert_latency_p95_ms": float(fps_snap.get("alert_latency_p95_ms", 0.0)),
            },
            "driver_state": driver_state_blk,
            "esp": esp_status,
            "mute_state": mute_state,
        }
        if bluetooth_phone is not None:
            payload["bluetooth_phone"] = bluetooth_phone
        return payload

    def queue_violation_burst(
        self,
        alert_type: str,
        verification_mode: str,
        duration_seconds: float,
        jpeg_frames: List[Tuple[str, bytes]],
        gps: Optional[Dict[str, Any]] = None,
        co2_ppm: float = 0.0,
        behavior_detail: str = "",
        confidence: Optional[float] = None,
        burst_fps: float = 4.0,
    ) -> str:
        """Persists JPEG burst to unified SQLite spool (works offline & in local mode without egress)."""
        now_s = time.time()
        event_id = f"EVT_{int(now_s * 1000)}_{uuid.uuid4().hex[:6]}"
        idem_key = f"IDEM_{self.cfg.vehicle_id}_{event_id}"
        eff_conf = 0.0 if confidence is None else max(0.0, min(1.0, float(confidence)))
        meta = {
            "schema_version": "1.0.0",
            "event_id": event_id,
            "idempotency_key": idem_key,
            "vehicle_id": self.cfg.vehicle_id,
            "driver_name": self.cfg.driver_name,
            "alert_state": "EDGE_TRIGGERED",
            "alert_type": alert_type,
            "verification_mode": verification_mode,
            "behavior_detail": behavior_detail,
            "started_at": round(now_s - float(duration_seconds), 3),
            "confirmed_at": round(now_s, 3),
            "duration_seconds": round(float(duration_seconds), 3),
            "confidence": round(eff_conf, 3),
            "burst_fps": round(max(1.0, min(15.0, float(burst_fps))), 2),
            "gps": gps or {},
            "co2_ppm": float(co2_ppm),
        }
        self.spool.enqueue_violation_burst(event_id, idem_key, meta, jpeg_frames)
        if self.enabled:
            self._wake.set()
        return event_id

    def queue_alert_dismissed(self, dismiss_source: str, alert_type: str, gps: Optional[Dict[str, Any]] = None) -> str:
        now_ms = int(time.time() * 1000)
        event_id = f"MUTE_{now_ms}_{uuid.uuid4().hex[:6]}"
        meta = {
            "event_id": event_id,
            "vehicle_id": self.cfg.vehicle_id,
            "dismiss_source": dismiss_source,
            "dismissed_alert_type": alert_type,
            "dismissed_at_ms": now_ms,
            "gps": gps or {},
        }
        self.spool.enqueue_json_event(event_id, event_id, "alert_dismissed", meta)
        if self.enabled:
            self._wake.set()
        return event_id

    def send_telemetry_once(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        self.egress_attempts += 1
        try:
            r = self._session.post(
                f"{self.server_url}/api/v1/telemetry",
                json=payload,
                headers=self._headers(),
                timeout=2.5,
            )
            if 200 <= r.status_code < 300:
                self.server_online = True
                self.last_error = ""
                body = r.json()
                self._handle_downlink(body)
                return body
            self.server_online = False
            self.last_error = f"HTTP {r.status_code}"
            return None
        except Exception as exc:
            self.server_online = False
            self.last_error = str(exc)
            return None

    def _handle_downlink(self, body: Dict[str, Any]) -> None:
        if not isinstance(body, dict):
            return
        rcfg = body.get("remote_config")
        if isinstance(rcfg, dict) and self.on_remote_config:
            try:
                self.on_remote_config(rcfg)
            except Exception:
                pass
        for item in body.get("commands") or []:
            if not isinstance(item, dict):
                continue
            cmd = str(item.get("cmd") or item.get("command") or "").strip()
            cmd_uid = item.get("cmd_uid")
            if not cmd:
                continue
            if not is_allowed_control_command(cmd):
                if cmd_uid:
                    self._send_command_ack(cmd_uid, "FAILED", "NOT_WHITELISTED")
                continue
            ok, detail = (True, "NO_HANDLER")
            if self.on_command:
                try:
                    ok, detail = self.on_command(cmd)
                except Exception as exc:
                    ok, detail = False, str(exc)
            if cmd_uid:
                self._send_command_ack(cmd_uid, "OK" if ok else "FAILED", detail)

    def _send_command_ack(self, cmd_uid: str, result: str, detail: str) -> None:
        if not self.enabled:
            return
        self.egress_attempts += 1
        try:
            self._session.post(
                f"{self.server_url}/api/v1/commands/ack",
                json={"cmd_uid": cmd_uid, "vehicle_id": self.cfg.vehicle_id, "result": result, "detail": detail},
                headers=self._headers(),
                timeout=2.0,
            )
        except Exception:
            pass

    def drain_spool_once(self, max_items: int = 4) -> int:
        if not self.enabled:
            return 0
        uploaded = 0
        for item in self.spool.fetch_due(limit=max_items):
            eid = item["event_id"]
            idem = item["idempotency_key"]
            kind = item["kind"]
            meta = item["meta"]
            self.egress_attempts += 1
            try:
                if kind == "violation_burst":
                    frames = self.spool.load_burst_files(item.get("burst_rel_dir"))
                    meta_str = json.dumps(meta, ensure_ascii=False)
                    if frames:
                        files = [("frames", (fn, raw, "image/jpeg")) for fn, raw in frames]
                        r = self._session.post(
                            f"{self.server_url}/api/v1/violation_burst",
                            data={"meta": meta_str},
                            files=files,
                            headers=self._headers({"Idempotency-Key": idem}),
                            timeout=10.0,
                        )
                    else:
                        # Force true multipart/form-data even with 0 JPEG frames (SOS, CO2, ESP_BROWNOUT!)
                        r = self._session.post(
                            f"{self.server_url}/api/v1/violation_burst",
                            files=[("meta", (None, meta_str.encode("utf-8"), "application/json"))],
                            headers=self._headers({"Idempotency-Key": idem}),
                            timeout=10.0,
                        )
                elif kind == "alert_dismissed":
                    r = self._session.post(
                        f"{self.server_url}/api/v1/events/alert_dismissed",
                        json=meta,
                        headers=self._headers({"Idempotency-Key": idem}),
                        timeout=4.0,
                    )
                else:
                    self.spool.mark_failed(eid, f"unknown_kind:{kind}", permanent=True)
                    continue

                if 200 <= r.status_code < 300:
                    self.spool.mark_uploaded(eid)
                    self.server_online = True
                    uploaded += 1
                elif r.status_code in (400, 413, 422):
                    self.spool.mark_failed(eid, f"HTTP {r.status_code}: {r.text[:120]}", permanent=True)
                else:
                    # F-004: Transient 401/403/404/408/429/5xx NEVER dead-letter — keep PENDING through long 4G/tunnel outages!
                    self.spool.mark_failed(eid, f"HTTP {r.status_code}", permanent=False, transient=True)
                    break
            except Exception as exc:
                self.spool.mark_failed(eid, str(exc), permanent=False, transient=True)
                self.server_online = False
                break
        return uploaded

    def _run_loop(self) -> None:
        last_telem = 0.0
        while not self._stop.is_set():
            try:
                now_m = time.monotonic()
                if self.local_ip == "127.0.0.1" and (now_m - self._last_ip_refresh_mono) >= 30.0:
                    self.local_ip = _local_lan_ip()
                    self._last_ip_refresh_mono = now_m
                if (now_m - last_telem) >= self.cfg.telemetry_interval_s:
                    latched_hw: List[str] = []
                    latched_mute = False
                    with self._lock:
                        payload = dict(self._latest_state) if self._latest_state else None
                        if payload is not None:
                            latched_hw = list(self._pending_hw_events)
                            latched_mute = self._pending_mute_newly
                    if payload:
                        self._tx_seq += 1
                        payload["telemetry_seq"] = self._tx_seq
                        if latched_hw:
                            esp_copy = dict(payload.get("esp") or {})
                            existing = list(esp_copy.get("hw_events") or [])
                            for e in latched_hw:
                                if e not in existing:
                                    existing.append(e)
                            esp_copy["hw_events"] = existing
                            payload["esp"] = esp_copy
                        if latched_mute:
                            ms_copy = dict(payload.get("mute_state") or {})
                            ms_copy["newly_triggered"] = True
                            payload["mute_state"] = ms_copy
                        res = self.send_telemetry_once(payload)
                        if res is not None:
                            with self._lock:
                                if latched_hw:
                                    del self._pending_hw_events[:len(latched_hw)]
                                if latched_mute:
                                    self._pending_mute_newly = False
                        last_telem = now_m
                self.drain_spool_once(max_items=3)
            except Exception as exc:
                self.worker_errors += 1
                self.last_error = f"sync_loop_err:{exc}"
            self._wake.wait(timeout=0.5)
            self._wake.clear()
