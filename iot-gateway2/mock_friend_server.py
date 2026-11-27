"""Máy chủ trung tâm mô phỏng (Server Bạn Tôi) để nhận và kiểm tra dữ liệu kết nối từ Máy Chủ Xe (FEAT-MOCK-SERVER).

Chạy độc lập trên PC hoặc Cloud Server:
    python AI/scripts/mock_friend_server.py --port 5000

Cung cấp:
- Endpoint POST /api/v1/telemetry : Tiếp nhận thông tin định kỳ (ESP, Cam, IP, GPS, MQ-135, Loa, Phần cứng)
- Endpoint POST /api/v1/violation : Tiếp nhận sự kiện vi phạm thời gian thực kèm ảnh bằng chứng JPEG Base64
- Endpoint GET /api/v1/telemetry  : Trả về trạng thái các xe đang kết nối (JSON)
- Endpoint GET /api/v1/violations : Trả về lịch sử các vi phạm đã nhận (JSON)
- Giao diện Web Trung Tâm Điều Hành tại http://localhost:5000 trực quan, hiện đại, tự động làm mới!
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Nạp module GPS và tính khoảng cách thực địa
try:
    from AI.scripts.gps_service import GpsManager, haversine_distance_km
except ImportError:
    try:
        from gps_service import GpsManager, haversine_distance_km
    except ImportError:
        GpsManager = None
        haversine_distance_km = None

# Bộ nhớ lưu tạm trên server bạn
CONNECTED_VEHICLES: Dict[str, Dict[str, Any]] = {}
VIOLATION_HISTORY: List[Dict[str, Any]] = []
SERVER_LOCK = threading.RLock()

# Tọa độ XYZ Máy Chủ quản lý để phát xuống cho xe (Server-Assisted XYZ)
# Tự động đồng bộ vị trí địa lý thực tế của máy tính người dùng tại Việt Nam
ACTIVE_DOWNLINK_XYZ: Dict[str, Any] = {
    "lat": 16.068500,
    "lon": 108.221500,
    "alt_m": 8.5,
    "speed_kmh": 0.0,
    "source": "server_xyz",
    "location_name": "Da Nang (Vi tri thuc may tinh)",
}

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trung Tâm Quản Lý Xe & Vi Phạm (Server Bạn Tôi)</title>
<style>
  :root {
    --bg: #090d16;
    --card: #131b2e;
    --border: #1f2d4a;
    --text: #f8fafc;
    --muted: #94a3b8;
    --primary: #38bdf8;
    --green: #22c55e;
    --amber: #f59e0b;
    --red: #ef4444;
    --purple: #c084fc;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 20px;
    font-size: 14px;
    line-height: 1.5;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 20px;
    flex-wrap: wrap;
    gap: 12px;
  }
  .title-group { display: flex; align-items: center; gap: 12px; }
  .pulse {
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--green); box-shadow: 0 0 10px var(--green);
    animation: blink 1.8s infinite;
  }
  @keyframes blink {
    0%, 100% { opacity: 0.8; transform: scale(0.95); }
    50% { opacity: 1; transform: scale(1.15); }
  }
  h1 { font-size: 22px; font-weight: 700; color: #fff; }
  .badge {
    padding: 3px 8px; border-radius: 6px; font-size: 11px;
    font-weight: 700; text-transform: uppercase;
  }
  .badge-online { background: rgba(34,197,94,0.18); color: var(--green); border: 1px solid var(--green); }
  .badge-danger { background: rgba(239,68,68,0.2); color: var(--red); border: 1px solid var(--red); }
  .badge-warn { background: rgba(245,158,11,0.2); color: var(--amber); border: 1px solid var(--amber); }

  .grid-layout {
    display: grid;
    grid-template-columns: 1fr 1.3fr;
    gap: 20px;
  }
  @media (max-width: 1100px) {
    .grid-layout { grid-template-columns: 1fr; }
  }

  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px;
    margin-bottom: 20px;
  }
  .card-header {
    font-size: 14px; font-weight: 700; text-transform: uppercase;
    color: var(--primary); margin-bottom: 14px; display: flex;
    justify-content: space-between; align-items: center;
  }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 10px;
    margin-bottom: 16px;
  }
  .stat-box {
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px;
  }
  .stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; }
  .stat-val { font-size: 17px; font-weight: 700; color: #fff; margin-top: 4px; }

  .btn {
    display: inline-flex; align-items: center; justify-content: center;
    background: #0284c7; color: #fff; text-decoration: none;
    padding: 8px 14px; border-radius: 6px; font-size: 12px;
    font-weight: 600; border: none; cursor: pointer; transition: 0.15s;
    gap: 6px;
  }
  .btn:hover { background: #0369a1; }
  .btn-outline {
    background: transparent; border: 1px solid var(--border); color: var(--text);
  }
  .btn-outline:hover { background: rgba(255,255,255,0.08); }

  .table-scroll { overflow-x: auto; max-height: 480px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { padding: 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); background: rgba(0,0,0,0.2); position: sticky; top: 0; }
  tr:hover { background: rgba(255,255,255,0.02); }

  .evidence-thumb {
    width: 68px; height: 48px; object-fit: cover; border-radius: 4px;
    cursor: pointer; border: 1px solid var(--border);
  }
  .evidence-thumb:hover { transform: scale(1.08); border-color: var(--primary); }

  /* Modal xem ảnh phóng to */
  .modal-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.85);
    display: none; justify-content: center; align-items: center; z-index: 999;
  }
  .modal-content {
    background: var(--card); border: 1px solid var(--border);
    padding: 16px; border-radius: 10px; max-width: 90vw; max-height: 90vh;
    text-align: center;
  }
  .modal-img { max-width: 100%; max-height: 75vh; border-radius: 6px; }
  .modal-close {
    margin-top: 10px; background: #334155; color: #fff; border: none;
    padding: 6px 16px; border-radius: 4px; cursor: pointer;
  }
</style>
</head>
<body>

<header>
  <div class="title-group">
    <div class="pulse"></div>
    <div>
      <h1>HỆ THỐNG GIÁM SÁT TRUNG TÂM (SERVER BẠN TÔI)</h1>
      <small style="color:var(--muted)">Nhận dữ liệu từ máy chủ xe: ESP, Cam, Địa chỉ truy cập, GPS, MQ-135, Loa, Vi phạm</small>
    </div>
  </div>
  <div style="display:flex; gap:10px; align-items:center;">
    <span id="server-status" class="badge badge-online">MÁY CHỦ SẴN SÀNG</span>
    <span id="clock" style="color:var(--muted); font-family:monospace;">--:--:--</span>
  </div>
</header>

<div class="grid-layout">
  <!-- CỘT TRÁI: DANH SÁCH XE & TRẠNG THÁI THỜI GIAN THỰC -->
  <div>
    <div class="card">
      <div class="card-header">
        <span>Danh Sách Xe Trực Tuyến (<span id="count-vehicles">0</span>)</span>
        <button class="btn btn-outline" onclick="loadData()">Làm Mới</button>
      </div>
      <div id="vehicle-container">
        <p style="color:var(--muted); text-align:center; padding:20px;">Đang đợi máy chủ xe (bên bạn) gửi tín hiệu...</p>
      </div>
    </div>

    <!-- CHI TIẾT THÔNG SỐ XE ĐANG CHỌN -->
    <div class="card" id="detail-card" style="display:none;">
      <div class="card-header">
        <span id="detail-vehicle-name">THÔNG SỐ XE: XE-01</span>
        <span id="detail-updated" class="badge badge-online">Vừa cập nhật</span>
      </div>

      <!-- ĐỊA CHỈ TRUY CẬP XEM CAMERA TRỰC TIẾP -->
      <div style="background:rgba(56,189,248,0.08); border:1px solid rgba(56,189,248,0.3); border-radius:8px; padding:12px; margin-bottom:16px;">
        <div style="font-weight:700; color:var(--primary); margin-bottom:6px;">ĐỊA CHỈ TRUY CẬP XEM CAMERA & BẢNG ĐIỀU KHIỂN TẠI XE:</div>
        <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:8px;">
          <span style="color:var(--muted)">IP Nội Bộ:</span>
          <code id="access-ip" style="background:#000; padding:2px 8px; border-radius:4px; color:#fff;">--</code>
          <a id="link-stream" href="#" target="_blank" class="btn">Xem Trực Tiếp Video Stream</a>
          <a id="link-dash" href="#" target="_blank" class="btn btn-outline">Mở Web Dashboard Xe</a>
        </div>
        <div id="box-tunnel" style="display:none; font-size:12px;">
          <span style="color:var(--muted)">Public Tunnel:</span> <a id="link-tunnel" href="#" target="_blank" style="color:var(--primary);">--</a>
        </div>
      </div>

      <!-- CÁC CHỈ SỐ ESP, GPS, MQ-135, LOA -->
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-label">ESP32 UART</div>
          <div id="stat-esp" class="stat-val" style="color:var(--green)">OK</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Camera FPS</div>
          <div id="stat-cam-fps" class="stat-val">--</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Tốc Độ GPS</div>
          <div id="stat-speed" class="stat-val" style="color:var(--primary)">0.0 km/h</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Cảm Biến MQ-135 / CO2</div>
          <div id="stat-mq135" class="stat-val">-- ppm</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Mức Khí Độc MQ-135</div>
          <div id="stat-mq135-lv" class="stat-val" style="font-size:14px;">An toàn</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Trạng Thái Loa</div>
          <div id="stat-speaker" class="stat-val" style="font-size:12px;">STOP</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Nhiệt Độ SoC</div>
          <div id="stat-temp" class="stat-val">-- °C</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">CPU / RAM</div>
          <div id="stat-res" class="stat-val" style="font-size:13px;">-- % / -- MB</div>
        </div>
      </div>

      <!-- VỊ TRÍ TỌA ĐỘ GPS -->
      <div style="background:rgba(0,0,0,0.2); border:1px solid var(--border); border-radius:8px; padding:10px; font-size:12px; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <b>Tọa Độ GPS:</b> <span id="stat-gps-coords">-- , --</span>
          <span style="color:var(--muted); margin-left:10px;">(Vệ tinh: <span id="stat-sats">0</span>)</span>
        </div>
        <a id="link-gmaps" href="#" target="_blank" class="btn btn-outline" style="padding:4px 8px; font-size:11px;">Mở Google Maps</a>
      </div>
    </div>

    <!-- ĐỊNH VỊ TỌA ĐỘ XYZ TỪ MÁY CHỦ CHO XE -->
    <div class="card" style="border: 1px solid rgba(56,189,248,0.4); background: rgba(56,189,248,0.03);">
      <div class="card-header" style="color:var(--primary);">
        <span>🛰️ TRẠM ĐỊNH VỊ XYZ TỪ MÁY CHỦ (SERVER-ASSISTED GPS)</span>
        <span class="badge badge-online">Giải phóng vệ tinh</span>
      </div>
      <p style="color:var(--muted); font-size:12px; margin-bottom:12px;">
        Máy chủ chủ động xác định &amp; phát tọa độ không gian 3 chiều (X: Kinh độ, Y: Vĩ độ, Z: Cao độ) xuống cho xe nhận ngay lập tức, phục vụ thiết kế &amp; vận hành mà không cần chờ bắt sóng vệ tinh ngoài trời.
      </p>
      <div style="display:grid; grid-template-columns: repeat(2, 1fr); gap:10px; margin-bottom:10px;">
        <div>
          <label style="font-size:11px; color:var(--muted); display:block; margin-bottom:3px;">Y (Vĩ độ Lat):</label>
          <input id="inp-lat" type="number" step="0.0001" value="16.0685" style="width:100%; padding:6px 8px; background:#0b111e; border:1px solid var(--border); color:#fff; border-radius:6px; font-size:12px;">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted); display:block; margin-bottom:3px;">X (Kinh độ Lon):</label>
          <input id="inp-lon" type="number" step="0.0001" value="108.2215" style="width:100%; padding:6px 8px; background:#0b111e; border:1px solid var(--border); color:#fff; border-radius:6px; font-size:12px;">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted); display:block; margin-bottom:3px;">Z (Cao độ Alt mét):</label>
          <input id="inp-alt" type="number" step="0.5" value="8.5" style="width:100%; padding:6px 8px; background:#0b111e; border:1px solid var(--border); color:#fff; border-radius:6px; font-size:12px;">
        </div>
        <div>
          <label style="font-size:11px; color:var(--muted); display:block; margin-bottom:3px;">Vận tốc ước tính (km/h):</label>
          <input id="inp-speed" type="number" step="1" value="0.0" style="width:100%; padding:6px 8px; background:#0b111e; border:1px solid var(--border); color:#fff; border-radius:6px; font-size:12px;">
        </div>
      </div>
      <div style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px;">
        <button class="btn" style="background:#059669; padding:4px 8px; font-size:11px; font-weight:700;" onclick="setPreset(16.0685, 108.2215, 8.5, 0)">📍 Vị Trí Máy Này (Đà Nẵng)</button>
        <button class="btn btn-outline" style="padding:4px 8px; font-size:11px;" onclick="setPreset(10.7725, 106.7012, 12.0, 35)">TP.HCM</button>
        <button class="btn btn-outline" style="padding:4px 8px; font-size:11px;" onclick="setPreset(21.0285, 105.8542, 18.0, 40)">Hà Nội</button>
        <button class="btn btn-outline" style="padding:4px 8px; font-size:11px;" onclick="setPreset(11.9404, 108.4583, 1500.0, 28)">Đèo Đà Lạt (Alt 1500m)</button>
      </div>
      <button class="btn" style="width:100%; padding:9px; font-size:13px; font-weight:700;" onclick="sendServerXYZ()">🛰️ Phát Tọa Độ XYZ Xuống Xe Ngay</button>
      <div id="xyz-notify" style="display:none; margin-top:8px; font-size:12px; color:var(--green); text-align:center;"></div>
    </div>
  </div>

  <!-- CỘT PHẢI: LỊCH SỬ CẢNH BÁO & BẰNG CHỨNG VI PHẠM THỜI GIAN THỰC -->
  <div>
    <div class="card">
      <div class="card-header">
        <span>Nhật Ký & Bằng Chứng Vi Phạm (Live Violation Feed)</span>
        <span id="count-violations" class="badge badge-danger">0 vụ vi phạm</span>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Ảnh bằng chứng</th>
              <th>Thời gian</th>
              <th>Xe / Tài xế</th>
              <th>Loại vi phạm</th>
              <th>Thời lượng</th>
              <th>Tốc độ / GPS</th>
              <th>Loa đã phát</th>
            </tr>
          </thead>
          <tbody id="violation-tbody">
            <tr><td colspan="7" style="text-align:center; color:var(--muted); padding:30px;">Chưa nhận được vi phạm nào.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- Modal xem ảnh lớn -->
<div id="modal-box" class="modal-overlay" onclick="closeModal()">
  <div class="modal-content" onclick="event.stopPropagation()">
    <h3 id="modal-title" style="margin-bottom:10px; color:#fff;">Ảnh Bằng Chứng Vi Phạm</h3>
    <img id="modal-img" class="modal-img" src="" alt="Bằng chứng">
    <div style="margin-top:10px;">
      <button class="modal-close" onclick="closeModal()">Đóng Cửa Sổ</button>
    </div>
  </div>
</div>

<script>
  let selectedVehicleId = null;

  function updateClock() {
    document.getElementById('clock').innerText = new Date().toLocaleTimeString('vi-VN');
  }
  setInterval(updateClock, 1000);
  updateClock();

  function openModal(src, title) {
    document.getElementById('modal-img').src = src;
    document.getElementById('modal-title').innerText = title || 'Ảnh Bằng Chứng Vi Phạm';
    document.getElementById('modal-box').style.display = 'flex';
  }

  function closeModal() {
    document.getElementById('modal-box').style.display = 'none';
  }

  async function loadData() {
    try {
      const res = await fetch('/api/v1/telemetry');
      if (!res.ok) return;
      const vehicles = await res.json();
      renderVehicles(vehicles);

      const vRes = await fetch('/api/v1/violations');
      if (vRes.ok) {
        const violations = await vRes.json();
        renderViolations(violations);
      }
    } catch (e) {
      console.warn("Lỗi tải dữ liệu máy chủ:", e);
    }
  }

  function renderVehicles(vehicles) {
    const vIds = Object.keys(vehicles);
    document.getElementById('count-vehicles').innerText = vIds.length;
    const container = document.getElementById('vehicle-container');

    if (vIds.length === 0) {
      container.innerHTML = '<p style="color:var(--muted); text-align:center; padding:20px;">Đang đợi máy chủ xe (bên bạn) gửi tín hiệu...</p>';
      document.getElementById('detail-card').style.display = 'none';
      return;
    }

    if (!selectedVehicleId || !vehicles[selectedVehicleId]) {
      selectedVehicleId = vIds[0];
    }

    let html = '';
    vIds.forEach(id => {
      const v = vehicles[id];
      const isSelected = (id === selectedVehicleId);
      const isOnline = (v.camera?.status === 'ONLINE' || v.esp?.connected);
      const alert = v.driver_state?.alert || 'NORMAL';
      const isAlerting = alert !== 'NORMAL' && alert !== 'UNKNOWN';

      html += `
        <div onclick="selectVehicle('${id}')" style="background:${isSelected ? 'rgba(56,189,248,0.12)' : 'rgba(255,255,255,0.02)'}; border:1px solid ${isSelected ? 'var(--primary)' : 'var(--border)'}; border-radius:8px; padding:12px; margin-bottom:10px; cursor:pointer; display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div style="font-weight:700; font-size:15px; color:#fff;">${v.vehicle_name || id} (${id})</div>
            <div style="font-size:12px; color:var(--muted);">Tài xế: <b>${v.driver_name || '--'}</b> | IP: ${v.access_info?.local_ip || '--'}</div>
          </div>
          <div style="text-align:right;">
            <span class="badge ${isAlerting ? 'badge-danger' : isOnline ? 'badge-online' : 'badge-warn'}">${isAlerting ? alert : isOnline ? 'ONLINE' : 'CHẬM KẾT NỐI'}</span>
            <div style="font-size:11px; color:var(--muted); margin-top:4px;">Cập nhật: ${v.timestamp ? v.timestamp.split('T')[1].split('+')[0] : '--'}</div>
          </div>
        </div>
      `;
    });
    container.innerHTML = html;

    // Cập nhật chi tiết xe
    if (selectedVehicleId && vehicles[selectedVehicleId]) {
      renderVehicleDetail(vehicles[selectedVehicleId]);
    }
  }

  function selectVehicle(id) {
    selectedVehicleId = id;
    loadData();
  }

  function renderVehicleDetail(v) {
    const card = document.getElementById('detail-card');
    card.style.display = 'block';

    document.getElementById('detail-vehicle-name').innerText = `THÔNG SỐ: ${v.vehicle_name || v.vehicle_id} (${v.vehicle_id})`;
    document.getElementById('access-ip').innerText = v.access_info?.local_ip || '--';

    const streamUrl = v.access_info?.stream_url || '#';
    const dashUrl = v.access_info?.dashboard_url || '#';
    document.getElementById('link-stream').href = streamUrl;
    document.getElementById('link-dash').href = dashUrl;

    if (v.access_info?.public_tunnel_url) {
      document.getElementById('box-tunnel').style.display = 'block';
      const linkTun = document.getElementById('link-tunnel');
      linkTun.href = v.access_info.public_tunnel_url;
      linkTun.innerText = v.access_info.public_tunnel_url;
    } else {
      document.getElementById('box-tunnel').style.display = 'none';
    }

    // ESP
    const espElem = document.getElementById('stat-esp');
    const espOk = v.esp?.connected;
    espElem.innerText = espOk ? `OK (${v.esp.port || 'UART'})` : 'NGẮT KẾT NỐI';
    espElem.style.color = espOk ? 'var(--green)' : 'var(--red)';

    // Camera
    document.getElementById('stat-cam-fps').innerText = (v.camera?.fps || 0) + ' FPS';

    // GPS & Tốc độ
    const speed = (v.gps?.speed_kmh || 0).toFixed(1);
    document.getElementById('stat-speed').innerText = speed + ' km/h';

    const lat = v.gps?.lat || 0;
    const lon = v.gps?.lon || 0;
    const alt = v.gps?.alt_m !== undefined ? v.gps.alt_m : (v.gps?.altitude_m || 0);
    const src = v.gps?.source || (v.gps?.status === 'SERVER_XYZ' ? 'server_xyz' : 'satellite');
    const isServerAssisted = (src === 'server_xyz' || v.gps?.satellites === 99 || v.gps?.status === 'SERVER_XYZ');
    const srcTag = isServerAssisted ? '🛰️ SERVER-ASSISTED' : '🛰️ VỆ TINH GNSS';
    document.getElementById('stat-gps-coords').innerHTML = `<b>X (Lon):</b> ${lon.toFixed(6)} | <b>Y (Lat):</b> ${lat.toFixed(6)} | <b>Z (Alt):</b> ${Number(alt).toFixed(1)}m <span class="badge ${isServerAssisted ? 'badge-online' : 'badge-warn'}" style="margin-left:6px;">${srcTag}</span>`;
    document.getElementById('stat-sats').innerText = isServerAssisted ? 'Server' : (v.gps?.satellites || 0);
    document.getElementById('link-gmaps').href = `https://www.google.com/maps?q=${lat},${lon}`;

    // MQ-135
    document.getElementById('stat-mq135').innerText = Math.round(v.mq135?.co2_ppm || 0) + ' ppm';
    const mqLevel = v.mq135?.gas_level || 0;
    const mqLvElem = document.getElementById('stat-mq135-lv');
    mqLvElem.innerText = v.mq135?.status_label || (mqLevel === 2 ? 'Nguy hiểm' : mqLevel === 1 ? 'Cảnh báo' : 'An toàn');
    mqLvElem.style.color = mqLevel === 2 ? 'var(--red)' : mqLevel === 1 ? 'var(--amber)' : 'var(--green)';

    // Loa
    document.getElementById('stat-speaker').innerText = v.speaker?.current_voice || 'STOP';

    // Nhiệt độ & Phần cứng
    document.getElementById('stat-temp').innerText = (v.hardware?.temp_c ? v.hardware.temp_c + ' °C' : '--');
    document.getElementById('stat-res').innerText = `${(v.hardware?.cpu_percent || 0).toFixed(1)}% / ${Math.round(v.hardware?.ram_mb || 0)} MB`;
  }

  function renderViolations(violations) {
    document.getElementById('count-violations').innerText = violations.length + ' vụ vi phạm';
    const tbody = document.getElementById('violation-tbody');
    if (!violations || violations.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--muted); padding:30px;">Chưa nhận được vi phạm nào.</td></tr>';
      return;
    }

    let html = '';
    // Đảo ngược để sự kiện mới nhất lên đầu
    [...violations].reverse().forEach(evt => {
      const imgTag = evt.evidence_image
        ? `<img class="evidence-thumb" src="${evt.evidence_image}" alt="Vi phạm" onclick="openModal('${evt.evidence_image}', '${evt.alert_type} - ${evt.vehicle_id}')">`
        : '<span style="color:var(--muted)">Không ảnh</span>';

      const timeStr = evt.confirmed_at ? evt.confirmed_at.replace('T', ' ').split('+')[0] : '--';
      const gpsStr = evt.gps ? `${evt.gps.speed_kmh} km/h (${evt.gps.lat.toFixed(4)}, ${evt.gps.lon.toFixed(4)})` : '--';

      html += `
        <tr>
          <td>${imgTag}</td>
          <td><b>${timeStr}</b></td>
          <td><b>${evt.vehicle_id}</b><br><small style="color:var(--muted)">${evt.driver_name || ''}</small></td>
          <td><span class="badge badge-danger">${evt.alert_type}</span></td>
          <td>${evt.duration_seconds}s</td>
          <td>${gpsStr}</td>
          <td><code style="color:var(--purple)">${evt.speaker_alert || '--'}</code></td>
        </tr>
      `;
    });
    tbody.innerHTML = html;
  }

  function setPreset(lat, lon, alt, spd) {
    document.getElementById('inp-lat').value = lat;
    document.getElementById('inp-lon').value = lon;
    document.getElementById('inp-alt').value = alt;
    document.getElementById('inp-speed').value = spd;
  }

  async function sendServerXYZ() {
    const lat = parseFloat(document.getElementById('inp-lat').value) || 0;
    const lon = parseFloat(document.getElementById('inp-lon').value) || 0;
    const alt = parseFloat(document.getElementById('inp-alt').value) || 0;
    const spd = parseFloat(document.getElementById('inp-speed').value) || 0;
    try {
      const res = await fetch('/api/v1/geo/inject_xyz', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({lat: lat, lon: lon, alt_m: alt, speed_kmh: spd})
      });
      const data = await res.json();
      const n = document.getElementById('xyz-notify');
      n.style.display = 'block';
      n.innerText = `✓ Đã cập nhật tọa độ XYZ! Xe sẽ nhận được ở nhịp Heartbeat tiếp theo: X(Lon)=${lon}, Y(Lat)=${lat}, Z(Alt)=${alt}m`;
      setTimeout(() => { n.style.display = 'none'; }, 4000);
    } catch(e) {
      alert('Lỗi cập nhật XYZ: ' + e);
    }
  }

  // Tự động kéo dữ liệu mỗi 1.5 giây
  setInterval(loadData, 1500);
  loadData();
</script>
</body>
</html>
"""


class FriendServerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Tắt log spam HTTP request

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def _send_json(self, data: Any, code: int = 200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/dashboard"):
            html = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif self.path == "/api/v1/telemetry":
            with SERVER_LOCK:
                data = dict(CONNECTED_VEHICLES)
            self._send_json(data)

        elif self.path == "/api/v1/violations":
            with SERVER_LOCK:
                data = list(VIOLATION_HISTORY)
            self._send_json(data)

        elif self.path in ("/api/v1/geo/xyz", "/api/v1/geo"):
            with SERVER_LOCK:
                data = dict(ACTIVE_DOWNLINK_XYZ)
            self._send_json(data)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}

        if self.path == "/api/v1/geo/inject_xyz":
            with SERVER_LOCK:
                if "lat" in payload:
                    ACTIVE_DOWNLINK_XYZ["lat"] = float(payload["lat"])
                if "lon" in payload:
                    ACTIVE_DOWNLINK_XYZ["lon"] = float(payload["lon"])
                if "alt_m" in payload or "alt" in payload:
                    ACTIVE_DOWNLINK_XYZ["alt_m"] = float(payload.get("alt_m", payload.get("alt", 0.0)))
                if "speed_kmh" in payload or "speed" in payload:
                    ACTIVE_DOWNLINK_XYZ["speed_kmh"] = float(payload.get("speed_kmh", payload.get("speed", 0.0)))
                ACTIVE_DOWNLINK_XYZ["source"] = "server_xyz"
                res_xyz = dict(ACTIVE_DOWNLINK_XYZ)
            self._send_json({"ok": True, "ack": "XYZ_INJECTED", "downlink_xyz": res_xyz})
            print(f"[SERVER BAN TOI] Da cap nhat toa do Downlink XYZ: X(Lon)={res_xyz['lon']}, Y(Lat)={res_xyz['lat']}, Z(Alt)={res_xyz['alt_m']}m, Spd={res_xyz['speed_kmh']}km/h")
            return

        elif self.path == "/api/v1/telemetry":
            vid = payload.get("vehicle_id") or payload.get("device_id") or "XE-UNKNOWN"
            if "vehicle_id" not in payload:
                payload["vehicle_id"] = vid
            if "vehicle_name" not in payload:
                payload["vehicle_name"] = f"Thiết bị {vid}"

            # Tự động tính khoảng cách mốc giữa Pi và Máy Chủ
            dist_info = ""
            pi_gps = payload.get("gps", {})
            pi_lat = float(pi_gps.get("lat", 0.0))
            pi_lon = float(pi_gps.get("lon", 0.0))
            anchor_lat = float(ACTIVE_DOWNLINK_XYZ.get("lat", 16.0685))
            anchor_lon = float(ACTIVE_DOWNLINK_XYZ.get("lon", 108.2215))

            if haversine_distance_km and pi_lat != 0.0 and pi_lon != 0.0:
                dist_km = haversine_distance_km(pi_lat, pi_lon, anchor_lat, anchor_lon)
                payload["distance_to_anchor_km"] = round(dist_km, 3)
                dist_info = f" | Lệch mốc GPS: {dist_km:.2f} km"

            with SERVER_LOCK:
                CONNECTED_VEHICLES[vid] = payload
                downlink_xyz = dict(ACTIVE_DOWNLINK_XYZ)
            self._send_json({"ok": True, "ack": "TELEMETRY_RECEIVED", "vehicle_id": vid, "downlink_xyz": downlink_xyz})
            print(f"[SERVER BAN TOI] Nhan dien xe [{vid}] | IP: {payload.get('access_info',{}).get('local_ip', '--')}{dist_info} | Moc May Chu: {ACTIVE_DOWNLINK_XYZ.get('location_name', 'Da Nang')}")

        elif self.path == "/api/v1/violation":
            vid = payload.get("vehicle_id") or payload.get("device_id") or "XE-UNKNOWN"
            rule = payload.get("alert_type", "VIOLATION")
            has_img = bool(payload.get("evidence_image"))
            with SERVER_LOCK:
                VIOLATION_HISTORY.append(payload)
                if len(VIOLATION_HISTORY) > 200:
                    VIOLATION_HISTORY.pop(0)
            self._send_json({"ok": True, "ack": "VIOLATION_LOGGED", "event_id": payload.get("event_id")})
            print(f"[SERVER BAN TOI] !!! CANH BAO VI PHAM !!! Tu xe [{vid}]: {rule} | Thoi luong: {payload.get('duration_seconds')}s | Anh bang chung: {'CO (Base64)' if has_img else 'KHONG'}")

        else:
            self.send_response(404)
            self.end_headers()


