/**
 * APP_IOT — Option C1 Driver PWA Web Bluetooth Client for ESP32 #2 (COMMS_BLE_GPS_HUB).
 *
 * Protocol Contract (LCK-05 v3 — MUST NOT drift from firmware/esp32_ble_wifi_hub/esp32_ble_wifi_hub.ino):
 *   - Service UUID : 6e400001-b5a3-f393-e0a9-e50e24dcca9e (Nordic UART Service)
 *   - RX (Write)   : 6e400002-b5a3-f393-e0a9-e50e24dcca9e -> ASCII + '\n' (MUTE|SOS|ACK|REST_START|REST_END|STATUS)
 *   - TX (Notify)  : 6e400003-b5a3-f393-e0a9-e50e24dcca9e <- ALERT:... | GPS:{...} | STATUS:{...} | OK:... | ERR:...
 */
(function (root) {
  "use strict";

  const BLE_NUS = Object.freeze({
    SERVICE_UUID: "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
    CHAR_RX_UUID: "6e400002-b5a3-f393-e0a9-e50e24dcca9e",
    CHAR_TX_UUID: "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
    DEVICE_PREFIX: "PI_AI_HUB_",
    ALLOWED_CMDS: new Set(["MUTE", "SOS", "ACK", "REST_START", "REST_END", "STATUS"]),
  });

  /**
   * Reassembles 20-byte BLE ATT MTU notify fragments into complete newline-delimited messages.
   */
  class BleLineFramingParser {
    constructor(maxBufferLen = 2048) {
      this._buf = "";
      this._maxLen = maxBufferLen;
    }

    pushChunk(textChunk) {
      if (!textChunk) return [];
      this._buf += String(textChunk);
      if (this._buf.length > this._maxLen) {
        // Drop runaway malformed stream safely without OOM
        this._buf = this._buf.slice(-512);
      }
      const lines = [];
      let idx;
      while ((idx = this._buf.indexOf("\n")) !== -1) {
        const line = this._buf.slice(0, idx).replace(/\r/g, "").trim();
        this._buf = this._buf.slice(idx + 1);
        if (line.length > 0) lines.push(line);
      }
      return lines;
    }

    reset() {
      this._buf = "";
    }
  }

  /**
   * Parses a single complete line emitted by ESP32 #2 over NUS TX.
   */
  function parseEsp2Notification(rawLine) {
    const line = String(rawLine || "").trim();
    if (!line) return { kind: "EMPTY", raw: "" };

    if (line.startsWith("GPS:")) {
      const jsonPart = line.slice(4).trim();
      try {
        const obj = JSON.parse(jsonPart);
        if (obj && typeof obj === "object" && !Array.isArray(obj)) {
          return {
            kind: "GPS",
            fix: Boolean(obj.fix),
            lat: Number(obj.lat || 0),
            lon: Number(obj.lon || 0),
            spd: Number(obj.spd || 0),
            hdg: obj.hdg != null ? Number(obj.hdg) : null,
            hdop: obj.hdop != null && Number(obj.hdop) < 99 ? Number(obj.hdop) : null,
            sats: Number(obj.sats || 0),
            satsView: obj.sats_view != null ? Number(obj.sats_view) : null,
            raw: line,
          };
        }
      } catch (_) {
        return { kind: "PARSE_ERROR", raw: line };
      }
      return { kind: "PARSE_ERROR", raw: line };
    }

    if (line.startsWith("STATUS:")) {
      const jsonPart = line.slice(7).trim();
      try {
        const obj = JSON.parse(jsonPart);
        if (obj && typeof obj === "object" && !Array.isArray(obj)) {
          return { kind: "STATUS", data: obj, raw: line };
        }
      } catch (_) {
        return { kind: "PARSE_ERROR", raw: line };
      }
      return { kind: "PARSE_ERROR", raw: line };
    }

    if (line.startsWith("ALERT:")) {
      const body = line.slice(6).trim();
      const parts = body.split("|");
      return {
        kind: "ALERT",
        alertType: (parts[0] || "WARNING").trim().toUpperCase(),
        detail: parts.slice(1).join("|").trim(),
        raw: line,
      };
    }

    if (line.startsWith("OK:")) {
      return { kind: "ACK", action: line.slice(3).trim(), raw: line };
    }

    if (line.startsWith("WARN:")) {
      return { kind: "WARN", message: line.slice(5).trim(), raw: line };
    }

    if (line.startsWith("ERR:")) {
      return { kind: "ERROR", message: line.slice(4).trim(), raw: line };
    }

    return { kind: "INFO", text: line, raw: line };
  }

  function encodeBleCommand(cmd) {
    const clean = String(cmd || "").trim().toUpperCase();
    if (!BLE_NUS.ALLOWED_CMDS.has(clean)) {
      throw new Error("Command not allowed: " + clean);
    }
    return clean + "\n";
  }

  /**
   * Browser Web Bluetooth NUS client with serialized RX write queue and auto-reconnect.
   */
  class DriverBleClient {
    constructor(callbacks = {}) {
      this.cb = callbacks;
      this.device = null;
      this.server = null;
      this.rxChar = null;
      this.txChar = null;
      this.connected = false;
      this._manualDisconnect = false;
      this._reconnectAttempt = 0;
      this._reconnectTimer = null;
      this._onDiscBound = () => this._onDisconnected();
      this._writeChain = Promise.resolve();
      this._framer = new BleLineFramingParser();
      this._decoder = typeof TextDecoder !== "undefined" ? new TextDecoder("utf-8") : null;
      this._encoder = typeof TextEncoder !== "undefined" ? new TextEncoder() : null;
    }

    static checkBrowserSupport() {
      const isSecure = typeof window !== "undefined" && Boolean(window.isSecureContext);
      const hasBle = typeof navigator !== "undefined" && Boolean(navigator.bluetooth);
      return {
        supported: isSecure && hasBle,
        isSecure,
        hasBle,
        reason: !isSecure
          ? "Trình duyệt chặn Web Bluetooth trên HTTP thường. Hãy mở qua HTTPS (Cloudflare Tunnel) hoặc localhost."
          : !hasBle
          ? "Trình duyệt này không hỗ trợ Web Bluetooth (Safari iPhone không hỗ trợ — hãy dùng Android Chrome hoặc Bluefy trên iOS)."
          : "",
      };
    }

    async connect() {
      const sup = DriverBleClient.checkBrowserSupport();
      if (!sup.supported) throw new Error(sup.reason);
      this._manualDisconnect = false;

      if (this.device) {
        try { this.device.removeEventListener("gattserverdisconnected", this._onDiscBound); } catch (_) {}
      }
      this.device = await navigator.bluetooth.requestDevice({
        filters: [
          { namePrefix: BLE_NUS.DEVICE_PREFIX },
          { services: [BLE_NUS.SERVICE_UUID] },
        ],
        optionalServices: [BLE_NUS.SERVICE_UUID],
      });
      this.device.addEventListener("gattserverdisconnected", this._onDiscBound);
      await this._connectGatt();
    }

    async _connectGatt() {
      if (!this.device || !this.device.gatt) return;
      this._framer.reset();
      this.server = await this.device.gatt.connect();
      // Retry GATT service discovery (Windows 11 / Android BLE stacks often need 400-1000ms after ESP32 connect)
      let service = null;
      let lastErr = null;
      const delays = [350, 500, 800, 1000];
      for (let i = 0; i < delays.length; i++) {
        await new Promise((r) => setTimeout(r, delays[i]));
        try {
          service = await this.server.getPrimaryService(BLE_NUS.SERVICE_UUID);
          if (service) break;
        } catch (err) {
          lastErr = err;
          try {
            const allSvcs = await this.server.getPrimaryServices();
            const found = allSvcs.find((s) => String(s.uuid || "").toLowerCase() === BLE_NUS.SERVICE_UUID);
            if (found) { service = found; break; }
          } catch (_) {}
        }
      }
      if (!service) {
        let diag = "";
        try {
          const allSvcs = await this.server.getPrimaryServices();
          diag = ` | Device=${this.device.name || "?"} | Services=[${allSvcs.map((s) => s.uuid).join(", ")}]`;
        } catch (_) {
          diag = ` | Device=${this.device.name || "?"} | (GATT cache rong — hay Go ghep doi trong Windows Settings > Bluetooth neu da pair cu)`;
        }
        throw new Error((lastErr ? lastErr.message : "Khong tim thay NUS service") + diag);
      }
      this.rxChar = await service.getCharacteristic(BLE_NUS.CHAR_RX_UUID);
      this.txChar = await service.getCharacteristic(BLE_NUS.CHAR_TX_UUID);
      this.txChar.addEventListener("characteristicvaluechanged", (evt) => this._onNotify(evt));
      // Unlock RX commands even if CCCD notify subscription needs a retry on some Android/Windows BLE stacks
      let notifyOk = false;
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          await this.txChar.startNotifications();
          notifyOk = true;
          break;
        } catch (err) {
          if (attempt === 0) await new Promise((r) => setTimeout(r, 350));
          else if (this.cb.onWarn) this.cb.onWarn(`Notify TX cảnh báo (${err.message}) — nút gửi lệnh vẫn hoạt động.`);
        }
      }
      this.connected = true;
      this._reconnectAttempt = 0;
      if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
      if (this.cb.onStateChange) this.cb.onStateChange("CONNECTED", this.device.name || "PI_AI_HUB", notifyOk);
    }

    _onDisconnected() {
      this.connected = false;
      if (this.cb.onStateChange) this.cb.onStateChange("DISCONNECTED", this.device ? this.device.name : "");
      if (!this._manualDisconnect && this.device && this._reconnectAttempt < 10) {
        const delayMs = Math.min(30000, 2500 * Math.pow(2, this._reconnectAttempt));
        this._reconnectAttempt += 1;
        if (this._reconnectTimer) clearTimeout(this._reconnectTimer);
        this._reconnectTimer = setTimeout(() => {
          if (!this._manualDisconnect && !this.connected) {
            this._connectGatt().catch(() => {
              if (!this._manualDisconnect && !this.connected) this._onDisconnected();
            });
          }
        }, delayMs);
      }
    }

    _onNotify(evt) {
      const dv = evt.target && evt.target.value;
      if (!dv || !this._decoder) return;
      // Decode exact DataView slice with {stream: true} so split UTF-8 chars across 20B MTU chunks stay intact
      const chunk = this._decoder.decode(dv, { stream: true });
      const lines = this._framer.pushChunk(chunk);
      for (const line of lines) {
        const parsed = parseEsp2Notification(line);
        if (this.cb.onMessage) this.cb.onMessage(parsed);
      }
    }

    sendCommand(cmd) {
      const wire = encodeBleCommand(cmd);
      const bytes = this._encoder.encode(wire);
      // F-014: Recover chain after any error via .catch() so 1 failed write NEVER bricks future BLE buttons!
      const next = this._writeChain.catch(() => {}).then(async () => {
        if (!this.connected || !this.rxChar) throw new Error("BLE chưa kết nối");
        if (typeof this.rxChar.writeValueWithoutResponse === "function") {
          try {
            await this.rxChar.writeValueWithoutResponse(bytes);
            return;
          } catch (_) {
            // Fallback to writeValueWithResponse / writeValue on Android stacks that require response
          }
        }
        if (typeof this.rxChar.writeValueWithResponse === "function") {
          await this.rxChar.writeValueWithResponse(bytes);
        } else {
          await this.rxChar.writeValue(bytes);
        }
      });
      this._writeChain = next.catch(() => {});
      return next;
    }

    disconnect() {
      this._manualDisconnect = true;
      this.connected = false;
      if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
      if (this.device && this.device.gatt && this.device.gatt.connected) {
        this.device.gatt.disconnect();
      }
    }
  }

  function buildPhoneGpsTelemetry(vehicleId, position) {
    const c = position && position.coords ? position.coords : null;
    if (!c) return null;
    const lat = Number(c.latitude);
    const lon = Number(c.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    if (Math.abs(lat) < 1e-6 && Math.abs(lon) < 1e-6) return null;
    const acc = Number.isFinite(Number(c.accuracy)) ? Number(c.accuracy) : null;
    if (acc !== null && acc > 500) return null;
    const spdMps = c.speed != null && Number.isFinite(Number(c.speed)) && Number(c.speed) >= 0 ? Number(c.speed) : null;
    const hdg = c.heading != null && Number.isFinite(Number(c.heading)) && Number(c.heading) >= 0 ? Number(c.heading) : null;
    const alt = c.altitude != null && Number.isFinite(Number(c.altitude)) ? Number(c.altitude) : null;
    return {
      vehicle_id: String(vehicleId || "XE-01").trim(),
      phone_gps: {
        is_valid: true,
        lat: Number(lat.toFixed(6)),
        lon: Number(lon.toFixed(6)),
        accuracy_m: acc !== null ? Number(acc.toFixed(1)) : null,
        altitude_m: alt !== null ? Number(alt.toFixed(1)) : null,
        speed_kmh: spdMps !== null ? Number((spdMps * 3.6).toFixed(1)) : 0.0,
        heading_deg: hdg !== null ? Number(hdg.toFixed(1)) : null,
        source: "phone_gnss",
      },
    };
  }

  // Export pure helpers for Node.js automated tests (APP_IOT/test_driver_ble.mjs)
  const exportsObj = {
    BLE_NUS,
    BleLineFramingParser,
    parseEsp2Notification,
    encodeBleCommand,
    buildPhoneGpsTelemetry,
    DriverBleClient,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = exportsObj;
  }
  root.PI_AI_DRIVER_BLE = exportsObj;

  // ---------------- Browser UI Wiring ----------------
  if (typeof document === "undefined") return;

  let client = null;
  let phoneToken = localStorage.getItem("pi_ai_driver_phone_token") || "";
  let pairedVehicle = localStorage.getItem("pi_ai_driver_vehicle_id") || "XE-01";

  function logLine(msg) {
    const el = document.getElementById("ble-log");
    if (!el) return;
    const ts = new Date().toLocaleTimeString("vi-VN");
    el.textContent = `[${ts}] ${msg}\n` + el.textContent.slice(0, 2400);
  }

  function setControlsEnabled(enabled) {
    document.querySelectorAll(".ble-action").forEach((b) => { b.disabled = !enabled; });
    const bd = document.getElementById("btn-disconnect");
    const bs = document.getElementById("btn-status");
    if (bd) bd.disabled = !enabled;
    if (bs) bs.disabled = !enabled;
  }

  function speakAlert(text) {
    try {
      if (navigator.vibrate) navigator.vibrate([250, 120, 250]);
      if (window.speechSynthesis) {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = "vi-VN";
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
      }
    } catch (_) {}
  }

  function onBleState(state, devName) {
    const badge = document.getElementById("ble-status-badge");
    const sub = document.getElementById("device-subtitle");
    if (state === "CONNECTED") {
      badge.textContent = "ĐÃ KẾT NỐI BLE";
      badge.className = "badge ok";
      sub.textContent = `Đang nối: ${devName}`;
      setControlsEnabled(true);
      syncWakeLock(true);
      logLine(`Kết nối thành công tới ${devName}`);
    } else {
      badge.textContent = "MẤT KẾT NỐI";
      badge.className = "badge err";
      setControlsEnabled(false);
      syncWakeLock(false);
      logLine("Mất kết nối BLE — sẽ tự thử nối lại theo chu kỳ tăng dần...");
    }
  }

  function onBleMessage(msg) {
    if (msg.kind === "GPS") {
      document.getElementById("gps-speed").textContent = msg.spd.toFixed(1);
      document.getElementById("gps-sats").textContent = msg.satsView != null ? `${msg.sats}/${msg.satsView}` : String(msg.sats);
      document.getElementById("gps-fix").textContent = msg.fix ? "LOCKED" : "NO FIX";
      document.getElementById("gps-coords").textContent = msg.fix
        ? `ESP2 GPS: ${msg.lat.toFixed(6)}, ${msg.lon.toFixed(6)}${msg.hdop != null ? ` (HDOP ${msg.hdop.toFixed(1)})` : ""}`
        : `ESP2 GPS: chưa khóa vệ tinh${msg.satsView != null ? ` (thấy ${msg.satsView} vệ tinh — đưa ăng-ten ra ngoài trời)` : " (ra ngoài trời)"}`;
      return;
    }
    if (msg.kind === "ALERT") {
      const ab = document.getElementById("alert-banner");
      ab.style.display = "block";
      ab.textContent = `CẢNH BÁO: ${msg.alertType}${msg.detail ? " — " + msg.detail : ""}`;
      speakAlert(`Cảnh báo lái xe ${msg.alertType}`);
      logLine(`ALERT -> ${msg.raw}`);
      return;
    }
    if (msg.kind === "ACK") {
      logLine(`ESP2 xác nhận: ${msg.action}`);
      if (msg.action === "MUTE_ALERT") {
        const ab = document.getElementById("alert-banner");
        ab.style.display = "none";
      }
      return;
    }
    if (msg.kind === "WARN") {
      showTopStatus(msg.message, false);
      logLine(`WARN -> ${msg.message}`);
      return;
    }
    logLine(msg.raw || JSON.stringify(msg));
  }

  function showTopStatus(msg, isError) {
    const cb = document.getElementById("compat-banner");
    if (!cb) return;
    if (!msg) { cb.style.display = "none"; return; }
    cb.style.display = "block";
    cb.className = isError ? "banner alert" : "banner warn";
    cb.textContent = msg;
  }

  root.uiConnectBle = async function () {
    const badge = document.getElementById("ble-status-badge");
    try {
      showTopStatus("", false);
      if (badge) { badge.textContent = "ĐANG KẾT NỐI..."; badge.className = "badge warn"; }
      if (!client) {
        client = new DriverBleClient({
          onStateChange: onBleState,
          onMessage: onBleMessage,
          onWarn: (w) => { logLine(w); showTopStatus(w, false); },
        });
      }
      await client.connect();
    } catch (err) {
      if (badge) { badge.textContent = "LỖI KẾT NỐI"; badge.className = "badge err"; }
      const msg = `Lỗi BLE: ${err && err.message ? err.message : err}`;
      logLine(msg);
      showTopStatus(msg, true);
    }
  };

  root.uiDisconnectBle = function () {
    if (client) client.disconnect();
  };

  root.uiSendBle = async function (cmd) {
    try {
      await client.sendCommand(cmd);
      logLine(`Đã gửi BLE -> ${cmd}`);
    } catch (err) {
      logLine(`Gửi thất bại (${cmd}): ${err.message}`);
    }
  };

  let wakeLock = null;
  async function syncWakeLock(active) {
    try {
      if (active && navigator.wakeLock && !wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      } else if (!active && wakeLock) {
        await wakeLock.release();
        wakeLock = null;
      }
    } catch (_) {}
  }

  root.uiSendSos = async function () {
    let bleOk = false;
    if (client && client.connected) {
      try {
        await client.sendCommand("SOS");
        logLine("Đã gửi BLE -> SOS");
        bleOk = true;
      } catch (err) {
        logLine(`Gửi BLE SOS thất bại: ${err.message}`);
      }
    }
    // Avoid duplicate CRITICAL SOS_PRESSED incidents: use direct HTTP fallback only if BLE didn't send it
    if (!bleOk && phoneToken) {
      try {
        const r = await fetch("/api/v1/phone/action", {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${phoneToken}` },
          body: JSON.stringify({ vehicle_id: pairedVehicle, action: "SOS", detail: { source: "PWA_HTTP_FALLBACK" } }),
        });
        logLine(r.ok ? "Đã gửi SOS dự phòng qua HTTP Server." : `Gửi SOS HTTP lỗi (${r.status}).`);
      } catch (_) {}
    }
  };

  root.uiRedeemOtp = async function () {
    const vid = (document.getElementById("pair-vehicle").value || "XE-01").trim();
    const otp = (document.getElementById("pair-otp").value || "").trim().toUpperCase();
    const st = document.getElementById("pair-status");
    if (!otp) { st.textContent = "Vui lòng nhập mã OTP do quản trị viên cấp."; return; }
    try {
      const r = await fetch("/api/v1/phone/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vehicle_id: vid, pair_code: otp }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      phoneToken = data.driver_phone_token;
      pairedVehicle = vid;
      localStorage.setItem("pi_ai_driver_phone_token", phoneToken);
      localStorage.setItem("pi_ai_driver_vehicle_id", pairedVehicle);
      st.textContent = `Đã ghép Server thành công cho xe ${vid} (token lưu cục bộ).`;
    } catch (err) {
      st.textContent = `Ghép OTP thất bại: ${err.message}`;
    }
  };

  let phoneGeoWatchId = null;
  let lastPhoneGeoPostMs = 0;

  root.uiTogglePhoneGps = function () {
    const btn = document.getElementById("btn-phone-gps");
    const out = document.getElementById("phone-gps-status");
    if (phoneGeoWatchId !== null) {
      if (navigator.geolocation) navigator.geolocation.clearWatch(phoneGeoWatchId);
      phoneGeoWatchId = null;
      if (btn) btn.textContent = "Bật chia sẻ GPS thật của điện thoại (dự phòng)";
      if (out) out.textContent = "Đã dừng chia sẻ GPS điện thoại.";
      logLine("Đã tắt GPS điện thoại.");
      return;
    }
    if (!navigator.geolocation) {
      if (out) out.textContent = "Trình duyệt không hỗ trợ GPS (Geolocation API).";
      return;
    }
    if (!window.isSecureContext) {
      if (out) out.textContent = "Cần mở qua HTTPS (Cloudflare Tunnel) hoặc localhost để đọc GPS điện thoại.";
      return;
    }
    if (btn) btn.textContent = "Dừng chia sẻ GPS điện thoại";
    if (out) out.textContent = "Đang lấy tọa độ GNSS thật từ chip GPS điện thoại...";
    phoneGeoWatchId = navigator.geolocation.watchPosition(
      async (pos) => {
        const body = buildPhoneGpsTelemetry(pairedVehicle, pos);
        if (!body) {
          if (out) out.textContent = `GPS điện thoại sai số quá lớn (±${Math.round(pos.coords.accuracy || 0)}m) — đang chờ tín hiệu tốt hơn...`;
          return;
        }
        const g = body.phone_gps;
        if (out) {
          out.textContent = `GPS Điện thoại: ${g.lat.toFixed(6)}, ${g.lon.toFixed(6)} (±${g.accuracy_m ?? "?"}m · ${g.speed_kmh} km/h)`;
        }
        const now = Date.now();
        if (phoneToken && now - lastPhoneGeoPostMs >= 3000) {
          lastPhoneGeoPostMs = now;
          try {
            const r = await fetch("/api/v1/phone/telemetry", {
              method: "POST",
              headers: { "Content-Type": "application/json", Authorization: `Bearer ${phoneToken}` },
              body: JSON.stringify(body),
            });
            if (!r.ok && out) out.textContent += ` [Server HTTP ${r.status}]`;
          } catch (_) {}
        } else if (!phoneToken && out) {
          out.textContent += " (Hãy ghép OTP bên dưới để gửi lên Server Dashboard)";
        }
      },
      (err) => {
        if (out) out.textContent = `Lỗi GPS điện thoại: ${err.message}`;
        logLine(`Lỗi GPS điện thoại: ${err.message}`);
      },
      { enableHighAccuracy: true, maximumAge: 3000, timeout: 10000 }
    );
  };

  window.addEventListener("DOMContentLoaded", () => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("./sw.js").catch(() => {});
    }
    const sup = DriverBleClient.checkBrowserSupport();
    const cb = document.getElementById("compat-banner");
    if (!sup.supported && cb) {
      cb.style.display = "block";
      cb.textContent = sup.reason;
    }
    if (phoneToken) {
      const st = document.getElementById("pair-status");
      if (st) st.textContent = `Đã ghép Server trước đó cho xe ${pairedVehicle}.`;
    }
  });
})(typeof window !== "undefined" ? window : globalThis);
