let TOKEN = sessionStorage.getItem("fleet_token") || "";
let ROLE = "";
let CURRENT_REVIEW_EVENT_ID = "";
let STORYBOARD_FRAMES = [];
let TIER2_IS_REAL_CNN = false;
let TIER2_MODEL_NAME = "temporal-baseline-v1";

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function safeUrl(u) {
  if (!u) return "";
  const s = String(u);
  if (s.startsWith("/") || s.startsWith("http://") || s.startsWith("https://")) return s;
  return "";
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
  const el = document.createElement("div");
  el.className = "toast-item" + (level === "crit" ? " crit" : "");
  el.textContent = title;
  document.getElementById("toast").appendChild(el);
  setTimeout(() => el.remove(), 7000);
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
  document.querySelectorAll(".navbtn").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  await refreshAll();
  connectSSE();
  setInterval(refreshAll, 10000);
}

async function refreshAll() {
  const [healthRes, vehiclesRes, violRes, dismissRes, tripsRes, incRes, cmdRes, drvRes] = await Promise.all([
    api("/api/v1/health"),
    api("/api/v1/vehicles"),
    api("/api/v1/violations"),
    api("/api/v1/dismissals"),
    api("/api/v1/trips"),
    api("/api/v1/incidents"),
    api("/api/v1/commands"),
    api("/api/v1/drivers/summary"),
  ]);
  TIER2_IS_REAL_CNN = Boolean(healthRes && healthRes.deep_verifier_is_real_cnn);
  TIER2_MODEL_NAME = (healthRes && healthRes.deep_cnn_verifier) || "temporal-baseline-v1";
  const banner = document.getElementById("tier2-engine-banner");
  if (banner) {
    banner.textContent = TIER2_IS_REAL_CNN
      ? `Bộ hậu kiểm Tầng 2: CNN ONNX (${TIER2_MODEL_NAME})`
      : `Bộ hậu kiểm Tầng 2: Hậu kiểm cơ bản (${TIER2_MODEL_NAME} — heuristic độ sáng/thời lượng, chưa nạp model CNN .onnx)`;
    banner.style.color = TIER2_IS_REAL_CNN ? "var(--green)" : "var(--amber)";
  }
  renderKPIs(vehiclesRes.vehicles, violRes.violations, incRes.incidents);
  renderFleet(vehiclesRes.vehicles, tripsRes.trips);
  renderDrivers(drvRes.drivers || []);
  renderOfflineMap(vehiclesRes.vehicles);
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
    ONLINE: "ok", SERVER_CNN_CONFIRMED: "crit", SERVER_CNN_REJECTED: "ok",
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
  if (clean.includes("token=")) return clean;
  const sep = clean.includes("?") ? "&" : "?";
  return `${clean}${sep}token=${encodeURIComponent(TOKEN)}`;
}

function openLiveStream(url, vid) {
  const clean = attachToken(url);
  if (!clean) return;
  document.getElementById("live-stream-title").textContent = `Camera trực tiếp (LAN) — ${vid || ""}`;
  const img = document.getElementById("live-stream-img");
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
    const raw = v.last_telemetry_json ? JSON.parse(v.last_telemetry_json) : {};
    const speed = (raw.gps || {}).speed_kmh ?? "-";
    const mq = raw.mq135 || {};
    const co2 = mq.co2_ppm ?? mq.ppm ?? "-";
    const pi = raw.pi || raw.hardware || {};
    const temp = pi.cpu_temp_c ?? pi.soc_temp_c ?? pi.temp_c ?? "-";
    const sUrl = attachToken(v.stream_url);
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

function renderOfflineMap(vehicles) {
  const canvas = document.getElementById("offline-map");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#1f2937";
  for (let x = 0; x < canvas.width; x += 60) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke(); }
  for (let y = 0; y < canvas.height; y += 60) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke(); }

  const pts = vehicles.map((v) => ({
    id: v.vehicle_id,
    lat: v.gps_valid ? v.gps_lat : v.xyz_lat,
    lon: v.gps_valid ? v.gps_lon : v.xyz_lon,
    valid: Boolean(v.gps_valid),
  })).filter((p) => p.lat != null && p.lon != null);

  const listEl = document.getElementById("map-list");
  if (!pts.length) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = "15px sans-serif";
    ctx.fillText("Chưa có tọa độ GNSS hợp lệ từ xe.", 24, 40);
    listEl.textContent = "Chưa có xe gửi tọa độ GPS.";
    return;
  }

  const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon);
  const minLat = Math.min(...lats) - 0.005, maxLat = Math.max(...lats) + 0.005;
  const minLon = Math.min(...lons) - 0.005, maxLon = Math.max(...lons) + 0.005;

  pts.forEach((p) => {
    const x = 60 + ((p.lon - minLon) / Math.max(1e-6, maxLon - minLon)) * (canvas.width - 120);
    const y = canvas.height - 60 - ((p.lat - minLat) / Math.max(1e-6, maxLat - minLat)) * (canvas.height - 120);
    ctx.fillStyle = p.valid ? "#22c55e" : "#f59e0b";
    ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#f8fafc";
    ctx.font = "13px sans-serif";
    ctx.fillText(`${p.id} (${p.lat.toFixed(4)}, ${p.lon.toFixed(4)})`, x + 12, y + 4);
  });
  listEl.innerHTML = pts.map((p) => `${esc(p.id)}: ${p.lat.toFixed(5)}, ${p.lon.toFixed(5)} [${p.valid ? "GNSS" : "XYZ"}]`).join(" · ");
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
  CURRENT_REVIEW_EVENT_ID = eventId || "";
  const titleEl = document.getElementById("video-player-title");
  if (titleEl) titleEl.textContent = `Xem & Thẩm định vi phạm — ${eventId}`;
  if (videoUrl) {
    playVideo(videoUrl);
  } else {
    document.getElementById("video-player-panel").style.display = "block";
    const v = document.getElementById("video-player");
    if (v) v.removeAttribute("src");
  }
  const sbBox = document.getElementById("storyboard-box");
  const noteEl = document.getElementById("review-note-input");
  try {
    const info = await api(`/api/v1/violations/${encodeURIComponent(eventId)}/frames`);
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
    if (sbBox) sbBox.style.display = "none";
  }
}

