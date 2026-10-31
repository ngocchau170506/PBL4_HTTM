"""High-performance embedded Web Dashboard for Driver Monitor.

Serves:
- Real-time MJPEG camera stream with AI annotations
- Live telemetry (Driver alert state, eye/mouth durations, AI FPS, CPU/RAM/Temp, ESP32 CO2/GPS)
- Interactive manual sound & alert triggers (VOICE:1..5, STOP, PING, MUTE, ALERT)
- Real-time parameter tuning (drowsy threshold, yawn threshold, cooldown, EAR/MAR)
- Works 100% offline (no CDN or external dependencies required)
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Optional
import cv2

try:
    import psutil
except ImportError:
    psutil = None


def get_system_metrics() -> Dict[str, Any]:
    """Read CPU, RAM, Temperature and Throttling on Raspberry Pi / Windows."""
    metrics = {
        "cpu_percent": 0.0,
        "ram_mb": 0.0,
        "temp_c": None,
        "throttled": "0x0",
    }
    if psutil:
        try:
            p = psutil.Process()
            metrics["cpu_percent"] = p.cpu_percent()
            metrics["ram_mb"] = round(p.memory_info().rss / (1024 * 1024), 1)
        except Exception:
            pass

    # Read Pi SoC temperature
    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if temp_path.exists():
        try:
            val = float(temp_path.read_text().strip()) / 1000.0
            metrics["temp_c"] = round(val, 1)
        except Exception:
            pass

    # Read Pi throttling & under-voltage flags
    try:
        import subprocess
        res = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, timeout=1.0).strip()
        if "=" in res:
            metrics["throttled"] = res.split("=")[-1]
    except Exception:
        pass
    return metrics


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Driver Monitor - Trung Tâm Điều Khiển & Giám Sát</title>
<style>
  :root {
    --bg: #0b0f19;
    --card: #151d2f;
    --border: #23314d;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --green: #22c55e;
    --amber: #f59e0b;
    --red: #ef4444;
    --purple: #a855f7;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 16px;
    font-size: 14px;
    line-height: 1.5;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 16px;
    flex-wrap: wrap;
    gap: 10px;
  }
  .title-group { display: flex; align-items: center; gap: 12px; }
  .pulse-dot {
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 10px var(--green);
    animation: pulse 1.8s infinite;
  }
  @keyframes pulse {
    0% { transform: scale(0.95); opacity: 0.8; }
    50% { transform: scale(1.15); opacity: 1; }
    100% { transform: scale(0.95); opacity: 0.8; }
  }
  h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
  .badge {
    padding: 3px 8px; border-radius: 6px; font-size: 11px;
    font-weight: 600; text-transform: uppercase;
  }
  .badge-online { background: rgba(34,197,94,0.15); color: var(--green); border: 1px solid var(--green); }
  .badge-offline { background: rgba(239,68,68,0.15); color: var(--red); border: 1px solid var(--red); }
  
  .grid-main {
    display: grid;
    grid-template-columns: 1.25fr 1fr;
    gap: 16px;
  }
  @media (max-width: 1024px) {
    .grid-main { grid-template-columns: 1fr; }
  }

  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    margin-bottom: 16px;
  }
  .card-title {
    font-size: 13px; font-weight: 700; text-transform: uppercase;
    color: var(--accent); margin-bottom: 12px; display: flex;
    justify-content: space-between; align-items: center;
  }

  /* Camera Container */
  .cam-wrap {
    position: relative;
    background: #000;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--border);
    min-height: 380px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .cam-wrap img {
    width: 100%; height: auto; max-height: 520px; object-fit: contain; display: block;
  }
  .cam-overlay {
    position: absolute; top: 10px; left: 10px; right: 10px;
    display: flex; justify-content: space-between; pointer-events: none;
  }
  .alert-banner {
    padding: 6px 14px; border-radius: 6px; font-weight: 700; font-size: 13px;
    letter-spacing: 0.5px;
  }
  .banner-NORMAL { background: rgba(34,197,94,0.85); color: #fff; }
  .banner-DROWSY { background: rgba(239,68,68,0.9); color: #fff; animation: blink 0.8s infinite; }
  .banner-YAWNING { background: rgba(245,158,11,0.9); color: #fff; }
  .banner-DISTRACTED { background: rgba(168,85,247,0.9); color: #fff; animation: blink 1.0s infinite; }
  .banner-TRACKING_LOST { background: rgba(100,116,139,0.9); color: #fff; }
  .banner-SUSPECTED { background: rgba(245,158,11,0.9); color: #fff; }
  @keyframes blink { 50% { opacity: 0.4; } }

  /* Metrics Grid */
  .metrics-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px;
  }
  .metric-box {
    background: rgba(11,15,25,0.6);
    border: 1px solid rgba(35,49,77,0.7);
    border-radius: 8px;
    padding: 10px;
  }
  .metric-label { font-size: 11px; color: var(--muted); text-transform: uppercase; }
  .metric-value { font-size: 18px; font-weight: 700; margin-top: 4px; color: #fff; }

  /* Progress Bar */
  .bar-wrap { margin-top: 8px; }
  .bar-head { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; }
  .progress { height: 7px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden; }
  .progress-fill { height: 100%; width: 0%; transition: width 0.2s ease, background-color 0.2s; }
  .fill-green { background: var(--green); }
  .fill-amber { background: var(--amber); }
  .fill-red { background: var(--red); }
  .fill-purple { background: var(--purple); }

  /* Buttons & Controls */
  .btn-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px;
  }
  button {
    background: #1e293b; color: #fff; border: 1px solid var(--border);
    padding: 9px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;
    cursor: pointer; transition: all 0.15s ease;
  }
  button:hover { background: #334155; border-color: var(--accent); }
  button:active { transform: scale(0.97); }
  button.btn-danger { background: rgba(239,68,68,0.2); border-color: var(--red); color: #fca5a5; }
  button.btn-danger:hover { background: var(--red); color: #fff; }
  button.btn-amber { background: rgba(245,158,11,0.2); border-color: var(--amber); color: #fcd34d; }
  button.btn-amber:hover { background: var(--amber); color: #000; }
  button.btn-blue { background: rgba(56,189,248,0.2); border-color: var(--accent); color: #bae6fd; }
  button.btn-blue:hover { background: var(--accent); color: #000; }

  /* Tuning Form */
  .form-row {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 10px;
  }
  .form-group label {
    display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px;
  }
  .form-group input {
    width: 100%; background: #0b0f19; border: 1px solid var(--border);
    color: #fff; padding: 7px 10px; border-radius: 6px; font-size: 13px;
  }
  .form-group input:focus { outline: none; border-color: var(--accent); }

  /* Event Log Table */
  .log-table {
    width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 6px;
  }
  .log-table th, .log-table td {
    padding: 6px 8px; text-align: left; border-bottom: 1px solid rgba(35,49,77,0.5);
  }
  .log-table th { color: var(--muted); font-weight: 600; }

  /* Toast Notification */
  #toast {
    position: fixed; bottom: 20px; right: 20px; background: #1e293b;
    border: 1px solid var(--accent); color: #fff; padding: 10px 16px;
    border-radius: 8px; font-size: 13px; z-index: 999; display: none;
    box-shadow: 0 4px 15px rgba(0,0,0,0.5);
  }
</style>
</head>
<body>

<header>
  <div class="title-group">
    <div class="pulse-dot"></div>
    <h1>AI Driver Monitoring System</h1>
    <span id="conn-badge" class="badge badge-online">ĐANG KẾT NỐI</span>
  </div>
  <div>
    <span style="color:var(--muted); font-size:12px;">Thiết bị: Raspberry Pi 4 | Cổng Web: <b id="lbl-port">8080</b></span>
  </div>
</header>

<div class="grid-main">
  <!-- CỘT TRÁI: CAMERA STREAM & TRẠNG THÁI TÀI XẾ -->
  <div>
    <div class="card" style="padding:10px;">
      <div class="card-title">
        <span>Camera Giám Sát Thời Gian Thực (RTSP LAN)</span>
        <span id="fps-badge" style="color:var(--accent);">0.0 FPS</span>
      </div>
      <div class="cam-wrap">
        <div class="cam-overlay">
          <div id="banner-state" class="alert-banner banner-NORMAL">TRẠNG THÁI: NORMAL</div>
          <div id="det-classes" style="background:rgba(0,0,0,0.6); padding:4px 8px; border-radius:4px; font-size:11px;">Classes: []</div>
        </div>
        <img id="cam-img" src="/stream" alt="Video stream..." />
      </div>
    </div>

    <!-- TIẾN ĐỘ THỜI GIAN NHẮM / NGÁP -->
    <div class="card">
      <div class="card-title">Bộ Đếm Tích Lũy Buồn Ngủ, Ngáp & Gục Đầu</div>
      <div class="bar-wrap">
        <div class="bar-head">
          <span>Thời Gian Nhắm Mắt (Eye Closed)</span>
          <b id="lbl-eye-time">0.0s / 2.0s</b>
        </div>
        <div class="progress">
          <div id="bar-eye" class="progress-fill fill-green"></div>
        </div>
      </div>
      <div class="bar-wrap" style="margin-top:14px;">
        <div class="bar-head">
          <span>Thời Gian Ngáp (Yawning Duration)</span>
          <b id="lbl-mouth-time">0.0s / 1.2s</b>
        </div>
        <div class="progress">
          <div id="bar-mouth" class="progress-fill fill-amber"></div>
        </div>
      </div>
      <div class="bar-wrap" style="margin-top:14px;">
        <div class="bar-head">
          <span>Gục Đầu / Mất Tập Trung (Head Distraction)</span>
          <b id="lbl-distract-time">0.0s / 1.5s</b>
        </div>
        <div class="progress">
          <div id="bar-distract" class="progress-fill fill-purple"></div>
        </div>
      </div>
    </div>

    <!-- NHẬT KÝ SỰ KIỆN GẦN NHẤT -->
    <div class="card">
      <div class="card-title">Nhật Ký Cảnh Báo Gần Nhất (Live Event Log)</div>
      <table class="log-table">
        <thead>
          <tr>
            <th>Thời gian</th>
            <th>Loại cảnh báo</th>
            <th>Nhãn nhận diện</th>
            <th>Lệnh gửi ESP</th>
          </tr>
        </thead>
        <tbody id="log-tbody">
          <tr><td colspan="4" style="color:var(--muted); text-align:center;">Đang đợi sự kiện...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- CỘT PHẢI: CHỈ SỐ, PHẦN CỨNG, BẢNG ĐIỀU KHIỂN & ĐIỀU CHỈNH -->
  <div>
    <!-- CHỈ SỐ AI & PHẦN CỨNG -->
    <div class="card">
      <div class="card-title">Chỉ Số AI & Tài Nguyên Raspberry Pi 4</div>
      <div class="metrics-grid">
        <div class="metric-box">
          <div class="metric-label">AI FPS</div>
          <div id="val-fps" class="metric-value">0.0</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Độ Trễ YOLO</div>
          <div id="val-yolo" class="metric-value">-- ms</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">CPU Pi 4</div>
          <div id="val-cpu" class="metric-value">-- %</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">RAM Tiến Trình</div>
          <div id="val-ram" class="metric-value">-- MB</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Nhiệt Độ SoC</div>
          <div id="val-temp" class="metric-value">-- °C</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">ESP32 UART</div>
          <div id="val-esp-conn" class="metric-value" style="font-size:14px; color:var(--green);">OK</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Góc Đầu (P/Y/R)</div>
          <div id="val-pose" class="metric-value" style="font-size:12px; color:var(--accent);">-- / -- / --</div>
        </div>
      </div>
    </div>

    <!-- DỮ LIỆU CẢM BIẾN ESP32 (MQ-135, CO2, GPS) -->
    <div class="card">
      <div class="card-title">Cảm Biến Khí CO2 & Môi Trường Xe (ESP32)</div>
      <div class="metrics-grid">
        <div class="metric-box">
          <div class="metric-label">Nồng Độ Khí CO2</div>
          <div id="val-co2" class="metric-value">-- ppm</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Cấp Độ Khí CO2</div>
          <div id="val-co2-lv" class="metric-value">0 (Bình thường)</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Tốc Độ GPS</div>
          <div id="val-speed" class="metric-value">0.0 km/h</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">ESP Reset Count</div>
          <div id="val-reset" class="metric-value">0</div>
        </div>
      </div>
      <div style="font-size:11px; color:var(--muted); margin-top:8px; font-family:monospace;" id="raw-telemetry">
        Bản tin thô: Đang chờ...
      </div>
    </div>

    <!-- BẢNG ĐIỀU KHIỂN LOA & TEST LỆNH THỦ CÔNG -->
    <div class="card">
      <div class="card-title">Thử Nghiệm Âm Thanh & Điều Khiển Loa ESP32</div>
      <div class="btn-grid">
        <button class="btn-danger" onclick="sendCmd('VOICE:1')">🔊 Buồn Ngủ (VOICE:1)</button>
        <button class="btn-amber" onclick="sendCmd('VOICE:2')">🥱 Ngáp Dài (VOICE:2)</button>
        <button class="btn-blue" onclick="sendCmd('VOICE:3')">👀 Mất Tập Trung (VOICE:3)</button>
        <button class="btn-amber" onclick="sendCmd('VOICE:4')">⚠️ CO2 Bí Khí (VOICE:4)</button>
        <button class="btn-danger" onclick="sendCmd('VOICE:5')">🚨 CO2 Nguy Cấp (VOICE:5)</button>
        <button onclick="sendCmd('ALERT:1:300')">🔔 Bíp Nhẹ (300ms)</button>
        <button onclick="sendCmd('ALERT:2:500')">🔔 Bíp To (500ms)</button>
        <button onclick="sendCmd('PING')">📡 PING ESP32</button>
        <button onclick="sendCmd('MUTE')">🔇 Tắt Tiếng 30s</button>
        <button class="btn-danger" style="grid-column: span 2;" onclick="sendCmd('STOP')">⏹ DỪNG PHÁT NGAY (STOP)</button>
      </div>
    </div>

    <!-- BẢNG ĐIỀU CHỈNH THÔNG SỐ TRỰC TIẾP (LIVE TUNING) -->
    <div class="card">
      <div class="card-title">Cấu Hình Ngưỡng Nhận Diện Trực Tiếp (Live Tuning)</div>
      <form id="cfg-form" onsubmit="saveConfig(event)">
        <div class="form-row">
          <div class="form-group">
            <label>Ngưỡng Buồn Ngủ (giây nhắm mắt liên tục):</label>
            <input type="number" step="0.1" id="cfg-drowsy" value="2.0" required>
          </div>
          <div class="form-group">
            <label>Ngưỡng Ngáp (giây ngáp mở miệng):</label>
            <input type="number" step="0.1" id="cfg-yawn" value="1.2" required>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Giãn Cách Giọng Nói Cooldown (giây):</label>
            <input type="number" step="0.5" id="cfg-cooldown" value="5.0" required>
          </div>
          <div class="form-group">
            <label>Ngưỡng Mắt EAR (MediaPipe Baseline):</label>
            <input type="number" step="0.01" id="cfg-ear" value="0.24" required>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Ngưỡng Gục Đầu (giây duy trì):</label>
            <input type="number" step="0.1" id="cfg-distract" value="1.5" required>
          </div>
          <div class="form-group">
            <label>Ngưỡng Góc Pitch Gục Đầu (°):</label>
            <input type="number" step="1.0" id="cfg-pitch" value="22.0" required>
          </div>
        </div>
        <button type="submit" class="btn-blue" style="width:100%; padding:10px;">💾 Lưu & Áp Dụng Ngay Trên Pi (Không Cần Khởi Động Lại)</button>
      </form>
    </div>
  </div>
</div>

<div id="toast">Thông báo</div>

<script>
  let lastEvents = [];
  function showToast(msg) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; }, 2500);
  }

  async function sendCmd(cmd) {
    try {
      const res = await fetch('/api/control', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({cmd: cmd})
      });
      const data = await res.json();
      if (data.ok) {
        showToast('Đã gửi [' + cmd + '] -> ACK: ' + (data.ack || 'Thành công'));
      } else {
        showToast('Lỗi gửi [' + cmd + ']: ' + data.error);
      }
    } catch (e) {
      showToast('Lỗi kết nối API: ' + e);
    }
  }

  async function saveConfig(e) {
    e.preventDefault();
    const payload = {
      drowsy_threshold: parseFloat(document.getElementById('cfg-drowsy').value),
      yawn_threshold: parseFloat(document.getElementById('cfg-yawn').value),
      voice_cooldown_s: parseFloat(document.getElementById('cfg-cooldown').value),
      ear_threshold: parseFloat(document.getElementById('cfg-ear').value),
      distraction_threshold: parseFloat(document.getElementById('cfg-distract').value),
      head_pitch_threshold: parseFloat(document.getElementById('cfg-pitch').value)
    };
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.ok) {
        showToast('Đã cập nhật ngưỡng nhận diện thành công!');
      } else {
        showToast('Lỗi lưu cấu hình: ' + data.error);
      }
    } catch (err) {
      showToast('Lỗi gửi cấu hình: ' + err);
    }
  }

  // Định kỳ 800ms cập nhật trạng thái thời gian thực
  async function fetchStatus() {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      document.getElementById('conn-badge').className = 'badge badge-online';
      document.getElementById('conn-badge').innerText = 'ĐANG KẾT NỐI';

      // Cập nhật banner & trạng thái
      const alert = data.alert || 'NORMAL';
      const banner = document.getElementById('banner-state');
      banner.innerText = 'TRẠNG THÁI: ' + alert;
      banner.className = 'alert-banner banner-' + (alert.includes('DROWSY') ? 'DROWSY' : alert.includes('YAWN') ? 'YAWNING' : alert.includes('DISTRACT') ? 'DISTRACTED' : alert);

      document.getElementById('det-classes').innerText = 'Classes: ' + JSON.stringify(data.detected_classes || []);
      document.getElementById('fps-badge').innerText = (data.ai_fps || 0).toFixed(1) + ' FPS';

      // Cập nhật metrics
      document.getElementById('val-fps').innerText = (data.ai_fps || 0).toFixed(1);
      document.getElementById('val-yolo').innerText = (data.yolo_latency_ms || 0).toFixed(1) + ' ms';
      document.getElementById('val-cpu').innerText = (data.hardware?.cpu_percent || 0).toFixed(1) + ' %';
      document.getElementById('val-ram').innerText = (data.hardware?.ram_mb || 0) + ' MB';
      document.getElementById('val-temp').innerText = (data.hardware?.temp_c ? data.hardware.temp_c + ' °C' : '--');

      // ESP
      const espConn = data.esp?.connected;
      const espElem = document.getElementById('val-esp-conn');
      espElem.innerText = espConn ? 'OK (' + (data.esp.port || 'USB') + ')' : 'NGẮT KẾT NỐI';
      espElem.style.color = espConn ? 'var(--green)' : 'var(--red)';
      document.getElementById('val-reset').innerText = data.esp?.reset_count || 0;
      document.getElementById('raw-telemetry').innerText = 'Bản tin thô: ' + (data.esp?.latest_telemetry || 'Chưa có');

      if (data.esp?.co2_ppm !== undefined && data.esp.co2_ppm !== null) {
        document.getElementById('val-co2').innerText = Math.round(data.esp.co2_ppm) + ' ppm';
      }
      if (data.esp?.co2_level !== undefined) {
        const lv = data.esp.co2_level;
        document.getElementById('val-co2-lv').innerText = lv === 2 ? '2 (Nguy cấp)' : lv === 1 ? '1 (Bí khí)' : '0 (An toàn)';
      }
      if (data.esp?.speed_kmh !== undefined) {
        document.getElementById('val-speed').innerText = Number(data.esp.speed_kmh).toFixed(1) + ' km/h';
      }

      // Thanh tiến trình
      const eyeDur = data.eye_duration || 0;
      const eyeThresh = data.config?.drowsy_threshold || 2.0;
      const eyePct = Math.min(100, (eyeDur / eyeThresh) * 100);
      document.getElementById('lbl-eye-time').innerText = eyeDur.toFixed(1) + 's / ' + eyeThresh.toFixed(1) + 's';
      const barEye = document.getElementById('bar-eye');
      barEye.style.width = eyePct + '%';
      barEye.className = 'progress-fill ' + (eyePct >= 100 ? 'fill-red' : eyePct > 50 ? 'fill-amber' : 'fill-green');

      const mouthDur = data.mouth_duration || 0;
      const mouthThresh = data.config?.yawn_threshold || 1.2;
      const mouthPct = Math.min(100, (mouthDur / mouthThresh) * 100);
      document.getElementById('lbl-mouth-time').innerText = mouthDur.toFixed(1) + 's / ' + mouthThresh.toFixed(1) + 's';
      const barMouth = document.getElementById('bar-mouth');
      barMouth.style.width = mouthPct + '%';
      barMouth.className = 'progress-fill ' + (mouthPct >= 100 ? 'fill-red' : mouthPct > 50 ? 'fill-amber' : 'fill-green');

      // Gục đầu / mất tập trung
      const distDur = data.distracted_duration || 0;
      const distThresh = data.config?.distraction_threshold || 1.5;
      const distPct = Math.min(100, (distDur / distThresh) * 100);
      document.getElementById('lbl-distract-time').innerText = distDur.toFixed(1) + 's / ' + distThresh.toFixed(1) + 's';
      const barDist = document.getElementById('bar-distract');
      barDist.style.width = distPct + '%';

      const hp = data.head_pose;
      if (hp && hp.pitch !== undefined) {
        document.getElementById('val-pose').innerText = `${hp.pitch}° / ${hp.yaw}° / ${hp.roll}°`;
      } else {
        document.getElementById('val-pose').innerText = '-- / -- / --';
      }

      // Bảng sự kiện
      if (data.events && Array.isArray(data.events) && data.events.length > 0) {
        const tbody = document.getElementById('log-tbody');
        tbody.innerHTML = '';
        data.events.slice(-6).reverse().forEach(evt => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td>${evt.time || '--'}</td>
            <td><b>${evt.alert}</b></td>
            <td>${JSON.stringify(evt.classes || [])}</td>
            <td><code>${evt.cmd || '--'}</code></td>
          `;
          tbody.appendChild(tr);
        });
      }
    } catch (e) {
      document.getElementById('conn-badge').className = 'badge badge-offline';
      document.getElementById('conn-badge').innerText = 'MẤT KẾT NỐI';
    }
  }

  setInterval(fetchStatus, 800);
  fetchStatus();
</script>
</body>
</html>
"""


