let TOKEN = sessionStorage.getItem("fleet_token") || "";
let ROLE = "";
let CURRENT_REVIEW_EVENT_ID = "";
let REVIEW_REQ_SEQ = 0;
let STORYBOARD_FRAMES = [];
let TIER2_IS_REAL_CNN = false;
let TIER2_MODE = "HEURISTIC_BASELINE";
let TIER2_MODEL_NAME = "temporal-baseline-v1";
let _REFRESH_TIMER = null;
let _SSE_ES = null;
let _SSE_RETRY_MS = 3000;
let LATEST_POSITIONS = [];
let SELECTED_MAP_VEHICLE = "";
let LATEST_TRACK = null;

function safeParseJson(s) {
  if (!s) return {};
  try {
    const obj = JSON.parse(s);
    return obj && typeof obj === "object" ? obj : {};
  } catch (e) {
    return {};
  }
}

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function safeUrl(u) {
  if (!u) return "";
  const s = String(u).trim();
  if ((s.startsWith("/") && !s.startsWith("//")) || s.startsWith("http://") || s.startsWith("https://")) return s;
  return "";
}

function isPrivateLanStreamUrl(u) {
  const s = safeUrl(u);
  if (!s || (!s.startsWith("http://") && !s.startsWith("https://"))) return false;
  try {
    const parsed = new URL(s);
    if (parsed.username || parsed.password || !parsed.pathname.startsWith("/stream")) return false;
    const h = parsed.hostname.toLowerCase();
    return (
      h === "localhost" ||
      h === "127.0.0.1" ||
      h.startsWith("192.168.") ||
      h.startsWith("10.") ||
      /^172\.(1[6-9]|2\d|3[0-1])\./.test(h)
    );
  } catch (_) {
    return false;
  }
}

function api(path, opts = {}) {
  opts.headers = Object.assign({ "X-Dashboard-Token": TOKEN, "Content-Type": "application/json" }, opts.headers || {});
  opts.credentials = "same-origin";
  return fetch(path, opts).then(async (r) => {
    if (!r.ok) {
      const t = await r.text();
      throw new Error(`${r.status}: ${t}`);
    }
    const ct = r.headers.get("content-type") || "";
    return ct.includes("application/json") ? r.json() : r.text();
  });
}

function toast(title, level = "info") {
  const host = document.getElementById("toast");
  if (!host) return;
  // Cap visible toasts to 3 max so reconnect/spool flushes NEVER cover the dashboard!
  while (host.children.length >= 3) {
    host.firstElementChild.remove();
  }
  const el = document.createElement("div");
  el.className = "toast-item" + (level === "crit" ? " crit" : "");
  el.textContent = title;
  host.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

async function doLogin() {
  const t = document.getElementById("token-input").value.trim();
  const errEl = document.getElementById("login-err");
  errEl.textContent = "";
  if (!t) { errEl.textContent = "Vui lòng nhập token."; return; }
  try {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ token: t }),
    });
    if (!res.ok) throw new Error("Token không hợp lệ");
    const info = await res.json();
    TOKEN = t;
    ROLE = info.role;
    sessionStorage.setItem("fleet_token", t);
    boot(info);
  } catch (e) {
    errEl.textContent = e.message;
  }
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".navbtn").forEach((b) => b.classList.remove("active"));
  const tab = document.getElementById("tab-" + name);
  if (tab) tab.classList.add("active");
  const btn = document.querySelector(`.navbtn[data-tab="${name}"]`);
  if (btn) btn.classList.add("active");
}

function applyRoleVisibility() {
  const isOp = ROLE === "operator" || ROLE === "admin";
  const isAdmin = ROLE === "admin";
  document.querySelectorAll('.navbtn[data-tab="admin"]').forEach((el) => { el.style.display = isAdmin ? "" : "none"; });
  document.querySelectorAll('.navbtn[data-tab="commands"]').forEach((el) => { el.style.display = isOp ? "" : "none"; });
  const hrBox = document.getElementById("human-review-box");
  if (hrBox) hrBox.style.display = isOp ? "block" : "none";
}