async function submitHumanReview(decision) {
  if (!CURRENT_REVIEW_EVENT_ID) return toast("Hãy chọn một vi phạm trước!", "crit");
  const note = (document.getElementById("review-note-input")?.value || "").trim();
  const res = await api(`/api/v1/violations/${encodeURIComponent(CURRENT_REVIEW_EVENT_ID)}/review`, {
    method: "POST",
    body: JSON.stringify({ decision, note }),
  });
  const scoreMsg = res.safety_score != null ? ` (Điểm AT: ${Math.round(res.safety_score)})` : "";
  toast(`Đã duyệt ${CURRENT_REVIEW_EVENT_ID}: ${decision}${scoreMsg}`);
  refreshAll();
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
  await api("/api/v1/server/command", { method: "POST", body: JSON.stringify({ vehicle_id: vid, cmd }) });
  toast(`Đã xếp hàng lệnh ${cmd} -> ${vid}`);
  refreshAll();
}

async function saveRemoteConfig() {
  const vid = document.getElementById("cfg-vehicle").value.trim();
  if (!vid) return toast("Nhập vehicle_id trước!", "crit");
  await api(`/api/v1/config/${encodeURIComponent(vid)}`, {
    method: "POST",
    body: JSON.stringify({
      allow_smoking: document.getElementById("cfg-smoking").checked,
      allow_phone: document.getElementById("cfg-phone").checked,
    }),
  });
  toast(`Đã lưu remote config cho ${vid}`);
}

function renderMute(rows) {
  document.querySelector("#mute-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${esc(new Date(r.created_at * 1000).toLocaleString())}</td>
    <td>${esc(r.vehicle_id)}</td><td>${esc(r.trip_id || "-")}</td>
    <td>${esc(r.dismiss_source)}</td><td>${r.reaction_time_ms ?? "-"} ms</td>
    <td>${esc(r.reaction_grade || "-")}</td>
  </tr>`).join("");
}

function renderTrips(rows) {
  document.querySelector("#trips-table tbody").innerHTML = rows.map((r) => `<tr>
    <td>${esc(r.trip_id)}</td><td>${esc(r.vehicle_id)}</td>
    <td>${esc(r.driver_name || r.driver_id || "-")}</td>
    <td>${Number(r.distance_km || 0).toFixed(2)}</td>
    <td>${Math.round(r.safety_score)}</td><td>${r.violation_count}</td>
    <td>${r.mute_count}</td><td>${badge(r.status === "ACTIVE" ? "ONLINE" : "OFFLINE")}</td>
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
  await api(`/api/v1/incidents/${encodeURIComponent(id)}/ack`, { method: "POST", body: JSON.stringify({ ack_by: ROLE }) });
  refreshAll();
}

async function resolveIncident(id) {
  await api(`/api/v1/incidents/${encodeURIComponent(id)}/resolve`, { method: "POST", body: JSON.stringify({ note: "Đã xử lý" }) });
  refreshAll();
}

async function exportReport() {
  const kind = document.getElementById("report-kind").value;
  const fmt = document.getElementById("report-fmt").value;
  const res = await fetch(`/api/v1/reports/export?kind=${encodeURIComponent(kind)}&fmt=${encodeURIComponent(fmt)}`, {
    headers: { "X-Dashboard-Token": TOKEN },
  });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${kind}.${fmt}`;
  a.click();
}

async function issueToken() {
  const role = document.getElementById("new-token-role").value;
  const res = await api("/api/v1/admin/tokens", { method: "POST", body: JSON.stringify({ role }) });
  document.getElementById("new-token-result").textContent = `Token mới (${res.role}): ${res.token}`;
}

function connectSSE() {
  // Uses HttpOnly SameSite=Lax 'web_token' cookie set by /api/v1/auth/login (no token leaked in URL query!)
  const es = new EventSource("/api/v1/stream/events", { withCredentials: true });
  es.addEventListener("violation", (e) => { toast("Tầng 1 phát hiện: " + JSON.parse(e.data).data.alert_type); refreshAll(); });
  es.addEventListener("cnn_verdict", (e) => {
    const d = JSON.parse(e.data).data;
    const tag = TIER2_IS_REAL_CNN ? "CNN Tầng 2" : "Hậu kiểm Tầng 2";
    toast(`${tag} [${d.event_id}]: ${d.cnn_status}`, d.cnn_status === "SERVER_CNN_CONFIRMED" ? "crit" : "info");
    refreshAll();
  });
  es.addEventListener("incident", (e) => { const d = JSON.parse(e.data).data; toast("Sự cố: " + d.title, "crit"); refreshAll(); });
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };
}

if (TOKEN) { boot(); }
