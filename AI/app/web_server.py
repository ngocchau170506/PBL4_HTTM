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
import numpy as np

from AI.scripts.telemetry import get_system_metrics


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
  
  /* Thanh Ping Tín Hiệu Nhỏ (Signal Badges) */
  .ping-bar {
    display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px;
  }
  .ping-pill {
    background: #151d2f; border: 1px solid var(--border);
    border-radius: 20px; padding: 6px 14px; font-size: 12px;
    font-weight: 600; display: flex; align-items: center; gap: 8px;
    transition: all 0.2s ease;
  }
  .ping-dot {
    width: 8px; height: 8px; border-radius: 50%; display: inline-block;
  }
  .ping-dot-green { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .ping-dot-red { background: var(--red); box-shadow: 0 0 8px var(--red); }
  .ping-dot-amber { background: var(--amber); box-shadow: 0 0 8px var(--amber); }
  .ping-dot-blue { background: var(--accent); box-shadow: 0 0 8px var(--accent); }
  .ping-dot-purple { background: var(--purple); box-shadow: 0 0 8px var(--purple); }

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
    <span id="auth-badge" class="badge" style="display:none; background:rgba(56,189,248,0.15); color:var(--accent); border:1px solid var(--accent); margin-left:6px;">🔒 ĐÃ XÁC THỰC</span>
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
    <!-- CHỈ SỐ AI, TÀI NGUYÊN PI 4 & BẢNG ĐO TỌA ĐỘ 3D XYZ GỌN GÀNG -->
    <div class="card" style="border: 1px solid rgba(56,189,248,0.4); background: linear-gradient(180deg, #151d2f 0%, #0d1527 100%);">
      <div class="card-title">
        <span>⚡ Chỉ Số AI & Tài Nguyên Raspberry Pi 4</span>
        <span id="badge-source" class="badge badge-online">ĐÀ NẴNG (MÁY TÍNH)</span>
      </div>

      <!-- 1. HÀNG PING TÍN HIỆU NHỎ GỌN TRONG GÓC -->
      <div class="ping-bar" style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px;">
        <div class="ping-pill" id="pill-esp" style="padding:4px 10px; font-size:11px;">
          <div class="ping-dot ping-dot-amber" id="dot-esp"></div>
          <span id="text-ping-esp">ESP32: DÒ CỔNG</span>
        </div>
        <div class="ping-pill" id="pill-gps" style="padding:4px 10px; font-size:11px;">
          <div class="ping-dot ping-dot-blue" id="dot-gps"></div>
          <span id="text-ping-gps">GPS: SERVER-XYZ</span>
        </div>
        <div class="ping-pill" id="pill-net" style="padding:4px 10px; font-size:11px;">
          <div class="ping-dot ping-dot-green" id="dot-net"></div>
          <span id="text-ping-net">TRỄ: -- ms</span>
        </div>
        <div class="ping-pill" id="pill-dist" style="padding:4px 10px; font-size:11px;">
          <div class="ping-dot ping-dot-purple" id="dot-dist"></div>
          <span id="text-ping-dist">CÁCH: 0.00 m</span>
        </div>
      </div>

      <!-- 2. CHỈ SỐ AI & PHẦN CỨNG PI 4 -->
      <div class="metrics-grid" style="margin-bottom:12px;">
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
        <div class="metric-box" style="grid-column: span 2;">
          <div class="metric-label">Góc Đầu (P/Y/R)</div>
          <div id="val-pose" class="metric-value" style="font-size:12px; color:var(--accent);">-- / -- / --</div>
        </div>
      </div>

      <!-- 3. BẢNG ĐO TỌA ĐỘ 3D XYZ & KHOẢNG CÁCH ĐƯỜNG TRUYỀN (TÍCH HỢP GỌN 1 GÓC) -->
      <div style="background: rgba(11,15,25,0.75); border: 1px solid var(--border); border-radius: 8px; padding: 10px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; gap:6px; flex-wrap:wrap;">
          <span style="font-size:11px; font-weight:700; color:var(--accent); text-transform:uppercase;">📐 TỌA ĐỘ 3D XYZ & KHOẢNG CÁCH (PI ↔ MÁY TÍNH)</span>
          <div style="display:flex; gap:6px;">
            <button onclick="syncBrowserLocation()" style="font-size:10px; padding:2px 8px; background:rgba(56,189,248,0.2); border:1px solid var(--accent); color:#bae6fd; border-radius:4px; cursor:pointer;" title="Đồng bộ tọa độ định vị từ máy tính / trình duyệt sang Pi khi test phòng kín">📍 Bù Tọa Độ Từ PC</button>
            <a id="btn-map-link" href="https://www.google.com/maps?q=16.0685,108.2215" target="_blank" style="color:var(--accent); text-decoration:none; font-size:10px; border:1px solid var(--border); padding:1px 6px; border-radius:4px; background:#1e293b;">🗺️ Bản Đồ</a>
          </div>
        </div>

        <!-- 3 KHỐI TỌA ĐỘ XYZ GỌN GÀNG -->
        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-bottom: 8px;">
          <div style="background: rgba(56,189,248,0.06); border-left: 2px solid var(--accent); padding: 6px 8px; border-radius: 4px;">
            <div style="font-size:10px; color:var(--muted);">TRỤC X (KINH ĐỘ)</div>
            <div id="val-x-coord" style="font-size:12px; font-weight:700; color:var(--accent); font-family:monospace; margin-top:2px;">108.221500° E</div>
          </div>
          <div style="background: rgba(34,197,94,0.06); border-left: 2px solid var(--green); padding: 6px 8px; border-radius: 4px;">
            <div style="font-size:10px; color:var(--muted);">TRỤC Y (VĨ ĐỘ)</div>
            <div id="val-y-coord" style="font-size:12px; font-weight:700; color:var(--green); font-family:monospace; margin-top:2px;">16.068500° N</div>
          </div>
          <div style="background: rgba(168,85,247,0.06); border-left: 2px solid var(--purple); padding: 6px 8px; border-radius: 4px;">
            <div style="font-size:10px; color:var(--muted);">TRỤC Z (CAO ĐỘ)</div>
            <div id="val-z-coord" style="font-size:12px; font-weight:700; color:var(--purple); font-family:monospace; margin-top:2px;">8.5 mét</div>
          </div>
        </div>

        <!-- KHOẢNG CÁCH (MÉT) & TỐC ĐỘ ĐƯỜNG TRUYỀN (PING MS) -->
        <div style="display:grid; grid-template-columns: 1.25fr 1fr; gap: 6px;">
          <div style="background: rgba(56,189,248,0.06); border: 1px solid rgba(56,189,248,0.25); border-radius: 6px; padding: 6px 10px; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div style="font-size:10px; color:var(--muted); text-transform:uppercase;">📏 KHOẢNG CÁCH PI ↔ PC</div>
              <div style="font-size:10px; color:var(--muted); margin-top:2px;">Lệch Z: <span id="val-delta-alt">0.0</span>m</div>
            </div>
            <div style="display:flex; align-items:baseline; gap:4px;">
              <span id="val-dist-meters" style="font-size:20px; font-weight:800; color:#fff;">0.00</span>
              <span style="font-size:12px; font-weight:700; color:var(--accent);">mét</span>
            </div>
          </div>

          <div style="background: rgba(34,197,94,0.06); border: 1px solid rgba(34,197,94,0.25); border-radius: 6px; padding: 6px 10px; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div style="font-size:10px; color:var(--muted); text-transform:uppercase;">📶 ĐƯỜNG TRUYỀN (LAN)</div>
              <div style="font-size:10px; color:var(--green); margin-top:2px;">RTT Khứ hồi</div>
            </div>
            <div style="display:flex; align-items:baseline; gap:4px;">
              <span id="val-net-ping" style="font-size:20px; font-weight:800; color:#fff;">--</span>
              <span style="font-size:12px; font-weight:700; color:var(--green);">ms</span>
            </div>
          </div>
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
        <div class="metric-box" style="grid-column: span 2;">
          <div class="metric-label" style="display:flex; justify-content:space-between; align-items:center;">
            <span>Định Vị Tọa Độ XYZ (Server-Assisted)</span>
            <a id="btn-map-link-esp" href="#" target="_blank" style="color:var(--accent); text-decoration:none; font-size:11px; display:none; border:1px solid var(--border); padding:1px 6px; border-radius:4px; background:rgba(56,189,248,0.1);">🗺️ Mở Bản Đồ</a>
          </div>
          <div id="val-gps-xyz" class="metric-value" style="font-size:12px; color:var(--accent);">-- , -- , --</div>
          <div id="val-gps-src" style="font-size:11px; color:var(--muted); margin-top:2px;">Nguồn: Chờ Máy Chủ / Vệ tinh</div>
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

    <!-- KẾT NỐI SERVER TỪ XA (SERVER BẠN TÔI) -->
    <div class="card">
      <div class="card-title">Kết Nối Máy Chủ Trung Tâm (Server Bạn Tôi)</div>
      <div style="font-size:12px; margin-bottom:10px;">
        <div><b>URL Server Hiện Tại:</b> <code id="sync-url" style="color:var(--accent);">--</code></div>
        <div style="margin-top:4px;"><b>Trạng thái:</b> <span id="sync-status" class="badge badge-offline">CHƯA KẾT NỐI</span></div>
      </div>
      <div style="display:flex; gap:8px; margin-bottom:12px;">
        <input type="text" id="cfg-server-url" placeholder="http://172.20.10.4:5000" style="flex:1; padding:7px 10px; background:var(--bg-input); border:1px solid var(--border); border-radius:6px; color:#fff; font-size:13px;">
        <button class="btn-green" style="padding:7px 14px; white-space:nowrap;" onclick="updateServerUrl()">💾 Lưu URL</button>
      </div>
      <div class="metrics-grid" style="margin-bottom:12px;">
        <div class="metric-box">
          <div class="metric-label">Gói Đã Gửi</div>
          <div id="sync-packets" class="metric-value">0</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Vi Phạm Đã Gửi</div>
          <div id="sync-violations" class="metric-value" style="color:var(--red);">0</div>
        </div>
        <div class="metric-box">
          <div class="metric-label">Đệm Gửi Bù (Spool)</div>
          <div id="sync-spool" class="metric-value" style="color:var(--amber);">0</div>
        </div>
      </div>
      <button class="btn-blue" style="width:100%; padding:8px;" onclick="testSyncServer()">🚀 Gửi Thử Nghiệm Lên Server Bạn Tôi</button>
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
  const _urlToken = new URLSearchParams(window.location.search).get('token');
  if (_urlToken) {
    localStorage.setItem('web_auth_token', _urlToken);
  }
  function authHeaders(custom = {}) {
    const h = { ...custom };
    const tok = localStorage.getItem('web_auth_token') || _urlToken;
    if (tok) {
      h['X-API-Token'] = tok;
      h['Authorization'] = 'Bearer ' + tok;
    }
    return h;
  }
  function handleAuthError(res) {
    if (res && res.status === 401) {
      const input = prompt('Yêu cầu mã xác thực (WEB_AUTH_TOKEN) để thao tác:');
      if (input) {
        localStorage.setItem('web_auth_token', input.trim());
        window.location.href = '/?token=' + encodeURIComponent(input.trim());
      }
      return true;
    }
    return false;
  }
  const _activeTok = localStorage.getItem('web_auth_token') || _urlToken;
  if (_activeTok) {
    const ab = document.getElementById('auth-badge');
    if (ab) ab.style.display = 'inline-block';
    const camImg = document.getElementById('cam-img');
    if (camImg && camImg.getAttribute('src') === '/stream') {
      camImg.src = '/stream?token=' + encodeURIComponent(_activeTok);
    }
  }

  function showToast(msg) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; }, 2500);
  }

  async function testSyncServer() {
    showToast('Đang gửi gói thử nghiệm lên Server bạn tôi...');
    try {
      const res = await fetch('/api/sync/test', {
        method: 'POST',
        headers: authHeaders({'Content-Type': 'application/json'}),
      });
      if (handleAuthError(res)) return;
      const data = await res.json();
      if (data.ok) {
        showToast('✅ Thành công! Server bạn tôi đã nhận: ' + data.resp);
        alert('✅ KẾT NỐI SERVER THÀNH CÔNG!\nPhản hồi từ Server: ' + data.resp + '\nURL: ' + data.server_url);
      } else {
        showToast('❌ Thất bại: ' + (data.resp || data.error));
        alert('❌ KẾT NỐI SERVER THẤT BẠI!\nLỗi: ' + (data.resp || data.error) + '\nURL đang thử: ' + data.server_url + '\n\n👉 Kiểm tra: Máy bạn đã chạy "python run_server.py" chưa, và đã mở cổng Firewall 5000 chưa?');
      }
    } catch (e) {
      showToast('Lỗi gửi: ' + e);
    }
  }

  async function updateServerUrl() {
    const urlInput = document.getElementById('cfg-server-url').value.trim();
    if (!urlInput) {
      alert('Vui lòng nhập URL hoặc IP Server (ví dụ: http://172.20.10.4:5000)');
      return;
    }
    showToast('Đang cập nhật URL Server...');
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ server_url: urlInput })
      });
      if (handleAuthError(res)) return;
      const data = await res.json();
      if (data.ok) {
        showToast('✅ Đã lưu URL Server!');
        alert('✅ ĐÃ CẬP NHẬT URL SERVER THÀNH CÔNG!\nURL mới: ' + (data.applied?.server_url || urlInput) + '\nĐang thử gửi gói tin test...');
        testSyncServer();
      } else {
        alert('❌ Cập nhật thất bại: ' + (data.error || 'Unknown error'));
      }
    } catch (err) {
      alert('❌ Lỗi kết nối: ' + err);
    }
  }

  async function syncBrowserLocation() {
    const fallbackPrompt = async () => {
      const latStr = prompt('Nhập Vĩ độ (Latitude) phòng máy tính / lab:', '16.068500');
      if (!latStr) return;
      const lonStr = prompt('Nhập Kinh độ (Longitude) phòng máy tính / lab:', '108.221500');
      if (!lonStr) return;
      const lat = parseFloat(latStr);
      const lon = parseFloat(lonStr);
      if (isNaN(lat) || isNaN(lon)) {
        alert('Tọa độ không hợp lệ!');
        return;
      }
      await pushLocationToPi(lat, lon, 8.5, 'manual_input');
    };

    if (!navigator.geolocation) {
      showToast('Trình duyệt không hỗ trợ Geolocation, chuyển sang nhập tay.');
      return fallbackPrompt();
    }

    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const lat = pos.coords.latitude;
        const lon = pos.coords.longitude;
        const alt = pos.coords.altitude || 8.5;
        await pushLocationToPi(lat, lon, alt, 'browser_geolocation');
      },
      (err) => {
        showToast('Trình duyệt chặn GPS (chưa cấp quyền hoặc HTTP): ' + err.message + ' -> Chuyển sang nhập tay.');
        fallbackPrompt();
      },
      { timeout: 5000, enableHighAccuracy: true }
    );
  }

  async function pushLocationToPi(lat, lon, alt, source) {
    try {
      const res = await fetch('/api/set_pc_location', {
        method: 'POST',
        headers: authHeaders({'Content-Type': 'application/json'}),
        body: JSON.stringify({ lat, lon, alt, source })
      });
      if (handleAuthError(res)) return;
      const data = await res.json();
      if (data.ok) {
        showToast('✅ Đã đồng bộ tọa độ [' + lat.toFixed(4) + ', ' + lon.toFixed(4) + '] sang Pi!');
      } else {
        showToast('❌ Lỗi bù tọa độ: ' + (data.error || 'Thất bại'));
      }
    } catch (e) {
      showToast('❌ Lỗi kết nối: ' + e);
    }
  }

  async function sendCmd(cmd) {
    try {
      const res = await fetch('/api/control', {
        method: 'POST',
        headers: authHeaders({'Content-Type': 'application/json'}),
        body: JSON.stringify({cmd: cmd})
      });
      if (handleAuthError(res)) return;
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
        headers: authHeaders({'Content-Type': 'application/json'}),
        body: JSON.stringify(payload)
      });
      if (handleAuthError(res)) return;
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
      const res = await fetch('/api/status', { headers: authHeaders() });
      if (res.status === 401) {
        document.getElementById('conn-badge').className = 'badge badge-offline';
        document.getElementById('conn-badge').innerText = 'KHÓA BẢO MẬT (401)';
        return;
      }
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

      // Cập nhật Ping Badge ESP32
      const dotEsp = document.getElementById('dot-esp');
      const textEsp = document.getElementById('text-ping-esp');
      if (dotEsp && textEsp) {
        dotEsp.className = espConn ? 'ping-dot ping-dot-green' : 'ping-dot ping-dot-amber';
        textEsp.innerText = espConn ? 'ESP32: TÍN HIỆU TỐT (' + (data.esp.port || 'USB') + ')' : 'ESP32: ĐANG TÌM TÍN HIỆU';
      }

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
      if (data.gps) {
        document.getElementById('val-speed').innerText = Number(data.gps.speed_kmh || 0).toFixed(1) + ' km/h';
        const isServerGps = data.gps.status === 'SERVER_XYZ' || data.gps.satellites === 99;
        const srcLabel = isServerGps ? '🛰️ MÁY CHỦ XYZ (Server-Assisted)' : (data.gps.is_valid ? '🛰️ Vệ Tinh GNSS (' + (data.gps.satellites || 0) + ' sats)' : 'Chờ bắt sóng...');
        const srcEl = document.getElementById('val-gps-src');
        if (srcEl) {
          srcEl.innerText = 'Nguồn: ' + srcLabel;
          srcEl.style.color = isServerGps ? 'var(--accent)' : (data.gps.is_valid ? 'var(--green)' : 'var(--muted)');
        }
        const xyzEl = document.getElementById('val-gps-xyz');
        if (xyzEl && data.gps.lat && data.gps.lon) {
          xyzEl.innerText = `X(Lon): ${data.gps.lon.toFixed(4)}, Y(Lat): ${data.gps.lat.toFixed(4)}, Z(Alt): ${(data.gps.altitude_m || 0).toFixed(1)}m`;
        }

        // Cập nhật 3 Thẻ Đo Tọa Độ X, Y, Z (Độ chính xác cao 6 chữ số)
        const elX = document.getElementById('val-x-coord');
        const elY = document.getElementById('val-y-coord');
        const elZ = document.getElementById('val-z-coord');
        if (elX && data.gps.lon !== undefined) elX.innerText = data.gps.lon.toFixed(6) + '° E';
        if (elY && data.gps.lat !== undefined) elY.innerText = data.gps.lat.toFixed(6) + '° N';
        if (elZ && data.gps.altitude_m !== undefined) elZ.innerText = (data.gps.altitude_m || 0).toFixed(1) + ' mét';

        const badgeSrc = document.getElementById('badge-source');
        if (badgeSrc) {
          badgeSrc.innerText = isServerGps ? 'MÁY CHỦ XYZ (ĐÀ NẴNG)' : (data.gps.is_valid ? 'GPS VỆ TINH PHẦN CỨNG' : 'CHỜ TÍN HIỆU');
        }

        // Cập nhật Ping Badge GPS
        const dotGps = document.getElementById('dot-gps');
        const textGps = document.getElementById('text-ping-gps');
        if (dotGps && textGps) {
          dotGps.className = data.gps.is_valid ? 'ping-dot ping-dot-green' : isServerGps ? 'ping-dot ping-dot-blue' : 'ping-dot ping-dot-red';
          textGps.innerText = data.gps.is_valid ? 'GPS: ĐÃ BẮT TÍN HIỆU (' + (data.gps.satellites || 0) + ' SATS)' : isServerGps ? 'GPS: SERVER-ASSISTED' : 'GPS: ĐANG TÌM TÍN HIỆU';
        }

        const mapLink = document.getElementById('btn-map-link');
        if (mapLink && data.gps.lat && data.gps.lon) {
          mapLink.href = data.gps.google_maps_url || `https://www.google.com/maps?q=${data.gps.lat},${data.gps.lon}`;
          mapLink.style.display = 'inline-block';
        }
      }

      // Cập nhật khoảng cách đo bằng mét Pi ↔ Máy tính
      if (data.distance) {
        const dMeters = data.distance.distance_meters;
        const textDist = document.getElementById('text-ping-dist');
        if (textDist) textDist.innerText = 'KHOẢNG CÁCH: ' + dMeters.toFixed(2) + ' mét';
        const valDist = document.getElementById('val-dist-meters');
        if (valDist) valDist.innerText = dMeters.toFixed(2);
        const valDeltaAlt = document.getElementById('val-delta-alt');
        if (valDeltaAlt) valDeltaAlt.innerText = data.distance.delta_alt_m.toFixed(1);
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

      // Đồng bộ máy chủ trung tâm
      if (data.sync) {
        document.getElementById('sync-url').innerText = data.sync.server_url || '--';
        const syncBadge = document.getElementById('sync-status');
        if (data.sync.online) {
          syncBadge.className = 'badge badge-online';
          syncBadge.innerText = 'ĐANG ĐỒNG BỘ (' + (data.sync.last_sync_time || 'OK') + ')';
        } else {
          syncBadge.className = 'badge badge-offline';
          syncBadge.innerText = data.sync.last_sync_status || 'MẤT KẾT NỐI';
        }
        document.getElementById('sync-packets').innerText = data.sync.packets_sent || 0;
        document.getElementById('sync-violations').innerText = data.sync.violations_sent || 0;
        document.getElementById('sync-spool').innerText = data.sync.pending_spool || 0;
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

  // Đo thời gian trễ đường truyền mạng (Ping RTT) giữa máy tính và Pi
  async function measurePing() {
    const t0 = performance.now();
    try {
      const res = await fetch('/api/ping?t=' + Date.now(), { headers: authHeaders(), cache: 'no-store' });
      if (res.ok) {
        const pingMs = Math.round(performance.now() - t0);
        const valNetPing = document.getElementById('val-net-ping');
        if (valNetPing) valNetPing.innerText = pingMs;
        const textNet = document.getElementById('text-ping-net');
        if (textNet) textNet.innerText = 'ĐƯỜNG TRUYỀN: ' + pingMs + ' ms';
        const dotNet = document.getElementById('dot-net');
        if (dotNet) dotNet.className = pingMs < 50 ? 'ping-dot ping-dot-green' : 'ping-dot ping-dot-amber';
      }
    } catch (e) {
      const valNetPing = document.getElementById('val-net-ping');
      if (valNetPing) valNetPing.innerText = '--';
      const textNet = document.getElementById('text-ping-net');
      if (textNet) textNet.innerText = 'ĐƯỜNG TRUYỀN: MẤT KẾT NỐI';
      const dotNet = document.getElementById('dot-net');
      if (dotNet) dotNet.className = 'ping-dot ping-dot-red';
    }
  }

  setInterval(measurePing, 3000);
  measurePing();
  setInterval(fetchStatus, 2000);
  fetchStatus();
</script>
</body>
</html>
"""

UNLOCK_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Driver Monitor - Xác Thực Truy Cập</title>
<style>
  :root {
    --bg: #0b0f19;
    --card: #151d2f;
    --border: #23314d;
    --text: #f1f5f9;
    --muted: #94a3b8;
    --accent: #38bdf8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 16px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 30px;
    max-width: 420px;
    width: 100%;
    box-shadow: 0 10px 30px rgba(0,0,0,0.6);
  }
  .icon { font-size: 40px; margin-bottom: 12px; }
  h2 { font-size: 20px; font-weight: 700; margin-bottom: 8px; color: #fff; }
  p { font-size: 13px; color: var(--muted); margin-bottom: 20px; line-height: 1.5; }
  input {
    width: 100%;
    background: #0b0f19;
    border: 1px solid var(--border);
    color: #fff;
    padding: 12px 14px;
    border-radius: 8px;
    font-size: 14px;
    margin-bottom: 14px;
  }
  input:focus { outline: none; border-color: var(--accent); }
  button {
    width: 100%;
    background: var(--accent);
    color: #0b0f19;
    border: none;
    padding: 12px;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    transition: opacity 0.2s;
  }
  button:hover { opacity: 0.9; }
  .note {
    font-size: 11px;
    color: var(--muted);
    margin-top: 16px;
    text-align: center;
    line-height: 1.4;
  }
</style>
</head>
<body>
<div class="card">
  <div class="icon">🔒</div>
  <h2>Bảo Vệ Bảng Điều Khiển</h2>
  <p>Hệ thống giám sát xe đang kích hoạt bảo mật. Vui lòng nhập mã bảo vệ (<b>WEB_AUTH_TOKEN</b>) để tiếp tục.</p>
  <form onsubmit="event.preventDefault(); const t = document.getElementById('token-input').value.trim(); if(t){ window.location.href = '/?token=' + encodeURIComponent(t); }">
    <input type="password" id="token-input" placeholder="Nhập WEB_AUTH_TOKEN..." autofocus required />
    <button type="submit">Mở Bảng Điều Khiển</button>
  </form>
  <div class="note">Mã này cấu hình trong <code>AI/deploy/pi.env</code> (hoặc truyền qua tham số <code>?token=...</code>)</div>
</div>
</body>
</html>
"""


class DriverMonitorWebServer:
    """Multi-threaded web server serving live video and bi-directional REST APIs."""

    def __init__(self, port: int = 8080, auth_token: Optional[str] = None):
        self.port = int(port)
        self.auth_token = (
            auth_token.strip()
            if auth_token is not None
            else os.getenv("WEB_AUTH_TOKEN", "").strip()
        )
        self._frame = None
        self._state: Dict[str, Any] = {}
        self._events = []
        self._last_logged_alert: Optional[str] = None
        self._condition = threading.Condition()
        self._frame_seq = 0
        self._jpeg_seq = -1
        self._jpeg_bytes: Optional[bytes] = None
        self._encode_lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.pipeline = None
        self.esp = None
        self.camera_source = None
        self.cloud_sync = None
        self.gps = None
        self._remote_esp_info: Dict[str, Any] = {}
        self._remote_esp_ts: float = 0.0
        self._remote_cmd_queue: list[str] = []
        self._pc_anchor_coords: Dict[str, float] = {"lat": 16.068500, "lon": 108.221500, "alt": 8.5}

        if self.port > 0:
            self._start()

    def set_context(self, pipeline=None, esp=None, camera_source=None, cloud_sync=None, gps=None):
        if pipeline is not None:
            self.pipeline = pipeline
        if esp is not None:
            self.esp = esp
        if camera_source is not None:
            self.camera_source = camera_source
        if cloud_sync is not None:
            self.cloud_sync = cloud_sync
        if gps is not None:
            self.gps = gps
        elif cloud_sync and getattr(cloud_sync, "gps_manager", None):
            self.gps = cloud_sync.gps_manager

        # Tự động chuyển giao liên kết esp và gps cho cloud_sync
        if self.cloud_sync and hasattr(self.cloud_sync, "set_context"):
            try:
                self.cloud_sync.set_context(esp=self.esp, gps=self.gps)
            except Exception:
                pass

    def update_frame(self, frame, state=None):
        with self._condition:
            self._frame = frame
            self._frame_seq += 1
            # Thu thập các sự kiện phần cứng độc lập từ ESP32 (CO2 tự động, Brownout sụt áp, nút Mute)
            if self.esp and hasattr(self.esp, "pop_hw_events"):
                for hw_evt in self.esp.pop_hw_events():
                    self._events.append(hw_evt)
                    if len(self._events) > 30:
                        self._events.pop(0)

            if state:
                self._state = state
                if self.cloud_sync:
                    self.cloud_sync.update_state(state)
                alert = state.get("confirmed_alert", "NORMAL")
                esp_target = state.get("esp_target")
                detailed_alert = state.get("detailed_alert") or ""
                alert_reason = state.get("alert_reason") or ""

                # Chỉ ghi vào bảng Nhật Ký Cảnh Báo khi thực sự có cảnh báo hoặc xung đột (không ghi TRACKING_LOST đơn thuần)
                is_actionable_alert = (
                    esp_target in ("DROWSY", "YAWNING", "DISTRACTED")
                    or alert in ("DROWSY", "YAWNING", "DISTRACTED", "CONFLICT")
                )
                if is_actionable_alert:
                    if alert == "SUSPECTED":
                        if esp_target == "DROWSY" or "DROWSY" in detailed_alert or detailed_alert == "SUSPECTED_ALERT":
                            event_alert = "DROWSY (SUSPECTED)"
                        elif esp_target == "YAWNING" or "YAWN" in detailed_alert:
                            event_alert = "YAWNING (SUSPECTED)"
                        else:
                            event_alert = f"SUSPECTED ({esp_target or detailed_alert})"
                    elif alert == "CONFLICT":
                        event_alert = "CONFLICT (CHẶN CẢNH BÁO GIẢ)"
                    else:
                        event_alert = alert

                    if esp_target == "DROWSY" or "DROWSY" in event_alert:
                        base_cmd = "VOICE:1"
                    elif esp_target == "YAWNING" or "YAWN" in event_alert:
                        base_cmd = "VOICE:2"
                    elif esp_target == "DISTRACTED" or "DISTRACT" in event_alert:
                        base_cmd = "VOICE:3"
                    elif alert == "CONFLICT":
                        base_cmd = "KHÔNG PHÁT (Xung đột YOLO/MP)"
                    else:
                        base_cmd = esp_target or "NONE"

                    det_list = list(state.get("detected_classes", []))
                    if alert_reason and alert_reason not in det_list:
                        det_list.append(alert_reason)

                    if self._last_logged_alert != event_alert:
                        self._last_logged_alert = event_alert
                        self._events.append(
                            {
                                "time": time.strftime("%H:%M:%S"),
                                "alert": event_alert,
                                "classes": det_list,
                                "cmd": base_cmd,
                            }
                        )
                        if len(self._events) > 30:
                            self._events.pop(0)
                    elif self._events and self._events[-1]["alert"] == event_alert:
                        # Cập nhật trạng thái phản hồi ACK thực tế từ ESP32 ngay khi luồng UART hoàn tất
                        delivery = getattr(self.esp, "last_delivery_status", "") if self.esp else ""
                        if delivery and delivery != "READY" and alert != "CONFLICT":
                            self._events[-1]["cmd"] = delivery
                        if det_list:
                            self._events[-1]["classes"] = det_list
                else:
                    self._last_logged_alert = None
            self._condition.notify_all()

    def get_stream_jpeg(self, after_seq: int, timeout: float = 1.0):
        """Chờ khung hình mới rồi trả (seq, jpeg_bytes); JPEG nén 1 lần/khung và dùng chung cho mọi client."""
        with self._condition:
            self._condition.wait_for(lambda: self._frame_seq != after_seq, timeout=timeout)
            seq = self._frame_seq
            frame = self._frame
            state = self._state
        if seq == after_seq:
            return after_seq, None
        with self._encode_lock:
            if self._jpeg_seq == seq and self._jpeg_bytes is not None:
                return seq, self._jpeg_bytes
            if frame is None:
                vis = np.zeros((480, 640, 3), dtype=np.uint8)
                vis[:] = (20, 24, 33)
                cv2.putText(vis, "DANG CHO TIN HIEU CAMERA NGOAI...", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (56, 189, 248), 2)
                cv2.putText(vis, "Dang do RTSP: 192.168.1.210:554 hoac USB Webcam", (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
                cv2.putText(vis, "Cam cap LAN Camera hoac USB Camera vao Pi de bat hinh", (50, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (148, 163, 184), 1)
            else:
                vis = frame.copy()  # copy vì khung hình dùng chung với luồng AI/bằng chứng
            alert = state.get("confirmed_alert", "NORMAL")
            banner_col = (
                (0, 0, 220) if alert in ("DROWSY", "SUSPECTED")
                else (0, 165, 255) if alert == "YAWNING"
                else (30, 30, 30)
            )
            cv2.rectangle(vis, (0, 0), (vis.shape[1], 36), banner_col, -1)
            cv2.putText(
                vis,
                f"ALERT: {alert} | Eye: {state.get('eye_duration', 0.0):.1f}s | "
                f"Mouth: {state.get('mouth_duration', 0.0):.1f}s | FPS: {state.get('ai_fps', 0.0):.1f}",
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2,
            )
            ok, jpg = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
            if not ok:
                return seq, None
            self._jpeg_bytes = jpg.tobytes()
            self._jpeg_seq = seq
            return seq, self._jpeg_bytes

    def _start(self):
        parent = self

        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Tắt log spam HTTP request

            def _is_authorized(self) -> bool:
                if not parent.auth_token:
                    return True
                client_ip = self.client_address[0] if self.client_address else ""
                if client_ip in ("127.0.0.1", "::1", "localhost"):
                    return True

                auth_header = self.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    if auth_header[7:].strip() == parent.auth_token:
                        return True

                if self.headers.get("X-API-Token", "").strip() == parent.auth_token:
                    return True

                from urllib.parse import urlparse, parse_qs
                query = parse_qs(urlparse(self.path).query)
                if query.get("token") and query["token"][0].strip() == parent.auth_token:
                    return True

                cookie_header = self.headers.get("Cookie", "")
                if cookie_header:
                    for part in cookie_header.split(";"):
                        k, _, v = part.strip().partition("=")
                        if k == "web_token" and v == parent.auth_token:
                            return True

                return False

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                parsed_url = urlparse(self.path)
                req_path = parsed_url.path
                query = parse_qs(parsed_url.query)

                is_auth = self._is_authorized()
                has_valid_url_token = bool(query.get("token") and query["token"][0].strip() == parent.auth_token)

                if req_path in ("/", "/index.html"):
                    if parent.auth_token and not is_auth:
                        auth_html = UNLOCK_HTML.encode("utf-8")
                        self.send_response(401)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(auth_html)))
                        self.end_headers()
                        self.wfile.write(auth_html)
                        return

                    html_data = DASHBOARD_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html_data)))
                    if parent.auth_token and (has_valid_url_token or is_auth):
                        self.send_header("Set-Cookie", f"web_token={parent.auth_token}; Path=/; SameSite=Lax; Max-Age=2592000")
                    self.end_headers()
                    self.wfile.write(html_data)

                elif req_path.startswith("/stream"):
                    if parent.auth_token and not is_auth:
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        body = json.dumps({"ok": False, "error": "Unauthorized: Yêu cầu WEB_AUTH_TOKEN"}).encode("utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return

                    self.send_response(200)
                    self.send_header("Cache-Control", "no-cache, private")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    last_seq = -1
                    try:
                        while True:
                            # JPEG được nén ĐÚNG 1 LẦN cho mỗi khung hình và chia sẻ cho mọi người xem;
                            # chỉ gửi khi có khung hình mới (không gửi lặp, không sleep xoay vòng).
                            last_seq, data = parent.get_stream_jpeg(last_seq, timeout=1.0)
                            if data is None:
                                continue
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
                            self.wfile.write(data)
                            self.wfile.write(b"\r\n")
                    except Exception:
                        pass

                elif req_path.startswith("/api/ping"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-cache, no-store")
                    body = json.dumps({
                        "ok": True,
                        "server_time_ms": int(time.time() * 1000),
                    }).encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                elif req_path in ("/api/status", "/api/state"):
                    if parent.auth_token and not is_auth:
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        body = json.dumps({"ok": False, "error": "Unauthorized: Yêu cầu WEB_AUTH_TOKEN"}).encode("utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    with parent._condition:
                        state = dict(parent._state)
                    hw = get_system_metrics()

                    # Ưu tiên cổng USB cắm trực tiếp trên Pi; nếu không có thì nhận cầu nối từ PC
                    esp_info = {"connected": False, "port": "", "latest_telemetry": "", "reset_count": 0}
                    if parent.esp and parent.esp.connected:
                        esp_info["connected"] = True
                        esp_info["port"] = parent.esp.port or ""
                        esp_info["latest_telemetry"] = parent.esp.latest_telemetry or ""
                        esp_info["reset_count"] = parent.esp.reset_count
                    elif (time.time() - parent._remote_esp_ts) < 6.0 and parent._remote_esp_info:
                        esp_info.update(parent._remote_esp_info)
                        esp_info["connected"] = True

                    raw = esp_info.get("latest_telemetry") or ""
                    if "{" in raw and "}" in raw:
                        try:
                            json_str = raw[raw.find("{") : raw.rfind("}") + 1]
                            jdata = json.loads(json_str)
                            if "co2_ppm" in jdata:
                                esp_info["co2_ppm"] = float(jdata["co2_ppm"])
                            if "co2_level" in jdata:
                                esp_info["co2_level"] = int(jdata["co2_level"])
                            if "speed_kmh" in jdata:
                                esp_info["speed_kmh"] = float(jdata["speed_kmh"])
                            if "gps_lat" in jdata:
                                esp_info["gps_lat"] = float(jdata["gps_lat"])
                            if "gps_lon" in jdata:
                                esp_info["gps_lon"] = float(jdata["gps_lon"])
                            if hasattr(parent, "gps") and parent.gps and jdata.get("gps_ready") in (1, 1.0, "1.0", "1", True):
                                parent.gps.update_from_telemetry_dict(jdata)
                        except Exception:
                            pass
                    elif "CO2:" in raw:
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

                    gps_snap = None
                    if hasattr(parent, "gps") and parent.gps:
                        gps_snap = parent.gps.get_snapshot()
                    elif parent.cloud_sync and getattr(parent.cloud_sync, "gps_manager", None):
                        gps_snap = parent.cloud_sync.gps_manager.get_snapshot()

                    # Khoảng cách thực tế 3D/2D (mét) giữa Pi và tọa độ mốc máy tính
                    pc_lat = float(parent._pc_anchor_coords.get("lat", 16.068500))
                    pc_lon = float(parent._pc_anchor_coords.get("lon", 108.221500))
                    pc_alt = float(parent._pc_anchor_coords.get("alt", 8.5))
                    pi_lat = float(gps_snap.get("lat") or pc_lat) if gps_snap else pc_lat
                    pi_lon = float(gps_snap.get("lon") or pc_lon) if gps_snap else pc_lon
                    pi_alt = float(gps_snap.get("altitude_m") or pc_alt) if gps_snap else pc_alt

                    from AI.scripts.gps_service import haversine_distance_km
                    dist_2d_m = haversine_distance_km(pi_lat, pi_lon, pc_lat, pc_lon) * 1000.0
                    delta_alt_m = abs(pi_alt - pc_alt)
                    dist_3d_m = (dist_2d_m ** 2 + delta_alt_m ** 2) ** 0.5

                    distance_info = {
                        "distance_meters": round(dist_3d_m, 2),
                        "distance_2d_m": round(dist_2d_m, 2),
                        "delta_alt_m": round(delta_alt_m, 2),
                        "pc_anchor": {
                            "lat": pc_lat,
                            "lon": pc_lon,
                            "alt_m": pc_alt,
                            "name": "Máy tính (Đà Nẵng)"
                        }
                    }

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
                        "thermal_level": state.get("thermal_level", 0),
                        "eye_duration": state.get("eye_duration", 0.0),
                        "mouth_duration": state.get("mouth_duration", 0.0),
                        "distracted_duration": state.get("distracted_duration", 0.0),
                        "head_pose": state.get("head_pose"),
                        "detected_classes": state.get("detected_classes", []),
                        "active_phase": state.get("active_phase", "YOLO_PHASE"),
                        "model_stage": state.get("active_phase", "YOLO_PHASE"),
                        "mp_phase_remaining": state.get("mp_phase_remaining", 0.0),
                        "mp_latency_ms": state.get("mp_latency_ms", 0.0),
                        "recording_mode": state.get("recording_mode", "VIDEO_CLIP_10S"),
                        "clip_recorder": state.get("clip_recorder", {}),
                        "hardware": hw,
                        "esp": esp_info,
                        "gps": gps_snap,
                        "distance": distance_info,
                        "config": cfg,
                        "events": list(parent._events),
                        "sync": parent.cloud_sync.get_sync_stats() if parent.cloud_sync else {},
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
                from urllib.parse import urlparse
                req_path = urlparse(self.path).path

                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                try:
                    payload = json.loads(raw_body)
                except Exception:
                    payload = {}

                if parent.auth_token:
                    payload_tok = str(payload.get("token", "")).strip() if isinstance(payload, dict) else ""
                    if not self._is_authorized() and payload_tok != parent.auth_token:
                        self._json_response({"ok": False, "error": "Unauthorized: Yêu cầu WEB_AUTH_TOKEN"}, 401)
                        return

                if req_path == "/api/control":
                    cmd = payload.get("cmd", "").strip().upper()
                    if not cmd:
                        self._json_response({"ok": False, "error": "Missing cmd parameter"}, 400)
                        return
                    label_map = {
                        "VOICE:1": "DROWSY",
                        "VOICE:2": "YAWNING",
                        "VOICE:3": "DISTRACTED",
                        "VOICE:4": "CO2_WARN",
                        "VOICE:5": "CO2_CRIT",
                    }
                    alert_type = label_map.get(cmd)

                    # Gửi sự kiện vi phạm kiểm thử kèm ảnh camera hiện tại lên máy chủ
                    if alert_type and parent.cloud_sync and hasattr(parent.cloud_sync, "dispatch_violation"):
                        cur_frame = parent._frame if getattr(parent, "_frame", None) is not None else None
                        try:
                            parent.cloud_sync.dispatch_violation(
                                alert_type=alert_type,
                                frame=cur_frame,
                                started_at=time.time() - 2.0,
                                confirmed_at=time.time(),
                                duration_seconds=2.0,
                                confidence=0.99,
                                meta={"test": True, "source": "manual_dashboard_test", "cmd": cmd},
                            )
                        except Exception as e:
                            print(f"[MANUAL TEST VIOLATION] Lỗi gửi vi phạm: {e}")

                    if parent.esp and parent.esp.connected:
                        ok, ack = parent.esp.send_raw_command(cmd, wait_ack=True, timeout=1.5)
                        with parent._condition:
                            parent._events.append({
                                "time": time.strftime("%H:%M:%S"),
                                "alert": alert_type or cmd,
                                "classes": ["manual_test", ack or ("OK" if ok else "NO_ACK")],
                                "cmd": cmd,
                            })
                            if len(parent._events) > 30:
                                parent._events.pop(0)
                        self._json_response({"ok": ok, "ack": ack, "cmd": cmd})
                        return
                    elif (time.time() - parent._remote_esp_ts) < 6.0:
                        with parent._condition:
                            parent._remote_cmd_queue.append(cmd)
                            parent._events.append({
                                "time": time.strftime("%H:%M:%S"),
                                "alert": cmd,
                                "classes": ["remote_pc_com3"],
                                "cmd": cmd,
                            })
                        self._json_response({"ok": True, "ack": "DA_GUI_QUY_TRINH_PC_COM3", "cmd": cmd})
                        return
                    self._json_response({"ok": False, "error": "ESP32 chưa kết nối (trên cả Pi và PC)"}, 503)

                elif req_path == "/api/esp_bridge":
                    # Cầu nối từ Máy tính (COM3 + GPS) đẩy sang Raspberry Pi qua mạng LAN
                    with parent._condition:
                        parent._remote_esp_ts = time.time()
                        parent._remote_esp_info = {
                            "connected": True,
                            "port": payload.get("port", "COM3 (PC -> Pi)"),
                            "latest_telemetry": payload.get("latest_telemetry", ""),
                            "reset_count": int(payload.get("reset_count", 0)),
                            "co2_ppm": payload.get("co2_ppm"),
                            "co2_level": payload.get("co2_level", 0),
                            "speed_kmh": payload.get("speed_kmh", 0.0),
                        }
                        if "pc_lat" in payload and "pc_lon" in payload:
                            parent._pc_anchor_coords["lat"] = float(payload["pc_lat"])
                            parent._pc_anchor_coords["lon"] = float(payload["pc_lon"])
                            if "pc_alt" in payload:
                                parent._pc_anchor_coords["alt"] = float(payload["pc_alt"])
                        pending_cmds = list(parent._remote_cmd_queue)
                        parent._remote_cmd_queue.clear()

                    # Nếu từ PC gửi kèm tọa độ GPS cảm biến hoặc Server XYZ, cập nhật ngay vào GpsManager trên Pi
                    if hasattr(parent, "gps") and parent.gps:
                        if payload.get("gps_lat") and payload.get("gps_lon"):
                            parent.gps.update_from_telemetry_dict(payload)
                        elif payload.get("server_xyz") and isinstance(payload["server_xyz"], dict):
                            xyz = payload["server_xyz"]
                            parent.gps.update_from_server_xyz(
                                x=float(xyz.get("x", 108.2215)),
                                y=float(xyz.get("y", 16.0685)),
                                z=float(xyz.get("z", 8.5)),
                            )
                    self._json_response({"ok": True, "pending_commands": pending_cmds})

                elif req_path == "/api/config":
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
                    if "server_url" in payload and parent.cloud_sync:
                        srv_in = str(payload["server_url"]).strip().rstrip("/")
                        if srv_in:
                            if not (srv_in.startswith("http://") or srv_in.startswith("https://")):
                                srv_in = f"http://{srv_in}"
                            parent.cloud_sync.server_url = srv_in
                            payload["server_url"] = srv_in
                            # Cập nhật bền vững vào file pi.env
                            try:
                                env_file = Path(__file__).resolve().parents[1] / "deploy" / "pi.env"
                                if env_file.exists():
                                    lines = env_file.read_text(encoding="utf-8").splitlines()
                                    new_lines = []
                                    replaced = False
                                    for line in lines:
                                        if line.startswith("REMOTE_SERVER_URL="):
                                             new_lines.append(f"REMOTE_SERVER_URL={srv_in}")
                                             replaced = True
                                        else:
                                             new_lines.append(line)
                                    if not replaced:
                                        new_lines.append(f"REMOTE_SERVER_URL={srv_in}")
                                    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                            except Exception:
                                pass
                    self._json_response({"ok": True, "applied": payload})

                elif req_path == "/api/sync/test":
                    if not parent.cloud_sync:
                        self._json_response({"ok": False, "error": "Chua khoi tao CloudSync"}, 503)
                        return
                    telemetry = parent.cloud_sync.build_telemetry_payload()
                    ok, resp = parent.cloud_sync._http_post("/api/v1/telemetry", telemetry, timeout_s=3.0)
                    self._json_response({
                        "ok": ok,
                        "resp": resp,
                        "server_url": parent.cloud_sync.server_url,
                        "vehicle_id": telemetry.get("vehicle_id"),
                    })

                elif req_path in ("/api/set_pc_location", "/api/gps/inject"):
                    # Tiếp nhận tọa độ định vị từ Trình duyệt / Máy tính để bù GPS khi test phòng lab
                    lat = float(payload.get("lat", payload.get("y", 16.068500)))
                    lon = float(payload.get("lon", payload.get("x", 108.221500)))
                    alt = float(payload.get("alt", payload.get("altitude_m", payload.get("z", 8.5))))
                    spd = float(payload.get("speed_kmh", 0.0))
                    source = str(payload.get("source", "pc_browser"))

                    with parent._condition:
                        parent._pc_anchor_coords["lat"] = lat
                        parent._pc_anchor_coords["lon"] = lon
                        parent._pc_anchor_coords["alt"] = alt

                    # Nếu PC Server gửi kèm server_url hoặc gọi từ mạng Wi-Fi mới, tự động cập nhật địa chỉ Server PC!
                    if parent.cloud_sync and not parent.cloud_sync.server_online:
                        srv_url_hint = str(payload.get("server_url", "")).strip()
                        if not srv_url_hint and self.client_address and self.client_address[0] not in ("127.0.0.1", "::1"):
                            srv_url_hint = f"http://{self.client_address[0]}:5000"
                        if srv_url_hint:
                            parent.cloud_sync.server_url = srv_url_hint.rstrip("/")

                    injected = False
                    if hasattr(parent, "gps") and parent.gps:
                        injected = parent.gps.inject_server_xyz(lat=lat, lon=lon, alt_m=alt, speed_kmh=spd)
                    elif parent.cloud_sync and getattr(parent.cloud_sync, "gps_manager", None):
                        injected = parent.cloud_sync.gps_manager.inject_server_xyz(lat=lat, lon=lon, alt_m=alt, speed_kmh=spd)

                    self._json_response({
                        "ok": True,
                        "message": "Đã cập nhật tọa độ bù từ Máy tính / Trình duyệt thành công",
                        "injected_into_gps": injected,
                        "coords": {"lat": lat, "lon": lon, "alt": alt, "source": source}
                    })
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


if __name__ == "__main__":
    import argparse
    import sys
    import numpy as np

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Chạy Dashboard Xe cục bộ (Pi Web Server)")
    parser.add_argument("--port", type=int, default=8080, help="Cổng web (mặc định 8080)")
    parser.add_argument("--server", type=str, default="http://127.0.0.1:5000", help="URL máy chủ trung tâm (mặc định http://127.0.0.1:5000)")
    parser.add_argument("--token", type=str, default=None, help="Mã xác thực WEB_AUTH_TOKEN (mặc định đọc từ env)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    for p in (str(repo_root), str(repo_root / "AI"), str(repo_root / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)

    from AI.scripts.cloud_sync import CloudSyncManager
    from AI.scripts.gps_service import GpsManager

    gps = GpsManager(simulation_mode=False)
    cloud_sync = CloudSyncManager(server_url=args.server, enabled=True, telemetry_interval=2.0, gps_manager=gps)

    web = DriverMonitorWebServer(port=args.port, auth_token=args.token)
    web.set_context(cloud_sync=cloud_sync, gps=gps)

    print("=" * 70)
    print(f"  [XE] BẢNG ĐIỀU KHIỂN & GIÁM SÁT XE ĐANG CHẠY TẠI:")
    print(f"  Web Dashboard Xe   : http://localhost:{args.port}")
    print(f"  Máy chủ trung tâm  : {args.server}")
    print(f"  Trạng thái         : Nhận tọa độ XYZ & đồng bộ 5 nhóm cảm biến")
    print("=" * 70)

    # Khung hình mô phỏng video stream nếu chưa gắn cam thật
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    try:
        while True:
            vis = dummy_frame.copy()
            cv2.rectangle(vis, (20, 20), (620, 460), (35, 49, 77), 2)
            cv2.putText(vis, "AI DRIVER MONITOR - LIVE SIMULATION", (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (56, 189, 248), 2)
            cv2.putText(vis, f"TIME: {time.strftime('%H:%M:%S')}", (40, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            snap = gps.get_snapshot()
            src_str = "SERVER_XYZ (MÁY CHỦ)" if snap.get("source") == "server_xyz" else "SATELLITE"
            cv2.putText(vis, f"GPS 3D: X={snap.get('x', 0):.4f}, Y={snap.get('y', 0):.4f}, Z={snap.get('z', 0):.1f}m", (40, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (34, 197, 94), 2)
            cv2.putText(vis, f"NGUON: {src_str} | SPD: {snap.get('speed_kmh', 0):.1f} km/h", (40, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (245, 158, 11), 2)

            state = {
                "confirmed_alert": "NORMAL",
                "ai_fps": 25.0,
                "pipeline_latency_ms": 14.0,
                "eye_duration": 0.0,
                "mouth_duration": 0.0,
                "distracted_duration": 0.0,
                "detected_classes": ["driver_present"],
                "head_pose": {"pitch": 1.5, "yaw": -0.8, "roll": 0.2},
            }
            web.update_frame(vis, state)
            cloud_sync.update_state(state)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[XE] Đang dừng hệ thống...")
    finally:
        web.close()
        cloud_sync.close()
        print("[XE] Đã giải phóng cổng mạng thành công.")
