"""Ultra-Light Bounded Pi Web Server (LCK-04 & LCK-06).

Security & Performance guarantees:
  1. Bounded ThreadPoolExecutor (max 6 workers) + 5s socket timeout (no unbounded thread leak!).
  2. Content-Length cap + Strict RBAC checked BEFORE reading POST body (kills unauth OOM DoS!).
  3. RUN_MODE=server ('minimal_server'): ONLY exposes 3 approved routes:
       - GET /api/v1/health
       - GET /stream (LAN direct, fresh-frame-only <= 15 FPS, single JPEG encode only when client connected)
       - POST /api/esp_bridge (device token required)
  4. RUN_MODE=local ('full_local'): adds /api/status, /api/control, and strictly .jpg-allowlisted /violations/<file>
     (NEVER exposes audit_permissions.jsonl or edge_spool.db to viewer!).
"""
from __future__ import annotations

import ipaddress
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import numpy as np

from pi_edge.core.access_control import AuditLogger, EdgeAccessController, is_allowed_control_command

MAX_POST_BODY_BYTES = 128 * 1024  # 128 KB max for Pi edge JSON commands
MAX_STREAM_CLIENTS = 2  # Never let MJPEG streams starve /api/control or /api/v1/health (6-worker pool)!
ALLOWED_EVIDENCE_EXTS = {".jpg", ".jpeg", ".mp4", ".webm"}
ALLOWED_APP_IOT_EXTS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _allowed_lan_origin(origin: str) -> Optional[str]:
    raw = (origin or "").strip()
    if not raw or "\r" in raw or "\n" in raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = (parsed.hostname or "").lower()
    if not host or host.startswith("."):
        return None
    allowed = False
    if host in ("localhost", "127.0.0.1"):
        allowed = True
    elif (host.endswith(".local") and len(host) > 6) or (
        host.endswith(".trycloudflare.com") and len(host) > 18
    ):
        allowed = True
    else:
        try:
            ip = ipaddress.IPv4Address(host)
            b = ip.packed
            if b[0] == 10 or (b[0] == 192 and b[1] == 168) or (b[0] == 172 and 16 <= b[1] <= 31):
                allowed = True
        except ValueError:
            allowed = False
    if not allowed:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"