async function doLogout() {
  try {
    await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch (_) {}
  if (_SSE_ES) { _SSE_ES.close(); _SSE_ES = null; }
  sessionStorage.removeItem("fleet_token");
  TOKEN = "";
  ROLE = "";
  location.reload();
}

function scheduleRefresh(delayMs = 400) {
  if (_REFRESH_TIMER) return;
  _REFRESH_TIMER = setTimeout(() => {
    _REFRESH_TIMER = null;
    refreshAll().catch(() => {});
  }, delayMs);
}

async function boot(meInfo) {
  try {
    const me = meInfo || await api("/api/v1/auth/me");
    ROLE = me.role;
  } catch (e) {
    sessionStorage.removeItem("fleet_token");
    TOKEN = "";
    return;
  }
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("app").classList.add("active");
  document.getElementById("whoami").textContent = `${ROLE} (...${TOKEN.slice(-6)})`;
  applyRoleVisibility();
  document.querySelectorAll(".navbtn").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  await refreshAll();
  connectSSE();
  setInterval(() => refreshAll().catch(() => {}), 10000);
}

async function refreshAll() {
  const [healthRes, vehiclesRes, violRes, dismissRes, tripsRes, incRes, cmdRes, drvRes, posRes] = await Promise.all([
    api("/api/v1/health"),
    api("/api/v1/vehicles"),
    api("/api/v1/violations"),
    api("/api/v1/dismissals"),
    api("/api/v1/trips"),
    api("/api/v1/incidents"),
    api("/api/v1/commands"),
    api("/api/v1/drivers/summary"),
    api("/api/v1/fleet/positions").catch(() => ({ positions: [] })),
  ]);
  LATEST_POSITIONS = posRes.positions || [];
  TIER2_IS_REAL_CNN = Boolean(healthRes && healthRes.deep_verifier_is_real_cnn);
  TIER2_MODE = (healthRes && healthRes.deep_verifier_mode) || (TIER2_IS_REAL_CNN ? "CNN_ONNX" : "HEURISTIC_BASELINE");
  TIER2_MODEL_NAME = (healthRes && healthRes.deep_cnn_verifier) || "temporal-baseline-v1";
  const banner = document.getElementById("tier2-engine-banner");
  if (banner) {
    if (TIER2_MODE === "CNN_ONNX") {
      banner.textContent = `Bộ hậu kiểm Tầng 2: CNN ONNX (${TIER2_MODEL_NAME})`;
      banner.style.color = "var(--green)";
    } else if (TIER2_MODE === "PRETRAINED_B1") {
      banner.textContent = `Bộ hậu kiểm Tầng 2: Pretrained B1 MediaPipe+YOLO HD (${TIER2_MODEL_NAME})`;
      banner.style.color = "var(--green)";
    } else {
      banner.textContent = `Bộ hậu kiểm Tầng 2: Hậu kiểm cơ bản (${TIER2_MODEL_NAME} — CNN Tầng 2 ở chế độ tùy chọn/tắt)`;
      banner.style.color = "var(--amber)";
    }
  }
  renderKPIs(vehiclesRes.vehicles, violRes.violations, incRes.incidents);
  renderFleet(vehiclesRes.vehicles, tripsRes.trips);
  renderPowerHealth(vehiclesRes.vehicles);
  renderDrivers(drvRes.drivers || []);
  syncMapVehicleSelector(LATEST_POSITIONS);
  await refreshMapTrack();
  renderViolations(violRes.violations);
  renderCommands(cmdRes.commands);
  renderMute(dismissRes.dismissals);
  renderTrips(tripsRes.trips);
  renderIncidents(incRes.incidents);
  if (ROLE === "admin") {
    refreshSecurityHealth().catch(() => {});
  }
}

async function refreshSecurityHealth() {
  const box = document.getElementById("security-health-box");
  if (!box) return;
  const res = await api("/api/v1/admin/security_health");
  box.textContent = JSON.stringify(res.token_health || res, null, 2);
}

function renderDrivers(rows) {
  const tbody = document.querySelector("#drivers-table tbody");
  if (!tbody) return;
  tbody.innerHTML = rows.map((d) => `<tr>
    <td>${esc(d.driver_name)}</td>
    <td>${esc((d.vehicles || []).join(", ") || "-")}</td>
    <td>${esc(d.trip_count)}</td>
    <td><strong>${esc(d.avg_safety_score)}</strong></td>
    <td>${esc(d.ai_confirmed)}</td>
    <td>${esc(d.human_confirmed)}</td>
    <td>${esc(d.human_false_positive)}</td>
    <td>${d.avg_reaction_ms != null ? esc(d.avg_reaction_ms) + " ms" : "-"}</td>
    <td>${badge(d.coaching_level || "AN_TOAN")}</td>
  </tr>`).join("");
}

function badge(status) {
  const m = {
    ONLINE: "ok", SURVIVAL_ESP2: "warn", SERVER_CNN_CONFIRMED: "crit", SERVER_CNN_REJECTED: "ok",
    HUMAN_CONFIRMED: "crit", HUMAN_FALSE_POSITIVE: "ok", PENDING_REVIEW: "warn",
    AN_TOAN: "ok", CAN_NHAC_NHO: "warn", NGUY_CO_CAO: "crit",
    PENDING: "warn", OFFLINE: "offline", WARN: "warn", CRIT: "crit",
    EXECUTED_OK: "ok", FAILED: "crit", SENT_TO_PI: "warn",
  };
  return `<span class="badge ${m[status] || "offline"}">${esc(status)}</span>`;
}

function renderKPIs(vehicles, violations, incidents) {
  const online = vehicles.filter((v) => v.status === "ONLINE").length;
  const confirmed = violations.filter((v) => v.cnn_status === "SERVER_CNN_CONFIRMED").length;
  const rejected = violations.filter((v) => v.cnn_status === "SERVER_CNN_REJECTED").length;
  const openInc = incidents.filter((i) => i.status === "OPEN").length;
  const lblConfirm = TIER2_IS_REAL_CNN ? "CNN Xác nhận" : "Tầng 2 Xác nhận (Baseline)";
  const lblReject = TIER2_IS_REAL_CNN ? "CNN Lọc báo giả" : "Tầng 2 Loại (Baseline)";
  document.getElementById("kpi-row").innerHTML = `
    <div class="kpi"><div class="label">Xe Online</div><div class="value">${online}/${vehicles.length}</div></div>
    <div class="kpi"><div class="label">${lblConfirm}</div><div class="value">${confirmed}</div></div>
    <div class="kpi"><div class="label">${lblReject}</div><div class="value">${rejected}</div></div>
    <div class="kpi"><div class="label">Sự cố mở</div><div class="value">${openInc}</div></div>
  `;
}

function attachToken(u) {
  const clean = safeUrl(u);
  if (!clean || !TOKEN) return clean;
  // SECURITY (F-002): NEVER append Server Admin/Operator TOKEN to external/cross-origin http(s) URLs!
  if (!clean.startsWith("/") || clean.startsWith("//")) return clean;
  if (clean.includes("token=")) return clean;
  const sep = clean.includes("?") ? "&" : "?";
  return `${clean}${sep}token=${encodeURIComponent(TOKEN)}`;
}

function openLiveStream(url, vid) {
  if (!isPrivateLanStreamUrl(url)) {
    toast("Từ chối mở stream URL không thuộc mạng LAN hợp lệ!", "crit");
    return;
  }
  const clean = safeUrl(url);
  let piRoot = clean;
  try {
    const parsed = new URL(clean);
    piRoot = `${parsed.protocol}//${parsed.host}/`;
  } catch (_) {}
  document.getElementById("live-stream-title").textContent = `Camera trực tiếp (LAN) — ${vid || ""}`;
  const linkEl = document.getElementById("live-stream-direct-link");
  if (linkEl) linkEl.href = piRoot;
  const hintEl = document.getElementById("live-stream-hint");
  if (hintEl) hintEl.textContent = `Đang tải từ ${clean}. Nếu Pi yêu cầu xác thực token Viewer, bấm "Mở trang xem Pi (:8080)" ở góc trên để đăng nhập 1 lần.`;
  const img = document.getElementById("live-stream-img");
  img.onerror = () => {
    if (hintEl) hintEl.textContent = `Trình duyệt chưa đăng nhập phiên Viewer trên Pi (${piRoot}). Hãy bấm "Mở trang xem Pi (:8080)" và nhập WEB_VIEWER_TOKEN trong pi.env.`;
  };
  img.src = clean;
  document.getElementById("live-stream-panel").style.display = "block";
}

function closeLiveStream() {
  const img = document.getElementById("live-stream-img");
  if (img) img.src = "";
  const panel = document.getElementById("live-stream-panel");
  if (panel) panel.style.display = "none";
}

function renderFleet(vehicles, trips) {
  const tbody = document.querySelector("#fleet-table tbody");
  tbody.innerHTML = vehicles.map((v) => {
    const t = trips.find((tr) => tr.vehicle_id === v.vehicle_id && tr.status === "ACTIVE");
    const raw = safeParseJson(v.last_telemetry_json);
    const speed = (raw.gps || {}).speed_kmh ?? "-";
    const mq = raw.mq135 || {};
    const co2 = mq.co2_ppm ?? mq.ppm ?? "-";
    const pi = raw.pi || raw.hardware || {};
    const temp = pi.cpu_temp_c ?? pi.soc_temp_c ?? pi.temp_c ?? "-";
    const sUrl = safeUrl(v.stream_url);
    return `<tr>
      <td>${esc(v.vehicle_id)}</td>
      <td>${esc(v.current_driver_name || v.current_driver_id || "-")}</td>
      <td>${badge(v.status)}</td>
      <td>${esc(speed)}</td>
      <td>${esc(co2)}</td>
      <td>${esc(temp)}</td>
      <td>${t ? Math.round(t.safety_score) : "-"}</td>
      <td>${sUrl ? `<button class="action ghost" data-url="${esc(sUrl)}" data-vid="${esc(v.vehicle_id)}" onclick="openLiveStream(this.dataset.url, this.dataset.vid)">▶ Xem LAN</button>` : `<span style="color:var(--muted)">Chỉ gửi Clip 10s</span>`}</td>
    </tr>`;
  }).join("");
}

function renderPowerHealth(vehicles) {
  const tbody = document.querySelector("#hw-power-table tbody");
  if (!tbody) return;
  tbody.innerHTML = vehicles.map((v) => {
    const raw = safeParseJson(v.last_telemetry_json);
    const pi = raw.pi || {};
    const esp1 = raw.esp || {};
    const esp2 = raw.bluetooth_phone || {};
    const temp = pi.cpu_temp_c;
    const tempHtml = temp != null
      ? `<span style="color:${Number(temp) >= 70 ? 'var(--red)' : (Number(temp) >= 65 ? 'var(--amber)' : 'var(--green)')}">${esc(temp)}°C</span>`
      : "-";
    const coreV = pi.core_volt_v != null ? `${esc(pi.core_volt_v)}V` : "-";
    const thr = String(pi.throttled || "0x0");
    const thrBits = Number.parseInt(thr, thr.startsWith("0x") ? 16 : 10) || 0;
    const thrLive = (thrBits & 0xF) !== 0;
    const thrSticky = (thrBits & 0xF0000) !== 0;
    const thrHtml = thrLive
      ? `<span style="color:var(--red);font-weight:600">⚠ ${esc(thr)} (Đang sụt áp/Bóp xung)</span>`
      : (thrSticky
        ? `<span style="color:var(--amber)">Đã từng sụt áp (${esc(thr)})</span>`
        : `<span style="color:var(--green)">OK (${esc(thr)})</span>`);
    const fpsTxt = `${pi.camera_fps ?? "-"} / ${pi.ai_fps ?? "-"}`;
    const isSurvivalEsp2 = Boolean(esp1.direct_wifi && String(esp1.hub_role || "").includes("COMMS_BLE"));
    const esp1Txt = (!isSurvivalEsp2 && esp1.connected)
      ? `${esc(esp1.role || "AUDIO_MQ135_NODE")} | Vol:${esc(esp1.vol ?? "-")} | Brownout:${esp1.brownout ? "CÓ" : "0"} | Reset:${esc(esp1.reset_count ?? 0)}`
      : `<span style="color:var(--muted)">Offline</span>`;
    const esp2Txt = isSurvivalEsp2
      ? `<span style="color:var(--amber)">SURVIVAL WIFI ACTIVE</span> | ĐT:${esp1.ble_connected ? "ĐÃ KẾT NỐI" : "Chờ BLE"} | GPS:${(raw.gps || {}).is_valid ? "FIX" : "NoFix"}`
      : (esp2.hub_connected
        ? `Hub:ON | ĐT:${esp2.phone_connected ? "ĐÃ KẾT NỐI" : "Chờ BLE"} | GPS:${esp2.gps_fix ? `FIX (${esc(esp2.gps_sats ?? 0)} sat)` : "NoFix"} | WiFi:${esp2.survival_wifi_on ? "ON" : "OFF"} | Brownout:${esp2.brownout ? "CÓ" : "0"} | Reset:${esc(esp2.reset_count ?? 0)} | Heap:${esc(esp2.free_heap ?? "-")}`
        : `<span style="color:var(--muted)">Chưa kích hoạt / Offline</span>`);
    return `<tr>
      <td>${esc(v.vehicle_id)}</td>
      <td>${tempHtml}</td>
      <td>${coreV}</td>
      <td>${thrHtml}</td>
      <td>${esc(fpsTxt)}</td>
      <td>${esp1Txt}</td>
      <td>${esp2Txt}</td>
    </tr>`;
  }).join("");
}

function syncMapVehicleSelector(positions) {
  const sel = document.getElementById("map-vehicle-select");
  if (!sel) return;
  const ids = (positions || []).map((p) => p.vehicle_id);
  if (!ids.length) {
    sel.innerHTML = `<option value="">Chưa có xe</option>`;
    SELECTED_MAP_VEHICLE = "";
    return;
  }
  if (!SELECTED_MAP_VEHICLE || !ids.includes(SELECTED_MAP_VEHICLE)) {
    const withPos = positions.find((p) => p.has_position);
    SELECTED_MAP_VEHICLE = withPos ? withPos.vehicle_id : ids[0];
  }
  sel.innerHTML = ids.map((id) => `<option value="${esc(id)}"${id === SELECTED_MAP_VEHICLE ? " selected" : ""}>${esc(id)}</option>`).join("");
}

function onMapVehicleChange() {
  const sel = document.getElementById("map-vehicle-select");
  SELECTED_MAP_VEHICLE = sel ? sel.value : "";
  refreshMapTrack().catch(() => {});
}

async function refreshMapTrack() {
  if (SELECTED_MAP_VEHICLE) {
    try {
      LATEST_TRACK = await api(`/api/v1/vehicles/${encodeURIComponent(SELECTED_MAP_VEHICLE)}/track?limit=300`);
    } catch (_) {
      LATEST_TRACK = null;
    }
  } else {
    LATEST_TRACK = null;
  }
  renderOfflineMap(LATEST_POSITIONS, LATEST_TRACK);
  renderGeoPositionsTable(LATEST_POSITIONS);
}

function projectToLocalEnuMeters(lat, lon, refLat, refLon) {
  // WGS-84 tangent-plane metric approximation accurate to <0.1% within 50 km
  const rad = Math.PI / 180;
  const latRad = refLat * rad;
  const mPerDegLat = 111132.92 - 559.82 * Math.cos(2 * latRad) + 1.175 * Math.cos(4 * latRad);
  const mPerDegLon = 111412.84 * Math.cos(latRad) - 93.5 * Math.cos(3 * latRad);
  return {
    east: (lon - refLon) * mPerDegLon,
    north: (lat - refLat) * mPerDegLat,
  };
}

function chooseScaleBarMeters(metersPerPx) {
  const target = metersPerPx * 110;
  const steps = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000];
  for (const s of steps) {
    if (s >= target) return s;
  }
  return 50000;
}