def _start_server_gps_anchor():
    """Tự động khởi động luồng đọc cảm biến GPS cắm tại máy chủ làm mốc chuẩn (Hardware Anchor)."""
    if GpsManager is None:
        return

    def _anchor_loop():
        # Khởi tạo GpsManager tự động dò tìm cảm biến GPS trên các cổng COM/USB
        anchor_mgr = GpsManager(simulation_mode=False, enable_direct_usb=True)
        while True:
            time.sleep(2.0)
            snap = anchor_mgr.get_snapshot()
            if snap.get("is_valid") and snap.get("source") == "satellite" and snap.get("satellites", 0) >= 3:
                with SERVER_LOCK:
                    ACTIVE_DOWNLINK_XYZ["lat"] = snap["lat"]
                    ACTIVE_DOWNLINK_XYZ["lon"] = snap["lon"]
                    ACTIVE_DOWNLINK_XYZ["alt_m"] = snap["altitude_m"]
                    ACTIVE_DOWNLINK_XYZ["speed_kmh"] = snap["speed_kmh"]
                    ACTIVE_DOWNLINK_XYZ["source"] = "hardware_gps_anchor"
                    ACTIVE_DOWNLINK_XYZ["satellites"] = snap["satellites"]
                    ACTIVE_DOWNLINK_XYZ["location_name"] = f"Cảm biến GPS phần cứng ({snap['satellites']} vệ tinh)"

    t = threading.Thread(target=_anchor_loop, name="server-gps-anchor", daemon=True)
    t.start()


def run_server(port: int = 5000):
    _start_server_gps_anchor()
    server = ThreadingHTTPServer(("0.0.0.0", port), FriendServerHandler)
    print("=" * 70)
    print(f"  MAY CHU TRUNG TAM (SERVER BAN TOI) DANG CHAY TAI:")
    print(f"  Web Dashboard : http://localhost:{port}")
    print(f"  API Telemetry : http://localhost:{port}/api/v1/telemetry (POST)")
    print(f"  API Violation : http://localhost:{port}/api/v1/violation (POST)")
    print(f"  Mốc định vị   : {ACTIVE_DOWNLINK_XYZ.get('location_name')} ({ACTIVE_DOWNLINK_XYZ.get('lat')}, {ACTIVE_DOWNLINK_XYZ.get('lon')})")
    print("=" * 70)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDang dung server...")
        server.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock Server Bạn Tôi")
    parser.add_argument("--port", type=int, default=5000, help="Cổng lắng nghe (mặc định 5000)")
    args = parser.parse_args()
    run_server(args.port)