_LIVE_VIEWER_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PI_AI — Camera Trực Tiếp (Pi 4 Edge)</title>
<style>
  body{margin:0;background:#0b1220;color:#e2e8f0;font-family:system-ui,-apple-system,sans-serif}
  header{display:flex;justify-content:space-between;align-items:center;padding:12px 18px;background:#111827;border-bottom:1px solid #1f2937;flex-wrap:wrap;gap:8px}
  h1{margin:0;font-size:16px}
  .wrap{max-width:980px;margin:16px auto;padding:0 14px}
  .card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px;margin-bottom:14px}
  .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  input{background:#0b1220;color:#f8fafc;border:1px solid #334155;border-radius:6px;padding:8px 10px;flex:1;min-width:220px}
  button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer}
  button.ghost{background:#1f2937;color:#cbd5e1}
  #cam-img{width:100%;max-height:72vh;object-fit:contain;background:#000;border-radius:8px;border:1px solid #1f2937;display:block}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:10px}
  .kpi{background:#0b1220;border:1px solid #1f2937;border-radius:8px;padding:10px}
  .kpi .lbl{font-size:11px;color:#94a3b8}
  .kpi .val{font-size:15px;font-weight:700;margin-top:3px}
  .ok{color:#22c55e} .warn{color:#f59e0b} .err{color:#ef4444}
</style>
</head>
<body>
<header>
  <h1>PI_AI — Camera Trực Tiếp &amp; Giám Sát Pi 4 (Offline 100%)</h1>
  <div class="row">
    <span id="hdr-status" style="font-size:12px;color:#94a3b8">Đang kiểm tra phiên...</span>
    <button class="ghost" onclick="logout()">Đăng xuất</button>
  </div>
</header>
<div class="wrap">
  <div id="login-card" class="card" style="display:none">
    <p style="margin:0 0 8px;font-size:13px;color:#94a3b8">Nhập <code>WEB_VIEWER_TOKEN</code> hoặc <code>WEB_ADMIN_TOKEN</code> (từ <code>pi.env</code>) để mở luồng Camera trực tiếp:</p>
    <div class="row">
      <input id="tok" type="password" placeholder="Nhập token Pi (WEB_VIEWER_TOKEN / WEB_ADMIN_TOKEN)" onkeydown="if(event.key==='Enter')login()">
      <button onclick="login()">Mở Camera</button>
    </div>
    <p id="login-err" class="err" style="margin:8px 0 0;font-size:12px"></p>
  </div>
  <div id="view-card" class="card" style="display:none">
    <img id="cam-img" alt="PI_AI Live Camera Stream">
    <div class="grid">
      <div class="kpi"><div class="lbl">Trạng thái AI</div><div id="m-ai" class="val">-</div></div>
      <div class="kpi"><div class="lbl">Cân chỉnh 10s</div><div id="m-cal" class="val">-</div></div>
      <div class="kpi"><div class="lbl">Camera / AI FPS</div><div id="m-fps" class="val">-</div></div>
      <div class="kpi"><div class="lbl">Nhiệt Pi / Sụt áp</div><div id="m-temp" class="val">-</div></div>
      <div class="kpi"><div class="lbl">GPS ESP32 #2</div><div id="m-gps" class="val">-</div></div>
      <div class="kpi"><div class="lbl">CO2 (MQ-135)</div><div id="m-co2" class="val">-</div></div>
    </div>
    <div id="m-gps-detail" style="margin-top:8px;font-size:12px;color:#94a3b8"></div>
  </div>
</div>
<script>
let pollTimer = null;
async function checkSession() {
  try {
    const r = await fetch('/api/live_meta', { credentials: 'same-origin' });
    if (r.ok) {
      const d = await r.json();
      showViewer(d);
      return;
    }
  } catch (_) {}
  document.getElementById('login-card').style.display = 'block';
  document.getElementById('view-card').style.display = 'none';
  document.getElementById('hdr-status').textContent = 'Cần đăng nhập token Viewer';
}
async function login() {
  const tok = document.getElementById('tok').value.trim();
  const err = document.getElementById('login-err');
  err.textContent = '';
  if (!tok) { err.textContent = 'Vui lòng nhập token.'; return; }
  const r = await fetch('/api/web_login', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: tok })
  });
  if (!r.ok) { err.textContent = 'Token không hợp lệ (' + r.status + ')'; return; }
  document.getElementById('tok').value = '';
  await checkSession();
}
async function logout() {
  await fetch('/api/web_logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  document.getElementById('cam-img').src = '';
  checkSession();
}
function updateMeta(d) {
  const st = d.state || {};
  const ds = st.driver_state || {};
  const pi = st.pi || {};
  const gps = st.gps || {};
  const mq = st.mq135 || {};
  const alertEl = document.getElementById('m-ai');
  const at = ds.alert_type || 'NORMAL';
  alertEl.textContent = at + (st.camera && st.camera.face_detected ? ' (Có mặt)' : ' (Chưa thấy mặt)');
  alertEl.className = 'val ' + (at === 'NORMAL' ? 'ok' : 'err');
  document.getElementById('m-fps').textContent = (pi.camera_fps ?? 0) + ' / ' + (pi.ai_fps ?? 0);
  const cal = ds.calibration || {};
  const calEl = document.getElementById('m-cal');
  if (calEl) {
    if (cal.state === 'RUNNING') {
      calEl.textContent = 'ĐANG ĐO ' + Number(cal.progress_s || 0).toFixed(1) + 's/' + Number(cal.duration_s || 10).toFixed(0) + 's';
      calEl.className = 'val warn';
    } else if (cal.locked) {
      calEl.textContent = 'ĐÃ KHÓA (EAR<=' + Number(cal.ear_closed_threshold || 0.18).toFixed(2) + ')';
      calEl.className = 'val ok';
    } else if (cal.state === 'DONE') {
      calEl.textContent = 'TẠM (EAR<=' + Number(cal.ear_closed_threshold || 0.18).toFixed(2) + ')';
      calEl.className = 'val ok';
    } else if (cal.state === 'FAILED') {
      calEl.textContent = 'CHƯA THẤY MẶT';
      calEl.className = 'val err';
    } else {
      calEl.textContent = 'CHỜ CAMERA';
      calEl.className = 'val warn';
    }
  }
  const tEl = document.getElementById('m-temp');
  tEl.textContent = (pi.cpu_temp_c ?? '-') + '°C | ' + (pi.throttled || '0x0');
  tEl.className = 'val ' + ((pi.cpu_temp_c || 0) >= 80 ? 'err' : ((pi.cpu_temp_c || 0) >= 70 ? 'warn' : 'ok'));
  const gEl = document.getElementById('m-gps');
  if (gps.is_valid && gps.lat != null && gps.lon != null) {
    gEl.textContent = 'FIX (' + (gps.satellites || 0) + ' sat)';
    gEl.className = 'val ok';
    document.getElementById('m-gps-detail').textContent =
      'Tọa độ thật: ' + Number(gps.lat).toFixed(6) + ', ' + Number(gps.lon).toFixed(6) +
      ' | Tốc độ: ' + Number(gps.speed_kmh || 0).toFixed(1) + ' km/h' +
      (gps.accuracy_m ? (' | Sai số: ±' + gps.accuracy_m + 'm') : '');
  } else {
    gEl.textContent = 'NO FIX (' + (gps.satellites || 0) + ' sat)';
    gEl.className = 'val warn';
    document.getElementById('m-gps-detail').textContent =
      'Chẩn đoán GPS UART2: ' + (gps.wiring_status || 'WAITING_SATELLITE_LOCK') +
      (gps.sats_in_view != null ? (' | Vệ tinh nhìn thấy: ' + gps.sats_in_view) : '') +
      (gps.gps_rx_bytes != null ? (' | UART2 bytes: ' + gps.gps_rx_bytes) : '');
  }
  document.getElementById('m-co2').textContent = Math.round(mq.co2_ppm || 0) + ' ppm';
}
function showViewer(initial) {
  document.getElementById('login-card').style.display = 'none';
  document.getElementById('view-card').style.display = 'block';
  document.getElementById('hdr-status').textContent = 'Đang xem trực tiếp (' + (initial.role || 'viewer') + ')';
  const img = document.getElementById('cam-img');
  if (!img.src || !img.src.includes('/stream')) img.src = '/stream?t=' + Date.now();
  updateMeta(initial);
  if (!pollTimer) {
    pollTimer = setInterval(async () => {
      try {
        const r = await fetch('/api/live_meta', { credentials: 'same-origin' });
        if (r.ok) updateMeta(await r.json());
      } catch (_) {}
    }, 1500);
  }
}
checkSession();
</script>
</body>
</html>
"""


class _BoundedHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, addr, handler_cls, max_workers: int = 6):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pi-http")
        super().__init__(addr, handler_cls)

    def process_request(self, request, client_address):
        request.settimeout(5.0)
        self._pool.submit(self._handle_one, request, client_address)

    def _handle_one(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def server_close(self):
        super().server_close()
        self._pool.shutdown(wait=False, cancel_futures=True)


class MiniWebServer:
    def __init__(
        self,
        host: str,
        port: int,
        web_mode: str,
        access: EdgeAccessController,
        audit: AuditLogger,
        violations_dir: Path,
        on_control_cmd: Optional[Callable[[str], Tuple[bool, str]]] = None,
        on_esp_bridge: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        on_stream_frame_sent: Optional[Callable[[], None]] = None,
        overlay_fn: Optional[Callable[[np.ndarray, Dict[str, Any]], np.ndarray]] = None,
        max_stream_fps: float = 30.0,
        jpeg_quality: int = 82,
        enable_live_stream: bool = True,
        app_iot_dir: Optional[Path] = None,
    ):
        self.host = host
        self.port = int(port)
        self.web_mode = web_mode  # 'minimal_server' | 'full_local'
        self.enable_live_stream = bool(enable_live_stream)
        self.access = access
        self.audit = audit
        self.violations_dir = Path(violations_dir)
        self.app_iot_dir = (
            Path(app_iot_dir) if app_iot_dir is not None else (Path(__file__).resolve().parents[2] / "APP_IOT")
        )
        self.on_control_cmd = on_control_cmd
        self.on_esp_bridge = on_esp_bridge
        self.on_stream_frame_sent = on_stream_frame_sent
        self.overlay_fn = overlay_fn
        self.min_stream_interval_s = 1.0 / max(1.0, float(max_stream_fps))
        self.jpeg_quality = max(50, min(95, int(jpeg_quality)))

        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_frame_id: int = 0
        self._cached_jpeg_id: int = 0
        self._cached_jpeg: bytes = b""
        self._state: Dict[str, Any] = {}
        self._stream_clients = 0
        self._stream_lock = threading.Lock()

        self._httpd: Optional[_BoundedHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def has_stream_clients(self) -> bool:
        with self._stream_lock:
            return self._stream_clients > 0

    def update_frame(self, frame_id: int, frame_bgr: Optional[np.ndarray], state: Dict[str, Any], is_stale: bool = False) -> None:
        with self._cond:
            self._state = dict(state)
            if not self.enable_live_stream:
                return
            # Only bump frame_id and wake /stream when a genuinely FRESH frame arrives!
            if not is_stale and frame_bgr is not None and frame_id != self._latest_frame_id:
                self._latest_frame = frame_bgr
                self._latest_frame_id = int(frame_id)
                self._cond.notify_all()

    def _get_jpeg(self) -> Tuple[int, bytes]:
        with self._cond:
            fid = self._latest_frame_id
            if fid == self._cached_jpeg_id and self._cached_jpeg:
                return fid, self._cached_jpeg
            frame = self._latest_frame
            state = dict(self._state)
        if frame is None:
            return 0, b""
        # Render AI HUD overlay on a copy ONLY when an active browser client is pulling /stream!
        # Clean frames in BurstCollector are NEVER modified (preserves LCK-02).
        render_frame = frame
        if self.overlay_fn is not None:
            try:
                render_frame = self.overlay_fn(frame.copy(), state)
            except Exception:
                render_frame = frame
        ok, buf = cv2.imencode(".jpg", render_frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            return 0, b""
        raw = buf.tobytes()
        with self._cond:
            if fid == self._latest_frame_id:
                self._cached_jpeg_id = fid
                self._cached_jpeg = raw
        return fid, raw

    def start(self) -> int:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _parse_cookies(self) -> Dict[str, str]:
                ck = SimpleCookie()
                raw = self.headers.get("Cookie")
                if raw:
                    try:
                        ck.load(raw)
                    except Exception:
                        pass
                return {k: v.value for k, v in ck.items()}

            def _send_cors_headers(self) -> Optional[str]:
                allowed = _allowed_lan_origin(self.headers.get("Origin", ""))
                if allowed:
                    self.send_header("Access-Control-Allow-Origin", allowed)
                    self.send_header("Vary", "Origin")
                return allowed

            def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self._send_cors_headers()
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                allowed = _allowed_lan_origin(self.headers.get("Origin", ""))
                self.send_response(204)
                if allowed:
                    self.send_header("Access-Control-Allow-Origin", allowed)
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Authorization, X-Auth-Token, Content-Type")
                    self.send_header("Vary", "Origin")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def _auth(self, min_role: str, query: Dict[str, str], required_audit: bool = False, allow_query_token: bool = False):
                clean_path = urlparse(self.path).path
                dec = parent.access.authorize(
                    min_role=min_role,
                    headers=self.headers,
                    query_params=(query if allow_query_token else {}),
                    cookies=self._parse_cookies(),
                )
                if not dec.allowed:
                    code = 401 if dec.reason in ("MISSING_TOKEN", "INVALID_TOKEN") else 403
                    parent.audit.record(
                        actor_role=dec.role,
                        method=self.command,
                        path=clean_path,
                        allowed=False,
                        reason=dec.reason,
                        remote_ip=self.client_address[0],
                    )
                    self._send_json(code, {"ok": False, "error": dec.reason})
                    return None
                if required_audit:
                    wrote = parent.audit.record(
                        actor_role=dec.role,
                        method=self.command,
                        path=clean_path,
                        allowed=True,
                        reason="OK",
                        remote_ip=self.client_address[0],
                        required=True,
                    )
                    if not wrote:
                        self._send_json(503, {"ok": False, "error": "AUDIT_LOG_UNAVAILABLE"})
                        return None
                return dec

            def _read_bounded_json(self) -> Optional[Dict[str, Any]]:
                raw_cl = self.headers.get("Content-Length", "0")
                try:
                    n = int(raw_cl)
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "INVALID_CONTENT_LENGTH"})
                    return None
                if n < 0 or n > MAX_POST_BODY_BYTES:
                    self._send_json(413, {"ok": False, "error": f"BODY_TOO_LARGE_MAX_{MAX_POST_BODY_BYTES}"})
                    return None
                raw = self.rfile.read(n) if n > 0 else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("not dict")
                    return data
                except Exception:
                    self._send_json(400, {"ok": False, "error": "INVALID_JSON"})
                    return None

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                q = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

                if path in ("/api/v1/health", "/health"):
                    with parent._stream_lock:
                        stream_clients = parent._stream_clients
                    self._send_json(
                        200,
                        {
                            "ok": not parent.audit.audit_degraded,
                            "target": "pi_local",
                            "app_iot_available": bool(
                                parent.app_iot_dir and (parent.app_iot_dir / "driver.html").is_file()
                            ),
                            "web_mode": parent.web_mode,
                            "enable_live_stream": parent.enable_live_stream,
                            "stream_clients": stream_clients,
                            "audit_degraded": parent.audit.audit_degraded,
                            "ts": time.time(),
                        },
                    )
                    return

                app_enabled = parent.enable_live_stream or parent.web_mode == "full_local"

                if path == "/driver":
                    if not app_enabled:
                        self._send_json(404, {"ok": False, "error": "ROUTE_DISABLED_IN_SERVER_MODE"})
                        return
                    if not (parent.app_iot_dir and (parent.app_iot_dir / "driver.html").is_file()):
                        self._send_json(404, {"ok": False, "error": "APP_IOT_NOT_FOUND"})
                        return
                    self.send_response(307)
                    self.send_header("Location", "/app_iot/driver.html")
                    self.send_header("Content-Length", "0")
                    self._send_cors_headers()
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return

                if path == "/app_iot" or path.startswith("/app_iot/"):
                    if not app_enabled:
                        self._send_json(404, {"ok": False, "error": "ROUTE_DISABLED_IN_SERVER_MODE"})
                        return
                    raw_rel = path[len("/app_iot/"):] if path.startswith("/app_iot/") else ""
                    rel_unquoted = unquote(raw_rel)
                    if "\\" in rel_unquoted or "\x00" in rel_unquoted:
                        self._send_json(403, {"ok": False, "error": "PATH_TRAVERSAL_FORBIDDEN"})
                        return
                    rel_raw = rel_unquoted.lstrip("/")
                    if not rel_raw or ".." in rel_unquoted.split("/") or ".." in Path(rel_raw).parts:
                        self._send_json(403, {"ok": False, "error": "PATH_TRAVERSAL_FORBIDDEN"})
                        return
                    base_resolved = parent.app_iot_dir.resolve()
                    target = (base_resolved / rel_raw).resolve()
                    if not target.is_relative_to(base_resolved):
                        self._send_json(403, {"ok": False, "error": "PATH_TRAVERSAL_FORBIDDEN"})
                        return
                    ctype = ALLOWED_APP_IOT_EXTS.get(target.suffix.lower())
                    if not ctype:
                        self._send_json(403, {"ok": False, "error": "FILE_TYPE_FORBIDDEN"})
                        return
                    if not target.is_file():
                        self._send_json(404, {"ok": False, "error": "NOT_FOUND"})
                        return
                    data_bytes = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data_bytes)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cache-Control", "no-cache")
                    self._send_cors_headers()
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data_bytes)
                    return

                if path == "/api/app_status":
                    if not app_enabled:
                        self._send_json(404, {"ok": False, "error": "ROUTE_DISABLED_IN_SERVER_MODE"})
                        return
                    dec = self._auth("viewer", q)
                    if dec is None:
                        return
                    with parent._cond:
                        st = dict(parent._state)
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "source": "pi_local",
                            "role": dec.role,
                            "web_mode": parent.web_mode,
                            "enable_live_stream": parent.enable_live_stream,
                            "state": st,
                        },
                    )
                    return

                if path in ("/", "/preview"):
                    if not parent.enable_live_stream:
                        self._send_json(404, {"ok": False, "error": "LIVE_STREAM_DISABLED"})
                        return
                    body = _LIVE_VIEWER_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Frame-Options", "SAMEORIGIN")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if path == "/api/live_meta":
                    if not parent.enable_live_stream:
                        self._send_json(404, {"ok": False, "error": "LIVE_STREAM_DISABLED"})
                        return
                    dec = self._auth("viewer", q)
                    if dec is None:
                        return
                    with parent._cond:
                        st = dict(parent._state)
                    self._send_json(200, {"ok": True, "role": dec.role, "state": st})
                    return

                if path == "/stream":
                    if not parent.enable_live_stream:
                        self._send_json(404, {"ok": False, "error": "LIVE_STREAM_DISABLED"})
                        return
                    if self._auth("viewer", q, allow_query_token=True) is None:
                        return
                    with parent._stream_lock:
                        if parent._stream_clients >= MAX_STREAM_CLIENTS:
                            self._send_json(503, {"ok": False, "error": "STREAM_BUSY_MAX_CLIENTS"})
                            return
                        parent._stream_clients += 1
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        last_sent_id = 0
                        last_sent_mono = 0.0
                        while not parent._stop.is_set():
                            with parent._cond:
                                if parent._latest_frame_id == last_sent_id:
                                    parent._cond.wait(timeout=1.0)
                                fid = parent._latest_frame_id
                            if fid == last_sent_id:
                                continue
                            now_m = time.monotonic()
                            wait_s = parent.min_stream_interval_s - (now_m - last_sent_mono)
                            if wait_s > 0:
                                time.sleep(wait_s)
                            sent_id, jpg = parent._get_jpeg()
                            if not jpg:
                                continue
                            hdr = (
                                b"--frame\r\nContent-Type: image/jpeg\r\n"
                                + f"Content-Length: {len(jpg)}\r\n\r\n".encode("ascii")
                            )
                            self.wfile.write(hdr + jpg + b"\r\n")
                            self.wfile.flush()
                            last_sent_id = sent_id
                            last_sent_mono = time.monotonic()
                            if parent.on_stream_frame_sent:
                                parent.on_stream_frame_sent()
                    except Exception:
                        pass
                    finally:
                        with parent._stream_lock:
                            parent._stream_clients = max(0, parent._stream_clients - 1)
                    return

                # Routes below are STRICTLY disabled in minimal_server mode (LCK-04)!
                if parent.web_mode != "full_local":
                    self._send_json(404, {"ok": False, "error": "ROUTE_DISABLED_IN_SERVER_MODE"})
                    return

                if path == "/api/status":
                    if self._auth("viewer", q) is None:
                        return
                    with parent._cond:
                        st = dict(parent._state)
                    self._send_json(200, {"ok": True, "state": st})
                    return

                if path.startswith("/violations/"):
                    if self._auth("viewer", q) is None:
                        return
                    rel_raw = path[len("/violations/"):].lstrip("/")
                    if ".." in Path(rel_raw).parts or not rel_raw:
                        self._send_json(403, {"ok": False, "error": "FILE_TYPE_FORBIDDEN"})
                        return
                    if Path(rel_raw).suffix.lower() not in ALLOWED_EVIDENCE_EXTS:
                        # Blocks .jsonl (audit/events) and .db (SQLite spool)!
                        self._send_json(403, {"ok": False, "error": "FILE_TYPE_FORBIDDEN"})
                        return
                    root = parent.violations_dir.resolve()
                    target = (root / rel_raw).resolve()
                    if root not in target.parents or not target.is_file():
                        self._send_json(404, {"ok": False, "error": "NOT_FOUND"})
                        return
                    file_size = target.stat().st_size
                    suf = target.suffix.lower()
                    ctype = "video/webm" if suf == ".webm" else ("video/mp4" if suf == ".mp4" else "image/jpeg")
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(file_size))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    with target.open("rb") as fp:
                        while True:
                            chunk = fp.read(65536)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    return

                self._send_json(404, {"ok": False, "error": "NOT_FOUND"})

            def do_POST(self):
                parsed = urlparse(self.path)
                path = parsed.path
                q = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

                # Check Content-Length cap BEFORE auth & body read (blocks OOM DoS!)
                raw_cl = self.headers.get("Content-Length", "0")
                try:
                    if int(raw_cl) > MAX_POST_BODY_BYTES or int(raw_cl) < 0:
                        self._send_json(413, {"ok": False, "error": "PAYLOAD_TOO_LARGE"})
                        return
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "INVALID_CONTENT_LENGTH"})
                    return

                if path == "/api/web_login":
                    if not (parent.enable_live_stream or parent.web_mode == "full_local"):
                        self._send_json(404, {"ok": False, "error": "LIVE_STREAM_DISABLED"})
                        return
                    data = self._read_bounded_json()
                    if data is None:
                        return
                    tok = str(data.get("token") or "").strip()
                    dec = parent.access.authorize(min_role="viewer", headers={"X-Auth-Token": tok})
                    if not dec.allowed:
                        parent.audit.record(
                            actor_role=dec.role,
                            method="POST",
                            path="/api/web_login",
                            allowed=False,
                            reason=dec.reason,
                            remote_ip=self.client_address[0],
                        )
                        self._send_json(401, {"ok": False, "error": dec.reason})
                        return
                    parent.audit.record(
                        actor_role=dec.role,
                        method="POST",
                        path="/api/web_login",
                        allowed=True,
                        reason="OK",
                        remote_ip=self.client_address[0],
                    )
                    body = json.dumps({"ok": True, "role": dec.role}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Set-Cookie", f"web_token={tok}; HttpOnly; SameSite=Lax; Path=/; Max-Age=43200")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self._send_cors_headers()
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if path == "/api/web_logout":
                    body = b'{"ok":true}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Set-Cookie", "web_token=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
                    self._send_cors_headers()
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if path == "/api/esp_bridge":
                    if self._auth("device", q) is None:
                        return
                    data = self._read_bounded_json()
                    if data is None:
                        return
                    res = parent.on_esp_bridge(data) if parent.on_esp_bridge else {"ok": True}
                    self._send_json(200, res)
                    return

                if path == "/api/control":
                    if not (parent.web_mode == "full_local" or parent.enable_live_stream):
                        self._send_json(404, {"ok": False, "error": "ROUTE_DISABLED_IN_SERVER_MODE"})
                        return
                    dec = self._auth("operator", q, required_audit=True)
                    if dec is None:
                        return
                    data = self._read_bounded_json()
                    if data is None:
                        return
                    cmd = str(data.get("cmd") or "").strip()
                    if not is_allowed_control_command(cmd):
                        self._send_json(400, {"ok": False, "error": "COMMAND_NOT_WHITELISTED"})
                        return
                    ok, detail = parent.on_control_cmd(cmd) if parent.on_control_cmd else (True, "NO_ESP")
                    self._send_json(200, {"ok": ok, "cmd": cmd, "detail": detail})
                    return

                if parent.web_mode != "full_local":
                    self._send_json(404, {"ok": False, "error": "ROUTE_DISABLED_IN_SERVER_MODE"})
                    return

                self._send_json(404, {"ok": False, "error": "NOT_FOUND"})

        self._httpd = _BoundedHTTPServer((self.host, self.port), Handler, max_workers=6)
        actual_port = int(self._httpd.server_address[1])
        self.port = actual_port
        self._stop.clear()
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="pi-mini-web", daemon=True)
        self._thread.start()
        return actual_port

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