class DriverMonitorWebServer:
    """Multi-threaded web server serving live video and bi-directional REST APIs."""

    def __init__(self, port: int = 8080):
        self.port = int(port)
        self._frame = None
        self._state: Dict[str, Any] = {}
        self._events = []
        self._condition = threading.Condition()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.pipeline = None
        self.esp = None
        self.camera_source = None

        if self.port > 0:
            self._start()

    def set_context(self, pipeline=None, esp=None, camera_source=None):
        if pipeline is not None:
            self.pipeline = pipeline
        if esp is not None:
            self.esp = esp
        if camera_source is not None:
            self.camera_source = camera_source

    def update_frame(self, frame, state=None):
        with self._condition:
            self._frame = frame
            if state:
                self._state = state
                alert = state.get("confirmed_alert", "NORMAL")
                if alert not in ("NORMAL", "UNKNOWN") and (
                    not self._events or self._events[-1]["alert"] != alert
                ):
                    self._events.append(
                        {
                            "time": time.strftime("%H:%M:%S"),
                            "alert": alert,
                            "classes": state.get("detected_classes", []),
                            "cmd": f"VOICE:{'1' if 'DROWSY' in alert else '2' if 'YAWN' in alert else '3' if 'DISTRACT' in alert else ''}",
                        }
                    )
                    if len(self._events) > 30:
                        self._events.pop(0)
            self._condition.notify_all()

    def _start(self):
        parent = self

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Tắt log spam HTTP request

            def do_GET(self):
                if self.path == "/" or self.path == "/index.html":
                    html_data = DASHBOARD_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html_data)))
                    self.end_headers()
                    self.wfile.write(html_data)

                elif self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Cache-Control", "no-cache, private")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while True:
                            with parent._condition:
                                parent._condition.wait(timeout=0.5)
                                frame = parent._frame
                                state = parent._state
                            if frame is None:
                                continue
                            vis = frame.copy()
                            alert = state.get("confirmed_alert", "NORMAL")
                            eye_dur = state.get("eye_duration", 0.0)
                            mouth_dur = state.get("mouth_duration", 0.0)
                            fps = state.get("ai_fps", 0.0)

                            # Overlay banner
                            banner_col = (
                                (0, 0, 220)
                                if alert in ("DROWSY", "SUSPECTED")
                                else (0, 165, 255)
                                if alert == "YAWNING"
                                else (30, 30, 30)
                            )
                            cv2.rectangle(vis, (0, 0), (vis.shape[1], 36), banner_col, -1)
                            cv2.putText(
                                vis,
                                f"ALERT: {alert} | Eye: {eye_dur:.1f}s | Mouth: {mouth_dur:.1f}s | FPS: {fps:.1f}",
                                (10, 24),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                (255, 255, 255),
                                2,
                            )
                            ok, jpg = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                            if not ok:
                                continue
                            data = jpg.tobytes()
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
                            self.wfile.write(data)
                            self.wfile.write(b"\r\n")
                    except Exception:
                        pass

                elif self.path == "/api/status":
                    with parent._condition:
                        state = dict(parent._state)
                    # Merge hardware metrics
                    hw = get_system_metrics()

                    # Merge ESP info
                    esp_info = {"connected": False, "port": "", "latest_telemetry": "", "reset_count": 0}
                    if parent.esp:
                        esp_info["connected"] = parent.esp.connected
                        esp_info["port"] = parent.esp.port or ""
                        esp_info["latest_telemetry"] = parent.esp.latest_telemetry or ""
                        esp_info["reset_count"] = parent.esp.reset_count

                        # Parse CO2 if available
                        raw = parent.esp.latest_telemetry or ""
                        if "CO2:" in raw:
                            try:
                                parts = raw.split(",")
                                for p in parts:
                                    if p.startswith("CO2:"):
                                        esp_info["co2_ppm"] = float(p.split(":")[1])
                                    elif p.startswith("LV:"):
                                        esp_info["co2_level"] = int(p.split(":")[1])
                                    elif p.startswith("SPD:"):
                                        esp_info["speed_kmh"] = float(p.split(":")[1])
                            except Exception:
                                pass

                    # Config info
                    cfg = {
                        "drowsy_threshold": (
                            getattr(parent.pipeline, "drowsy_threshold", 2.0) if parent.pipeline else 2.0
                        ),
                        "yawn_threshold": (
                            getattr(parent.pipeline, "yawning_threshold", 1.2) if parent.pipeline else 1.2
                        ),
                        "distraction_threshold": (
                            getattr(parent.pipeline, "distraction_threshold", 1.5) if parent.pipeline else 1.5
                        ),
                        "head_pitch_threshold": (
                            getattr(parent.pipeline, "head_pitch_threshold", 22.0) if parent.pipeline else 22.0
                        ),
                        "voice_cooldown_s": (
                            getattr(parent.esp, "voice_cooldown_s", 5.0) if parent.esp else 5.0
                        ),
                        "ear_threshold": (
                            getattr(parent.pipeline, "ear_threshold", 0.24) if parent.pipeline else 0.24
                        ),
                    }

                    resp_data = {
                        "alert": state.get("confirmed_alert", "NORMAL"),
                        "ai_fps": state.get("ai_fps", 0.0),
                        "yolo_latency_ms": state.get("yolo_latency_ms", 0.0),
                        "pipeline_latency_ms": state.get("pipeline_latency_ms", 0.0),
                        "eye_duration": state.get("eye_duration", 0.0),
                        "mouth_duration": state.get("mouth_duration", 0.0),
                        "distracted_duration": state.get("distracted_duration", 0.0),
                        "head_pose": state.get("head_pose"),
                        "detected_classes": state.get("detected_classes", []),
                        "hardware": hw,
                        "esp": esp_info,
                        "config": cfg,
                        "events": list(parent._events),
                    }
                    body = json.dumps(resp_data).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                try:
                    payload = json.loads(raw_body)
                except Exception:
                    payload = {}

                if self.path == "/api/control":
                    cmd = payload.get("cmd", "").strip().upper()
                    if not cmd:
                        self._json_response({"ok": False, "error": "Missing cmd parameter"}, 400)
                        return
                    if not parent.esp:
                        self._json_response({"ok": False, "error": "ESP communicator not initialized"}, 503)
                        return
                    ok, ack = parent.esp.send_raw_command(cmd, wait_ack=True, timeout=1.5)
                    self._json_response({"ok": ok, "ack": ack, "cmd": cmd})

                elif self.path == "/api/config":
                    if not parent.pipeline and not parent.esp:
                        self._json_response({"ok": False, "error": "Pipeline/ESP not initialized"}, 503)
                        return
                    if "drowsy_threshold" in payload and parent.pipeline:
                        val = float(payload["drowsy_threshold"])
                        parent.pipeline.drowsy_threshold = val
                        if hasattr(parent.pipeline, "fusion"):
                            parent.pipeline.fusion.thresholds["eye"] = val
                    if "yawn_threshold" in payload and parent.pipeline:
                        val = float(payload["yawn_threshold"])
                        parent.pipeline.yawning_threshold = val
                        if hasattr(parent.pipeline, "fusion"):
                            parent.pipeline.fusion.thresholds["mouth"] = val
                    if "ear_threshold" in payload and parent.pipeline:
                        parent.pipeline.ear_threshold = float(payload["ear_threshold"])
                    if "distraction_threshold" in payload and parent.pipeline:
                        parent.pipeline.distraction_threshold = float(payload["distraction_threshold"])
                    if "head_pitch_threshold" in payload and parent.pipeline:
                        parent.pipeline.head_pitch_threshold = float(payload["head_pitch_threshold"])
                    if "voice_cooldown_s" in payload and parent.esp:
                        parent.esp.voice_cooldown_s = float(payload["voice_cooldown_s"])
                    self._json_response({"ok": True, "applied": payload})
                else:
                    self.send_response(404)
                    self.end_headers()

            def _json_response(self, data: dict, code: int = 200):
                body = json.dumps(data).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", self.port), RequestHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="web-dashboard")
            self._thread.start()
            print(f"[WEB DASHBOARD] Mo giao dien Web Dieu Khien tai: http://0.0.0.0:{self.port}")
        except Exception as exc:
            print(f"[WEB DASHBOARD] Loi khoi tao cong {self.port}: {exc}")

    def close(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