function renderOfflineMap(positions, track) {
  const canvas = document.getElementById("offline-map");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#1f2937";
  ctx.lineWidth = 1;
  for (let x = 0; x < W; x += 60) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
  for (let y = 0; y < H; y += 60) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

  // Compass rose (N) top-right
  ctx.fillStyle = "#64748b";
  ctx.font = "12px sans-serif";
  ctx.fillText("↑ Bắc (N)", W - 78, 24);

  const realPts = (positions || []).filter((p) => p.has_position && p.lat != null && p.lon != null);
  const trackPts = (track && Array.isArray(track.points)) ? track.points : [];
  const listEl = document.getElementById("map-list");

  if (!realPts.length && !trackPts.length) {
    ctx.fillStyle = "#f59e0b";
    ctx.font = "16px sans-serif";
    ctx.fillText("CHƯA CÓ TỌA ĐỘ GNSS THẬT (0 tọa độ mẫu được phép vẽ).", 24, 46);
    ctx.fillStyle = "#94a3b8";
    ctx.font = "13px sans-serif";
    ctx.fillText("• Đưa ăng-ten GPS NEO (ESP32 #2 UART2 GPIO16/17) ra ngoài trời để khóa vệ tinh, HOẶC", 24, 76);
    ctx.fillText("• Bật 'Chia sẻ GPS thật của điện thoại' trong APP_IOT (/driver) sau khi ghép OTP.", 24, 98);
    if (listEl) {
      const diagTxt = (positions || []).map((p) => {
        const d = p.diagnostics || {};
        return `${p.vehicle_id}: NO_FIX (${d.wiring_status || "WAITING"}, sat=${d.satellites ?? 0}${d.sats_in_view != null ? ", view=" + d.sats_in_view : ""}${d.gps_rx_bytes != null ? ", uart2_rx=" + d.gps_rx_bytes + "B" : ""})`;
      }).join(" · ");
      listEl.textContent = diagTxt || "Chưa có xe kết nối.";
    }
    return;
  }

  // Pick metric ENU origin: active selected vehicle or first real position
  const focusPos = realPts.find((p) => p.vehicle_id === SELECTED_MAP_VEHICLE) || realPts[0] || trackPts[trackPts.length - 1];
  const refLat = Number(focusPos.lat);
  const refLon = Number(focusPos.lon);

  const allEnu = [];
  const trackEnu = trackPts.map((pt) => {
    const enu = projectToLocalEnuMeters(pt.lat, pt.lon, refLat, refLon);
    allEnu.push(enu);
    return Object.assign({}, pt, enu);
  });
  const vehEnu = realPts.map((p) => {
    const enu = projectToLocalEnuMeters(p.lat, p.lon, refLat, refLon);
    allEnu.push(enu);
    return Object.assign({}, p, enu);
  });

  let minE = Math.min(...allEnu.map((p) => p.east));
  let maxE = Math.max(...allEnu.map((p) => p.east));
  let minN = Math.min(...allEnu.map((p) => p.north));
  let maxN = Math.max(...allEnu.map((p) => p.north));

  // Enforce minimum 120m metric viewport span so accuracy ring & arrow remain legible
  const padE = Math.max(60, (maxE - minE) * 0.2);
  const padN = Math.max(60, (maxN - minN) * 0.2);
  minE -= padE; maxE += padE;
  minN -= padN; maxN += padN;

  const spanE = Math.max(120, maxE - minE);
  const spanN = Math.max(120, maxN - minN);
  const margin = 56;
  const usableW = W - margin * 2;
  const usableH = H - margin * 2;
  // Equal isotropic scale (meters/px) on East & North -> ZERO geographic distortion!
  const pxPerMeter = Math.min(usableW / spanE, usableH / spanN);
  const centerE = (minE + maxE) * 0.5;
  const centerN = (minN + maxN) * 0.5;
  const toX = (e) => W * 0.5 + (e - centerE) * pxPerMeter;
  const toY = (n) => H * 0.5 - (n - centerN) * pxPerMeter;

  // Draw real driven trajectory polyline
  if (trackEnu.length >= 2) {
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    trackEnu.forEach((pt, idx) => {
      const x = toX(pt.east), y = toY(pt.north);
      if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    // Start marker
    const s0 = trackEnu[0];
    ctx.fillStyle = "#94a3b8";
    ctx.beginPath();
    ctx.arc(toX(s0.east), toY(s0.north), 4.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // Draw vehicles with real accuracy radius & heading vector
  vehEnu.forEach((p) => {
    const x = toX(p.east);
    const y = toY(p.north);
    const col = p.fix_status === "LIVE_ESP2_GNSS" ? "#22c55e"
      : (p.fix_status === "LIVE_PHONE_GNSS" ? "#06b6d4" : "#f59e0b");

    // Physical accuracy circle (±accuracy_m)
    if (p.accuracy_m && p.accuracy_m > 0) {
      const radPx = Math.max(10, Math.min(140, p.accuracy_m * pxPerMeter));
      ctx.fillStyle = p.fix_status === "LIVE_ESP2_GNSS" ? "rgba(34,197,94,0.14)"
        : (p.fix_status === "LIVE_PHONE_GNSS" ? "rgba(6,182,212,0.14)" : "rgba(245,158,11,0.14)");
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(x, y, radPx, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }

    // Heading directional arrow (0° = North -> -Y on canvas)
    if (p.heading_deg != null && Number.isFinite(Number(p.heading_deg))) {
      const hRad = (Number(p.heading_deg) * Math.PI) / 180;
      const ax = x + Math.sin(hRad) * 24;
      const ay = y - Math.cos(hRad) * 24;
      ctx.strokeStyle = col;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(ax, ay);
      ctx.stroke();
    }

    ctx.fillStyle = col;
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#f8fafc";
    ctx.font = "12px sans-serif";
    const lbl = `${p.vehicle_id} (${Number(p.lat).toFixed(5)}, ${Number(p.lon).toFixed(5)}) · ${p.speed_kmh ?? 0} km/h`;
    ctx.fillText(lbl, x + 12, y + 4);
  });

  // Draw dynamic metric scale bar (bottom-left)
  const scaleM = chooseScaleBarMeters(1.0 / pxPerMeter);
  const scalePx = scaleM * pxPerMeter;
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(22, H - 22);
  ctx.lineTo(22 + scalePx, H - 22);
  ctx.moveTo(22, H - 27);
  ctx.lineTo(22, H - 17);
  ctx.moveTo(22 + scalePx, H - 27);
  ctx.lineTo(22 + scalePx, H - 17);
  ctx.stroke();
  ctx.fillStyle = "#cbd5e1";
  ctx.font = "11px sans-serif";
  ctx.fillText(scaleM >= 1000 ? `${(scaleM / 1000).toFixed(1)} km` : `${scaleM} m`, 22, H - 30);

  if (listEl) {
    const trkInfo = track && track.point_count > 0
      ? `Quỹ đạo ${track.vehicle_id}: ${track.point_count} điểm thật · ${track.total_distance_km} km · TB ${track.avg_speed_kmh} / Max ${track.max_speed_kmh} km/h`
      : "Chưa có lịch sử di chuyển có tọa độ thật.";
    listEl.textContent = trkInfo;
  }
}

function renderGeoPositionsTable(positions) {
  const tbody = document.querySelector("#geo-positions-table tbody");
  if (!tbody) return;
  if (!positions || !positions.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="color:var(--muted)">Chưa có xe nào kết nối.</td></tr>`;
    return;
  }
  tbody.innerHTML = positions.map((p) => {
    const stBadge = p.fix_status === "LIVE_ESP2_GNSS" ? `<span class="badge ok">ESP2 GNSS LIVE</span>`
      : (p.fix_status === "LIVE_PHONE_GNSS" ? `<span class="badge ok">PHONE GNSS LIVE</span>`
      : (p.fix_status === "LAST_KNOWN_STALE" ? `<span class="badge warn">GẦN NHẤT (${esc(p.fix_age_s ?? "?")}s)</span>`
      : `<span class="badge offline">NO FIX</span>`));
    const coordsHtml = p.has_position
      ? `<strong>${Number(p.lat).toFixed(6)}, ${Number(p.lon).toFixed(6)}</strong><br><span style="font-size:11px;color:var(--muted)">${esc(p.dms || "")}</span>`
      : `<span style="color:var(--muted)">Chưa có tọa độ thật</span>`;
    const utmTxt = p.utm ? `UTM ${esc(p.utm.zone_label)}: E=${esc(p.utm.easting_m)}m, N=${esc(p.utm.northing_m)}m` : "-";
    const ecefTxt = p.ecef ? `ECEF: X=${esc(p.ecef.x_m)}, Y=${esc(p.ecef.y_m)}, Z=${esc(p.ecef.z_m)}` : "";
    const projHtml = p.has_position
      ? `<span style="font-size:12px">${utmTxt}</span><br><span style="font-size:11px;color:var(--muted)">${ecefTxt}</span>`
      : "-";
    const motionHtml = p.has_position
      ? `${esc(p.speed_kmh ?? 0)} km/h · ${p.heading_deg != null ? esc(p.heading_deg) + "° (" + esc(p.compass) + ")" : "-"}<br><span style="font-size:11px;color:var(--muted)">Cao: ${p.altitude_m != null ? esc(p.altitude_m) + " m" : "-"}</span>`
      : "-";
    const d = p.diagnostics || {};
    const diagHtml = `${p.accuracy_m != null ? "±" + esc(p.accuracy_m) + " m" : "±?"}${p.hdop != null ? " (HDOP " + esc(p.hdop) + ")" : ""}<br>`
      + `<span style="font-size:11px;color:var(--muted)">Sat: ${esc(d.satellites ?? 0)}${d.sats_in_view != null ? "/" + esc(d.sats_in_view) : ""} · ${esc(d.wiring_status || "-")}${d.gps_rx_bytes != null ? " (" + esc(d.gps_rx_bytes) + "B)" : ""}</span>`;
    const linksHtml = (p.has_position && p.maps_links)
      ? `<a href="${esc(safeUrl(p.maps_links.osm_url))}" target="_blank" rel="noopener">OSM</a> · <a href="${esc(safeUrl(p.maps_links.google_maps_url))}" target="_blank" rel="noopener">GMap</a>`
      : "-";
    return `<tr>
      <td><strong>${esc(p.vehicle_id)}</strong></td>
      <td>${stBadge}<br><span style="font-size:11px;color:var(--muted)">${esc(p.source || "-")}</span></td>
      <td>${coordsHtml}</td>
      <td>${projHtml}</td>
      <td>${motionHtml}</td>
      <td>${diagHtml}</td>
      <td>${linksHtml}</td>
    </tr>`;
  }).join("");
}

function renderViolations(rows) {
  const tbody = document.querySelector("#violations-table tbody");
  tbody.innerHTML = rows.map((r) => {
    const img = safeUrl(r.image_url);
    const vid = safeUrl(r.video_url);
    const hr = r.human_review_status || "PENDING_REVIEW";
    return `<tr>
      <td>${esc(new Date(r.created_at * 1000).toLocaleString())}</td>
      <td>${esc(r.vehicle_id)}</td>
      <td>${esc(r.alert_type || "-")}</td>
      <td>${badge(r.cnn_status || "PENDING")}</td>
      <td>${r.cnn_confidence != null ? Math.round(r.cnn_confidence * 100) + "%" : "-"}</td>
      <td>${badge(hr)}</td>
      <td>${img ? `<a href="${esc(img)}" target="_blank" rel="noopener">Ảnh</a>` : "-"}</td>
      <td><button class="action ghost" data-eid="${esc(r.event_id)}" data-vid="${esc(vid)}" onclick="openViolationReview(this.dataset.eid, this.dataset.vid)">▶ Xem & Duyệt</button></td>
    </tr>`;
  }).join("");
}

function showStoryboardFrame(idx) {
  const i = Math.max(0, Math.min(STORYBOARD_FRAMES.length - 1, Number(idx) || 0));
  const img = document.getElementById("storyboard-img");
  const lbl = document.getElementById("storyboard-label");
  if (img && STORYBOARD_FRAMES[i]) img.src = STORYBOARD_FRAMES[i];
  if (lbl) lbl.textContent = `${i + 1} / ${STORYBOARD_FRAMES.length}`;
}

async function openViolationReview(eventId, videoUrl) {
  const reqId = ++REVIEW_REQ_SEQ;
  CURRENT_REVIEW_EVENT_ID = eventId || "";
  STORYBOARD_FRAMES = [];
  const titleEl = document.getElementById("video-player-title");
  if (titleEl) titleEl.textContent = `Xem & Thẩm định vi phạm — ${eventId}`;
  const msg = document.getElementById("video-player-msg");
  const dl = document.getElementById("video-download-link");
  if (videoUrl) {
    playVideo(videoUrl);
  } else {
    document.getElementById("video-player-panel").style.display = "block";
    const v = document.getElementById("video-player");
    if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
    if (msg) msg.textContent = "Vi phạm này chưa có clip video ghép — xem từng khung hình bên dưới.";
    if (dl) dl.href = "#";
  }
  const sbBox = document.getElementById("storyboard-box");
  const noteEl = document.getElementById("review-note-input");
  if (noteEl) noteEl.value = "";
  try {
    const info = await api(`/api/v1/violations/${encodeURIComponent(eventId)}/frames`);
    if (reqId !== REVIEW_REQ_SEQ) return;
    if (noteEl) noteEl.value = info.human_review_note || "";
    STORYBOARD_FRAMES = (info.frames || []).map(safeUrl).filter(Boolean);
    if (STORYBOARD_FRAMES.length > 0 && sbBox) {
      sbBox.style.display = "block";
      const slider = document.getElementById("storyboard-slider");
      slider.max = String(STORYBOARD_FRAMES.length - 1);
      slider.value = "0";
      showStoryboardFrame(0);
    } else if (sbBox) {
      sbBox.style.display = "none";
    }
  } catch (e) {
    if (reqId === REVIEW_REQ_SEQ && sbBox) sbBox.style.display = "none";
  }
}

function closeVideoPanel() {
  const v = document.getElementById("video-player");
  if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
  const panel = document.getElementById("video-player-panel");
  if (panel) panel.style.display = "none";
}

async function submitHumanReview(decision) {
  if (!CURRENT_REVIEW_EVENT_ID) return toast("Hãy chọn một vi phạm trước!", "crit");
  const note = (document.getElementById("review-note-input")?.value || "").trim();
  try {
    const res = await api(`/api/v1/violations/${encodeURIComponent(CURRENT_REVIEW_EVENT_ID)}/review`, {
      method: "POST",
      body: JSON.stringify({ decision, note }),
    });
    const scoreMsg = res.safety_score != null ? ` (Điểm AT: ${Math.round(res.safety_score)})` : "";
    toast(`Đã duyệt ${CURRENT_REVIEW_EVENT_ID}: ${decision}${scoreMsg}`);
    scheduleRefresh(100);
  } catch (e) {
    toast(`Duyệt thất bại: ${e.message}`, "crit");
  }
}

function playVideo(url) {
  const clean = safeUrl(url);
  if (!clean) return;
  const panel = document.getElementById("video-player-panel");
  const v = document.getElementById("video-player");
  const msg = document.getElementById("video-player-msg");
  const dl = document.getElementById("video-download-link");
  panel.style.display = "block";
  if (dl) dl.href = clean;
  if (msg) msg.textContent = `Đang tải clip: ${clean}`;
  v.onerror = () => {
    if (msg) msg.textContent = "Trình duyệt chưa phát được codec clip này — bạn có thể xem từng khung hình ngay bên dưới hoặc bấm 'Mở / Tải clip'.";
  };
  v.onloadeddata = () => {
    if (msg) msg.textContent = `Đã sẵn sàng (${v.videoWidth}x${v.videoHeight}, ${v.duration ? v.duration.toFixed(1) + "s" : ""})`;
  };
  v.src = clean;
  v.load();
  v.play().catch(() => {});
}

function renderCommands(rows) {
  const tbody = document.querySelector("#commands-table tbody");
  tbody.innerHTML = rows.map((r) => `<tr>
    <td>${esc(r.cmd_uid)}</td><td>${esc(r.vehicle_id)}</td><td>${esc(r.command)}</td>
    <td>${badge(r.status)}</td><td>${esc(r.ack_detail || "-")}</td>
  </tr>`).join("");
}

async function sendCommand() {
  const vid = document.getElementById("cmd-vehicle").value.trim();
  const cmd = document.getElementById("cmd-select").value;
  if (!vid) return toast("Nhập vehicle_id trước!", "crit");
  try {
    await api("/api/v1/server/command", { method: "POST", body: JSON.stringify({ vehicle_id: vid, cmd }) });
    toast(`Đã xếp hàng lệnh ${cmd} -> ${vid}`);
    scheduleRefresh(100);
  } catch (e) {
    toast(`Gửi lệnh thất bại: ${e.message}`, "crit");
  }
}

async function saveRemoteConfig() {
  const vid = document.getElementById("cfg-vehicle").value.trim();
  if (!vid) return toast("Nhập vehicle_id trước!", "crit");
  try {
    await api(`/api/v1/config/${encodeURIComponent(vid)}`, {
      method: "POST",
      body: JSON.stringify({
        allow_smoking: document.getElementById("cfg-smoking").checked,
        allow_phone: document.getElementById("cfg-phone").checked,
      }),
    });
    toast(`Đã lưu remote config cho ${vid}`);
  } catch (e) {
    toast(`Lưu config thất bại: ${e.message}`, "crit");
  }
}

function renderMute(rows) {
  document.querySelector("#mute-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${esc(new Date(r.created_at * 1000).toLocaleString())}</td>
    <td>${esc(r.vehicle_id)}</td><td>${esc(r.trip_id || "-")}</td>
    <td>${esc(r.dismiss_source)}</td><td>${esc(r.reaction_time_ms ?? "-")} ms</td>
    <td>${esc(r.reaction_grade || "-")}</td>
  </tr>`).join("");
}

function renderTrips(rows) {
  document.querySelector("#trips-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${esc(r.trip_id)}</td><td>${esc(r.vehicle_id)}</td>
    <td>${esc(r.driver_name || r.driver_id || "-")}</td>
    <td>${Number(r.distance_km || 0).toFixed(2)}</td>
    <td>${Math.round(r.safety_score)}</td><td>${esc(r.violation_count)}</td>
    <td>${esc(r.mute_count)}</td><td>${badge(r.status === "ACTIVE" ? "ONLINE" : "OFFLINE")}</td>
  </tr>`).join("");
}

function renderIncidents(rows) {
  document.querySelector("#incidents-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${esc(new Date(r.created_at * 1000).toLocaleString())}</td>
    <td>${esc(r.vehicle_id)}</td>
    <td>${badge(r.severity === "CRITICAL" || r.severity === "HIGH" ? "CRIT" : "WARN")}</td>
    <td>${esc(r.category)}</td><td>${esc(r.title)}</td><td>${esc(r.status)}</td>
    <td>
      ${r.status === "OPEN" ? `<button class="action ghost" data-id="${esc(r.incident_id)}" onclick="ackIncident(this.dataset.id)">Tiếp nhận</button>` : ""}
      ${r.status !== "RESOLVED" ? `<button class="action" data-id="${esc(r.incident_id)}" onclick="resolveIncident(this.dataset.id)">Đóng</button>` : ""}
    </td>
  </tr>`).join("");
}

async function ackIncident(id) {
  try {
    await api(`/api/v1/incidents/${encodeURIComponent(id)}/ack`, { method: "POST", body: JSON.stringify({ ack_by: ROLE }) });
    scheduleRefresh(100);
  } catch (e) {
    toast(`Tiếp nhận thất bại: ${e.message}`, "crit");
  }
}

async function resolveIncident(id) {
  try {
    await api(`/api/v1/incidents/${encodeURIComponent(id)}/resolve`, { method: "POST", body: JSON.stringify({ note: "Đã xử lý" }) });
    scheduleRefresh(100);
  } catch (e) {
    toast(`Đóng sự cố thất bại: ${e.message}`, "crit");
  }
}

async function exportReport() {
  const kind = document.getElementById("report-kind").value;
  const fmt = document.getElementById("report-fmt").value;
  const res = await fetch(`/api/v1/reports/export?kind=${encodeURIComponent(kind)}&fmt=${encodeURIComponent(fmt)}`, {
    headers: { "X-Dashboard-Token": TOKEN },
    credentials: "same-origin",
  });
  if (!res.ok) {
    const errText = await res.text();
    return toast(`Xuất báo cáo thất bại (${res.status}): ${errText.slice(0, 120)}`, "crit");
  }
  const blob = await res.blob();
  const objUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objUrl;
  a.download = `${kind}.${fmt}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(objUrl), 5000);
}

async function issueToken() {
  const role = document.getElementById("new-token-role").value;
  try {
    const res = await api("/api/v1/admin/tokens", { method: "POST", body: JSON.stringify({ role }) });
    document.getElementById("new-token-result").textContent = `Token mới (${res.role}): ${res.token}`;
  } catch (e) {
    toast(`Tạo token thất bại: ${e.message}`, "crit");
  }
}

function connectSSE() {
  // Uses HttpOnly SameSite=Lax 'web_token' cookie set by /api/v1/auth/login (no token leaked in URL query!)
  if (_SSE_ES) { _SSE_ES.close(); _SSE_ES = null; }
  const es = new EventSource("/api/v1/stream/events", { withCredentials: true });
  _SSE_ES = es;
  es.onopen = () => { _SSE_RETRY_MS = 3000; };
  es.addEventListener("violation", (e) => {
    const d = safeParseJson(e.data).data || {};
    toast("Tầng 1 phát hiện: " + (d.alert_type || "VIOLATION"));
    scheduleRefresh();
  });
  es.addEventListener("cnn_verdict", (e) => {
    const d = safeParseJson(e.data).data || {};
    const tag = TIER2_IS_REAL_CNN ? "CNN Tầng 2" : "Hậu kiểm Tầng 2";
    toast(`${tag} [${d.event_id || ""}]: ${d.cnn_status || ""}`, d.cnn_status === "SERVER_CNN_CONFIRMED" ? "crit" : "info");
    scheduleRefresh();
  });
  es.addEventListener("incident", (e) => {
    const d = safeParseJson(e.data).data || {};
    toast("Sự cố: " + (d.title || ""), "crit");
    scheduleRefresh();
  });
  es.addEventListener("violation_reviewed", () => scheduleRefresh());
  es.addEventListener("command_ack", () => scheduleRefresh());
  es.onerror = () => {
    es.close();
    const wait = _SSE_RETRY_MS;
    _SSE_RETRY_MS = Math.min(30000, _SSE_RETRY_MS * 2);
    setTimeout(connectSSE, wait);
  };
}

document.addEventListener("DOMContentLoaded", () => {
  const inp = document.getElementById("token-input");
  if (inp) inp.addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
});

if (TOKEN) { boot(); }
