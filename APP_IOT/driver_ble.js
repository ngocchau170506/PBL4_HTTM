/**
 * APP_IOT — Standalone 3-Source Driver Cockpit PWA (Pi Cục Bộ :8080 · Server PC :5000 · ESP32 #2 BLE).
 *
 * Protocol Contract (LCK-05 v3 — MUST NOT drift from firmware/esp32_ble_wifi_hub/esp32_ble_wifi_hub.ino):
 *   - Service UUID : 6e400001-b5a3-f393-e0a9-e50e24dcca9e (Nordic UART Service)
 *   - RX (Write)   : 6e400002-b5a3-f393-e0a9-e50e24dcca9e -> ASCII + '\n' (MUTE|SOS|ACK|REST_START|REST_END|CALIBRATE|STATUS)
 *   - TX (Notify)  : 6e400003-b5a3-f393-e0a9-e50e24dcca9e <- ALERT:... | GPS:{...} | STATUS:{...} | OK:... | ERR:...
 */
(function (root) {
  "use strict";

  const BLE_NUS = Object.freeze({
    SERVICE_UUID: "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
    CHAR_RX_UUID: "6e400002-b5a3-f393-e0a9-e50e24dcca9e",
    CHAR_TX_UUID: "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
    DEVICE_PREFIX: "PI_AI_HUB_",
    ALLOWED_CMDS: new Set([
      "MUTE",
      "DISMISS_ALARM",
      "MODE_AUTO",
      "MODE_PAUSE",
      "MODE_SLEEP",
      "SOS",
      "ACK",
      "REST_START",
      "REST_END",
      "CALIBRATE",
      "STATUS",
    ]),
  });

  const PI_LOCAL_ACTION_MAP = Object.freeze({
    MUTE: "MUTE",
    DISMISS_ALARM: "DISMISS_DROWSY_ALARM",
    MODE_AUTO: "MODE_AUTO",
    MODE_PAUSE: "MODE_PAUSE_30M",
    MODE_SLEEP: "MODE_REST_OFF",
    CALIBRATE: "CALIBRATE",
    REST_START: "REST_BREAK_REQUEST",
    STATUS: "PING",
    SOS: null,
    ACK: null,
    REST_END: null,
  });

  const SERVER_HTTP_ACTION_MAP = Object.freeze({
    MUTE: "MUTE_ALERT",
    DISMISS_ALARM: "DISMISS_DROWSY_ALARM",
    MODE_AUTO: "MODE_AUTO",
    MODE_PAUSE: "MODE_PAUSE_30M",
    MODE_SLEEP: "MODE_REST_OFF",
    SOS: "SOS",
    ACK: "ACK_AWAKE",
    REST_START: "REST_BREAK_START",
    REST_END: "REST_BREAK_END",
    CALIBRATE: "CALIBRATE_LOCK",
    STATUS: null,
  });

  /**
   * Normalizes user-entered or origin base URL: trims whitespace, strips trailing '/', accepts only http(s)://.
   */
  function normalizeBaseUrl(raw) {
    const s = String(raw || "").trim().replace(/\/+$/, "");
    if (!s || !/^https?:\/\/[^/\s]+/i.test(s)) return "";
    try {
      const u = new URL(s);
      if (u.protocol !== "http:" && u.protocol !== "https:") return "";
      if (!u.hostname) return "";
      const cleanPath = (u.pathname || "").replace(/\/+$/, "");
      return `${u.protocol}//${u.host}${cleanPath}`;
    } catch (_) {
      return "";
    }
  }

  /**
   * Detects endpoint target kind from /api/v1/health JSON payload.
   */
  function detectTargetKind(healthJson) {
    if (!healthJson || typeof healthJson !== "object") return "unknown";
    if (healthJson.target === "pi_local" || "web_mode" in healthJson) return "pi_local";
    if (healthJson.service === "pi_ai_central_server") return "server_http";
    return "unknown";
  }

  /**
   * Formats live AI engine status (MediaPipe 10s calibration vs YOLO monitoring vs NO_CAMERA).
   */
  function formatAiEngineStatus(driverState, calibration) {
    const ds = driverState && typeof driverState === "object" ? driverState : {};
    const cal = calibration && typeof calibration === "object" ? calibration : {};

    if (cal.state === "RUNNING") {
      const prog = Number(cal.progress_s || 0);
      const dur = Number(cal.duration_s || 10);
      return {
        mode: "MEDIAPIPE_10S",
        title: "MediaPipe 478 đang chạy (YOLO tạm tắt)",
        detail: `Tiến độ cân chỉnh: ${prog.toFixed(1)}s / ${dur.toFixed(0)}s — ngồi thẳng, mở mắt tự nhiên.`,
        tone: "running",
      };
    }

    if (ds.alert_reason === "NO_CAMERA") {
      return {
        mode: "NO_CAMERA",
        title: "Chưa có tín hiệu Camera",
        detail: "Camera trên xe chưa sẵn sàng hoặc đang mất tín hiệu.",
        tone: "warn",
      };
    }

    const locked = Boolean(cal.locked);
    const title = locked
      ? "YOLO đang giám sát (MediaPipe đã tắt) · Đã khóa cấu hình"
      : "YOLO đang giám sát (MediaPipe đã tắt)";
    const parts = [];
    if (cal.ear_closed_threshold != null && Number.isFinite(Number(cal.ear_closed_threshold))) {
      parts.push(`Ngưỡng mắt EAR ≤ ${Number(cal.ear_closed_threshold).toFixed(2)}`);
    }
    if (ds.alert_type) {
      parts.push(`Trạng thái: ${ds.alert_type}`);
    }
    return {
      mode: "YOLO_ACTIVE",
      title,
      detail: parts.length ? parts.join(" · ") : "Đang giám sát tài xế bằng YOLO thời gian thực.",
      tone: "ok",
    };
  }

  /**
   * Maps unified cockpit command names to transport-specific command/action payloads.
   */
  function mapActionForTransport(cmd, transport) {
    const cleanCmd = String(cmd || "").trim().toUpperCase();
    const cleanTransport = String(transport || "").trim().toLowerCase();
    if (!cleanCmd) return null;
    if (cleanTransport === "ble") {
      return BLE_NUS.ALLOWED_CMDS.has(cleanCmd) ? cleanCmd : null;
    }
    if (cleanTransport === "pi_local") {
      return Object.prototype.hasOwnProperty.call(PI_LOCAL_ACTION_MAP, cleanCmd)
        ? PI_LOCAL_ACTION_MAP[cleanCmd]
        : null;
    }
    if (cleanTransport === "server_http") {
      return Object.prototype.hasOwnProperty.call(SERVER_HTTP_ACTION_MAP, cleanCmd)
        ? SERVER_HTTP_ACTION_MAP[cleanCmd]
        : null;
    }
    return null;
  }

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
      const alertType = (parts[0] || "WARNING").trim().toUpperCase();
      const detail = parts.slice(1).join("|").trim();
      const isCalibration = alertType.startsWith("CALIB_");
      const isAdvisoryInfo = isCalibration || alertType === "INFO";
      return {
        kind: "ALERT",
        alertType,
        detail,
        isCalibration,
        isDangerAlert: !isAdvisoryInfo,
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
          ? "BLE cần HTTPS — bạn vẫn dùng đầy đủ các nút qua kết nối Pi/Server phía trên!"
          : !hasBle
          ? "Trình duyệt này không hỗ trợ Web Bluetooth — bạn vẫn dùng đầy đủ các nút qua kết nối Pi/Server phía trên!"
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
          diag = ` | Device=${this.device.name || "?"} | (GATT cache rong — hay Go ghep doi trong Settings > Bluetooth neu da pair cu)`;
        }
        throw new Error((lastErr ? lastErr.message : "Khong tim thay NUS service") + diag);
      }
      this.rxChar = await service.getCharacteristic(BLE_NUS.CHAR_RX_UUID);
      this.txChar = await service.getCharacteristic(BLE_NUS.CHAR_TX_UUID);
      this.txChar.addEventListener("characteristicvaluechanged", (evt) => this._onNotify(evt));
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
      const next = this._writeChain.catch(() => {}).then(async () => {
        if (!this.connected || !this.rxChar) throw new Error("BLE chưa kết nối");
        if (typeof this.rxChar.writeValueWithoutResponse === "function") {
          try {
            await this.rxChar.writeValueWithoutResponse(bytes);
            return;
          } catch (_) {}
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

  const CALIB_FAIL_REASONS = Object.freeze({
    NO_FACE: "Không thấy rõ khuôn mặt trước camera",
    LOW_SAMPLES: "Chưa thu đủ mẫu mắt mở ổn định",
    NO_CAMERA: "Camera trên xe chưa sẵn sàng",
    CALIBRATION_BUSY: "Đang trong tiến trình cân chỉnh 10s",
  });

  function classifyCalibrationNotice(msg) {
    if (!msg || typeof msg !== "object") return null;
    if (msg.kind === "ACK" && msg.action === "CALIBRATE_LOCK") {
      return {
        handled: true,
        triggerDangerSiren: false,
        tone: "running",
        countdownSec: 10,
        statusText: "Đang nhận diện tư thế (10s)... Hãy ngồi thẳng & mở mắt tự nhiên.",
        ttsText: "",
      };
    }
    if (msg.kind !== "ALERT") return null;
    const t = String(msg.alertType || "").toUpperCase();
    const det = String(msg.detail || "").trim();
    if (t === "CALIB_START") {
      const m = det.match(/(\d+(?:\.\d+)?)/);
      const sec = m ? Math.max(1, Math.min(30, Math.round(Number(m[1])))) : 10;
      return {
        handled: true,
        triggerDangerSiren: false,
        tone: "running",
        countdownSec: sec,
        statusText: `Đang nhận diện tư thế (${sec}s)... Hãy ngồi thẳng & mở mắt tự nhiên.`,
        ttsText: "Bắt đầu nhận diện tư thế mười giây",
      };
    }
    if (t === "CALIB_PROGRESS") {
      return {
        handled: true,
        triggerDangerSiren: false,
        tone: "running",
        countdownSec: null,
        statusText: `Đang nhận diện tư thế${det ? " (" + det + ")" : ""}... Giữ thẳng đầu & mở mắt.`,
        ttsText: "",
      };
    }
    if (t === "CALIB_DONE") {
      const isTemp = det.toLowerCase() === "temp";
      return {
        handled: true,
        triggerDangerSiren: false,
        tone: "ok",
        countdownSec: 0,
        statusText: isTemp
          ? "Đã cân chỉnh tư thế đầu giờ (10s tạm thời)."
          : "Đã nhận diện & khóa cấu hình tài xế!",
        ttsText: isTemp ? "Đã cân chỉnh tư thế tài xế" : "Đã lưu cấu hình tài xế",
      };
    }
    if (t === "CALIB_FAIL") {
      const key = det.toUpperCase();
      const reasonVi = CALIB_FAIL_REASONS[key] || det || "Chưa đạt điều kiện mẫu";
      return {
        handled: true,
        triggerDangerSiren: false,
        tone: "warn",
        countdownSec: 0,
        statusText: `Cân chỉnh chưa thành công (${reasonVi}) — hãy thử lại.`,
        ttsText: "Chưa nhận diện được tư thế, vui lòng thử lại",
      };
    }
    if (t === "INFO") {
      return {
        handled: true,
        triggerDangerSiren: false,
        tone: "running",
        countdownSec: null,
        statusText: det || "Thông báo hệ thống",
        ttsText: "",
      };
    }
    return null;
  }

  function resolveCalibTimeoutNotice(httpOnlyMode, currentTone = "running") {
    if (httpOnlyMode) {
      return {
        tone: "warn",
        statusText: "Đã gửi lệnh cân chỉnh 10s qua Server — vui lòng kiểm tra trạng thái trên xe/Dashboard",
        ttsText: "",
      };
    }
    if (currentTone === "running") {
      return {
        tone: "warn",
        statusText: "Đang chờ Pi xác nhận kết quả cân chỉnh...",
        ttsText: "",
      };
    }
    return null;
  }

  function shouldFinishHttpCalibration(cal, sawRunning, baselineProfileTs) {
    if (!cal || typeof cal !== "object") return false;
    const state = String(cal.state || "").toUpperCase();
    if (state !== "DONE" && state !== "FAILED") return false;
    if (sawRunning) return true;
    const ts = cal.profile_ts;
    return ts != null && ts !== baselineProfileTs;
  }

  function formatDrowsyEscalationStatus(escObj) {
    const e = escObj && typeof escObj === "object" ? escObj : {};
    const state = String(e.state || "IDLE").toUpperCase();
    const count = Math.max(0, Number(e.count || 0));
    const limit = Math.max(2, Number(e.limit || 3));
    const winMin = Math.round(Number(e.window_s || 180) / 60);
    const probSec = Math.max(0, Math.ceil(Number(e.probation_remaining_s || 0)));
    const mm = String(Math.floor(probSec / 60)).padStart(1, "0");
    const ss = String(probSec % 60).padStart(2, "0");

    if (state === "CONTINUOUS_RELAPSE") {
      return {
        state,
        count,
        limit,
        alarmActive: true,
        badgeText: "TÁI PHẠM — BÁO ĐỘNG LIÊN TỤC!",
        badgeClass: "badge err",
        boxClass: "drowsy-box crit",
        title: "🚨 TÁI PHẠM BUỒN NGỦ — BÁO ĐỘNG LIÊN TỤC!",
        detail: "Bạn vừa ngủ gục lại trong thời gian kiểm tra! Hãy dừng xe nghỉ ngơi ngay lập tức.",
      };
    }
    if (state === "ALARM_LATCHED") {
      return {
        state,
        count,
        limit,
        alarmActive: true,
        badgeText: `BÁO ĐỘNG LẦN ${count}/${limit}`,
        badgeClass: "badge err",
        boxClass: "drowsy-box crit",
        title: `🔔 BÁO ĐỘNG BUỒN NGỦ LẦN ${count}/${limit} ĐANG KÊU!`,
        detail: `Bấm nút đỏ bên dưới để tắt loa ESP32 #1 — Pi sẽ kiểm tra lại trong ${winMin} phút tới.`,
      };
    }
    if (state === "PROBATION") {
      return {
        state,
        count,
        limit,
        alarmActive: false,
        badgeText: `KIỂM TRA LẠI ${mm}:${ss}`,
        badgeClass: "badge warn",
        boxClass: "drowsy-box prob",
        title: `⏳ Đang kiểm tra lại độ tỉnh táo (${mm}:${ss})`,
        detail: "Nếu bạn ngủ gục lại trước khi hết giờ kiểm tra, loa sẽ phát cảnh báo liên tục!",
      };
    }
    if (count > 0) {
      return {
        state,
        count,
        limit,
        alarmActive: false,
        badgeText: `BUỒN NGỦ ${count}/${limit}`,
        badgeClass: "badge warn",
        boxClass: "drowsy-box warn",
        title: `⚠️ Đã ghi nhận buồn ngủ lần ${count}/${limit} (trong ${winMin} phút)`,
        detail: `Nếu đạt đủ ${limit} lần liên tiếp trong ${winMin} phút, ESP32 #1 sẽ bật báo động khóa liên tục.`,
      };
    }
    return {
      state: "IDLE",
      count: 0,
      limit,
      alarmActive: false,
      badgeText: `TỈNH TÁO 0/${limit}`,
      badgeClass: "badge ok",
      boxClass: "drowsy-box",
      title: "Trạng thái: Đang theo dõi tỉnh táo",
      detail: `Lần 1–2 nhắc loa ESP32 #1; đủ ${limit} lần liên tiếp trong ${winMin} phút sẽ bật báo động khóa.`,
    };
  }

  function formatMonitoringModeStatus(monObj, faceDetected) {
    const m = monObj && typeof monObj === "object" ? monObj : {};
    const mode = String(m.mode || "AUTO").toUpperCase();
    const hasPerson = m.presence ? m.presence === "DRIVER_PRESENT" : Boolean(faceDetected);
    const pauseSec = Math.max(0, Math.ceil(Number(m.pause_remaining_s || 0)));
    const pMin = Math.floor(pauseSec / 60);
    const pSec = String(pauseSec % 60).padStart(2, "0");
    const resumeSpd = Number(m.auto_resume_speed_kmh || 15);

    const presenceText = hasPerson ? "🟢 CAM THẤY NGƯỜI" : "🟡 CAM KHÔNG THẤY NGƯỜI";
    const presenceClass = hasPerson ? "badge ok" : "badge warn";

    if (mode === "REST_SLEEP_OFF") {
      return {
        mode,
        hasPerson,
        presenceText,
        presenceClass,
        detail: `Chế độ 3 (Nghỉ / Ngủ trên xe): Camera vẫn giám sát, tắt toàn bộ loa cảnh báo. Hệ thống tự bật lại AUTO khi xe chạy ≥ ${resumeSpd} km/h hoặc khi Pi khởi động lại.`,
      };
    }
    if (mode === "TEMP_PAUSE_30M") {
      return {
        mode,
        hasPerson,
        presenceText,
        presenceClass,
        detail: `Chế độ 2 (Tạm tắt vắng mặt — còn ${pMin}:${pSec}): Đang tắt cảnh báo không thấy người. Nếu cam vẫn thấy tài xế, mọi cảnh báo vi phạm vẫn kêu bình thường!`,
      };
    }
    return {
      mode: "AUTO",
      hasPerson,
      presenceText,
      presenceClass,
      detail: "Chế độ 1 (AUTO — mặc định khi bật Pi): Tự xác định thấy/không thấy người; vắng mặt quá 15–30s sẽ báo App/Server rồi tự tạm tắt vắng mặt 30p.",
    };
  }

  function canAccessAdminActions(targetKindVal, piAuthedVal, piRoleVal, srvAdminAuthedVal, srvAdminRoleVal) {
    const pr = String(piRoleVal || "").toLowerCase();
    const sr = String(srvAdminRoleVal || "").toLowerCase();
    if (targetKindVal === "pi_local") {
      return Boolean(piAuthedVal && (pr === "admin" || pr === "operator"));
    }
    if (targetKindVal === "server_http") {
      return Boolean(srvAdminAuthedVal && (sr === "admin" || sr === "operator"));
    }
    return false;
  }

  const DISPUTE_REASONS = Object.freeze({
    FALSE_DETECTION: "AI nhận diện nhầm hành vi",
    SENSOR_ERROR: "Nhiễu cảm biến MQ-135 / GPS",
    NOT_ME: "Đã đổi tài nhưng chưa kịp cập nhật",
    OTHER: "Lý do khác",
  });

  function classifySafetyScoreTier(scoreVal) {
    const score = Number.isFinite(Number(scoreVal)) ? Math.max(0, Math.min(100, Number(scoreVal))) : 100;
    if (score <= 20) {
      return {
        score: Math.round(score),
        tier: "RED_LOCKED",
        badgeText: "BÁO ĐỘNG ĐỎ",
        badgeClass: "badge err",
        heroClass: "score-hero err",
        circleColor: "#dc2626",
        description: "Điểm ≤ 20: Tài khoản tạm khóa nhận ca mới. Liên hệ Quản trị viên hoặc gửi khiếu nại vi phạm sai.",
      };
    }
    if (score <= 65) {
      return {
        score: Math.round(score),
        tier: "YELLOW_WARN",
        badgeText: "CẢNH CÁO VÀNG",
        badgeClass: "badge warn",
        heroClass: "score-hero warn",
        circleColor: "#d97706",
        description: "Điểm ≤ 65: Giảm 30% tần suất gợi ý ca đêm. Nếu giảm xuống ≤ 20 điểm sẽ bị khóa nhận ca.",
      };
    }
    return {
      score: Math.round(score),
      tier: "GREEN_GOOD",
      badgeText: "HOẠT ĐỘNG TỐT",
      badgeClass: "badge ok",
      heroClass: "score-hero ok",
      circleColor: "#16a34a",
      description: "Điểm > 65: Tài xế đạt chuẩn an toàn cao. Đủ điều kiện nhận ca đêm và hành trình dài.",
    };
  }

  function validateDisputeSubmission(violationEventId, reasonCode, explanation) {
    const eid = String(violationEventId || "").trim();
    const code = String(reasonCode || "").trim().toUpperCase();
    const text = String(explanation || "").trim();
    if (!eid) return { ok: false, error: "Thiếu mã vi phạm cần khiếu nại." };
    if (!Object.prototype.hasOwnProperty.call(DISPUTE_REASONS, code)) {
      return { ok: false, error: "Lý do khiếu nại không hợp lệ." };
    }
    if (text.length < 5) return { ok: false, error: "Vui lòng nhập giải trình ít nhất 5 ký tự." };
    return { ok: true, violation_event_id: eid, reason_code: code, explanation: text.slice(0, 500) };
  }

  // Export pure helpers for Node.js automated tests (APP_IOT/test_driver_ble.mjs)
  const exportsObj = {
    BLE_NUS,
    DISPUTE_REASONS,
    normalizeBaseUrl,
    detectTargetKind,
    formatAiEngineStatus,
    formatDrowsyEscalationStatus,
    formatMonitoringModeStatus,
    canAccessAdminActions,
    classifySafetyScoreTier,
    validateDisputeSubmission,
    mapActionForTransport,
    shouldFinishHttpCalibration,
    BleLineFramingParser,
    parseEsp2Notification,
    classifyCalibrationNotice,
    resolveCalibTimeoutNotice,
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

  const STATUS_POLL_MS = 2500;

  let client = null;
  let activeBaseUrl = "";
  let targetKind = "unknown"; // "pi_local" | "server_http" | "unknown"
  let piLocalToken = localStorage.getItem("pi_ai_pi_local_token") || "";
  let piLocalUrl = localStorage.getItem("pi_ai_pi_local_url") || "";
  let piLocalRole = (localStorage.getItem("pi_ai_pi_local_role") || "").toLowerCase();
  let piAuthed = false;

  let phoneToken = localStorage.getItem("pi_ai_driver_phone_token") || "";
  let pairedVehicle = localStorage.getItem("pi_ai_driver_vehicle_id") || "XE-01";
  let serverPaired = false;

  // Admin portal state (supports Server Admin/Operator OR Pi Local Admin/Operator)
  let activePortal = localStorage.getItem("pi_ai_active_portal") === "admin" ? "admin" : "driver";
  let serverAdminToken = sessionStorage.getItem("pi_ai_admin_token") || "";
  let serverAdminRole = (sessionStorage.getItem("pi_ai_admin_role") || "").toLowerCase();
  let serverAdminAuthed = false;
  let adminConfigHydrated = false;

  let statusPollTimer = null;
  let pollInFlight = false;
  let lastBleGpsMs = 0;

  let calibTimer = null;
  let calibRemainingSec = 0;
  let calibPendingHttpResult = false;
  let lastKnownCalibProfileTs = null;
  let calibBaselineProfileTs = null;
  let calibSawRunning = false;
  let lastHttpCalibState = "IDLE";
  let lastCalmTtsText = "";
  let lastCalmTtsMs = 0;

  function logLine(msg) {
    const el = document.getElementById("ble-log");
    if (!el) return;
    const ts = new Date().toLocaleTimeString("vi-VN");
    el.textContent = `[${ts}] ${msg}\n` + el.textContent.slice(0, 2600);
  }

  function getPageOrigin() {
    if (typeof window === "undefined" || !window.location) return "";
    if (window.location.protocol !== "http:" && window.location.protocol !== "https:") return "";
    return normalizeBaseUrl(window.location.origin);
  }

  function setHint(id, text, tone) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.className = tone ? `hint-line ${tone}` : "hint-line";
  }

  function isBleReady() {
    return Boolean(client && client.connected);
  }

  function isPiLocalReady() {
    return Boolean(activeBaseUrl && targetKind === "pi_local" && piAuthed && piLocalToken);
  }

  function isPiLocalControlReady() {
    return Boolean(isPiLocalReady() && piLocalRole !== "viewer");
  }

  function isServerReady() {
    return Boolean(activeBaseUrl && targetKind === "server_http" && serverPaired && phoneToken);
  }

  function resolveTransportForAction(cmd) {
    const candidates = [];
    if (isBleReady()) candidates.push("ble");
    if (isPiLocalControlReady()) candidates.push("pi_local");
    if (isServerReady()) candidates.push("server_http");
    for (const tr of candidates) {
      const mapped = mapActionForTransport(cmd, tr);
      if (mapped !== null) return { transport: tr, mapped };
    }
    return null;
  }

  function syncTopBadgesAndControls() {
    const connBadge = document.getElementById("conn-status-badge");
    if (connBadge) {
      if (isPiLocalControlReady()) {
        connBadge.textContent = "PI CỤC BỘ OK";
        connBadge.className = "badge ok";
      } else if (isPiLocalReady()) {
        connBadge.textContent = "PI CHỈ XEM";
        connBadge.className = "badge warn";
      } else if (isServerReady()) {
        connBadge.textContent = "SERVER OK";
        connBadge.className = "badge ok";
      } else if (targetKind === "pi_local") {
        connBadge.textContent = "PI CHỜ MÃ";
        connBadge.className = "badge warn";
      } else if (targetKind === "server_http") {
        connBadge.textContent = "SERVER CHỜ OTP";
        connBadge.className = "badge warn";
      } else {
        connBadge.textContent = "CHƯA NỐI HTTP";
        connBadge.className = "badge";
      }
    }

    // Enable/disable every action button according to available channels
    document.querySelectorAll("[data-action]").forEach((btn) => {
      const cmd = btn.getAttribute("data-action");
      if (cmd === "CALIBRATE") return; // handled by syncCalibrateButtonState
      const route = resolveTransportForAction(cmd);
      btn.disabled = !route;
    });

    const bd = document.getElementById("btn-disconnect");
    if (bd) bd.disabled = !isBleReady();

    // Update channel note below quick actions
    const readyList = [];
    if (isBleReady()) readyList.push("ESP32 #2 BLE");
    if (isPiLocalControlReady()) readyList.push("Pi Cục Bộ");
    if (isServerReady()) readyList.push(`Server (${pairedVehicle})`);

    if (readyList.length === 0) {
      if (isPiLocalReady() && piLocalRole === "viewer") {
        setHint(
          "actions-channel-note",
          "Đang ở chế độ CHỈ XEM (viewer) — nhập WEB_OPERATOR_TOKEN hoặc WEB_ADMIN_TOKEN để bấm điều khiển!",
          "warn"
        );
      } else {
        setHint("actions-channel-note", "Chưa có kênh điều khiển sẵn sàng (ưu tiên tự động: BLE → Pi cục bộ → Server HTTP).", "");
      }
    } else if (isPiLocalControlReady() && !isBleReady() && !isServerReady()) {
      setHint(
        "actions-channel-note",
        "Đang chạy trực tiếp qua Pi Cục Bộ: hỗ trợ Tắt còi, Bắt đầu nghỉ, Cân chỉnh 10s & Kiểm tra trạng thái (SOS / Tôi đang tỉnh / Kết thúc nghỉ cần BLE hoặc Server).",
        "ok"
      );
    } else {
      setHint("actions-channel-note", `Kênh sẵn sàng: ${readyList.join(" + ")} (tự điều phối ưu tiên BLE → Pi → Server).`, "ok");
    }

    syncCalibrateButtonState();
    syncAdminControls();
    syncStatusPolling();
  }

  function isAdminReady() {
    return canAccessAdminActions(targetKind, piAuthed, piLocalRole, serverAdminAuthed, serverAdminRole);
  }

  function syncAdminControls() {
    const ready = isAdminReady();
    const badge = document.getElementById("admin-auth-badge");
    if (badge) {
      if (ready) {
        const rName = targetKind === "pi_local" ? piLocalRole : serverAdminRole;
        badge.textContent = `ĐÃ ĐĂNG NHẬP (${String(rName).toUpperCase()})`;
        badge.className = "badge ok";
      } else {
        badge.textContent = "CHƯA ĐĂNG NHẬP";
        badge.className = "badge warn";
      }
    }
    document.querySelectorAll(".admin-ctrl").forEach((el) => {
      el.disabled = !ready;
    });
    const mintBtn = document.getElementById("btn-admin-mint-otp");
    if (mintBtn) {
      mintBtn.disabled = !(targetKind === "server_http" && serverAdminAuthed);
    }
    const otpCard = document.getElementById("admin-otp-card");
    if (otpCard) {
      otpCard.style.display = targetKind === "pi_local" ? "none" : "block";
    }
    const dashLink = document.getElementById("admin-open-dashboard-link");
    if (dashLink && activeBaseUrl) {
      dashLink.href = `${activeBaseUrl}/`;
    }
  }

  function renderDrowsyEscalationBox(escObj) {
    const info = formatDrowsyEscalationStatus(escObj);
    const badge = document.getElementById("drowsy-state-badge");
    const box = document.getElementById("drowsy-meter-box");
    const titleEl = document.getElementById("drowsy-meter-title");
    const detailEl = document.getElementById("drowsy-meter-detail");
    const dismissBtn = document.getElementById("btn-dismiss-alarm");

    if (badge) { badge.textContent = info.badgeText; badge.className = info.badgeClass; }
    if (box) box.className = info.boxClass;
    if (titleEl) titleEl.textContent = info.title;
    if (detailEl) detailEl.textContent = info.detail;

    for (let i = 1; i <= 3; i++) {
      const dot = document.getElementById(`drowsy-dot-${i}`);
      if (!dot) continue;
      if (info.alarmActive || info.count >= info.limit) {
        dot.className = "strike-dot lit-crit";
      } else if (i <= info.count) {
        dot.className = "strike-dot lit-warn";
      } else {
        dot.className = "strike-dot";
      }
    }
    if (dismissBtn) {
      dismissBtn.classList.toggle("pulsing", Boolean(info.alarmActive));
    }
  }

  function renderMonitoringModeBox(monObj, faceDetected) {
    const info = formatMonitoringModeStatus(monObj, faceDetected);
    const pBadge = document.getElementById("presence-badge");
    const detailEl = document.getElementById("monitoring-detail");
    const bAuto = document.getElementById("btn-mode-auto");
    const bPause = document.getElementById("btn-mode-pause");
    const bSleep = document.getElementById("btn-mode-sleep");

    if (pBadge) { pBadge.textContent = info.presenceText; pBadge.className = info.presenceClass; }
    if (detailEl) detailEl.textContent = info.detail;
    if (bAuto) bAuto.className = "btn btn-mode" + (info.mode === "AUTO" ? " active-auto" : "");
    if (bPause) bPause.className = "btn btn-mode" + (info.mode === "TEMP_PAUSE_30M" ? " active-pause" : "");
    if (bSleep) bSleep.className = "btn btn-mode" + (info.mode === "REST_SLEEP_OFF" ? " active-sleep" : "");
  }

  function hydrateAdminConfigFields(cfg) {
    if (!cfg || typeof cfg !== "object" || adminConfigHydrated) return;
    adminConfigHydrated = true;
    const setCheck = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.checked = Boolean(val); };
    const setNum = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = String(val); };
    setCheck("adm-cfg-smoking", cfg.allow_smoking);
    setCheck("adm-cfg-phone", cfg.allow_phone);
    setNum("adm-cfg-drowsy-win", cfg.drowsy_window_s);
    setNum("adm-cfg-drowsy-lim", cfg.drowsy_escalation_limit);
    setNum("adm-cfg-absence-s", cfg.absence_alert_s);
    setNum("adm-cfg-pause-min", cfg.pause_duration_min);
    setNum("adm-cfg-resume-spd", cfg.auto_resume_speed_kmh);
    setNum("adm-cfg-smoke-conf", cfg.smoking_conf_threshold);
  }

  root.uiSwitchPortal = function (portal) {
    activePortal = portal === "admin" ? "admin" : "driver";
    localStorage.setItem("pi_ai_active_portal", activePortal);
    const pDrv = document.getElementById("portal-driver");
    const pAdm = document.getElementById("portal-admin");
    const tDrv = document.getElementById("tab-btn-driver");
    const tAdm = document.getElementById("tab-btn-admin");
    if (pDrv) pDrv.classList.toggle("hidden", activePortal !== "driver");
    if (pAdm) pAdm.classList.toggle("hidden", activePortal !== "admin");
    if (tDrv) tDrv.classList.toggle("active", activePortal === "driver");
    if (tAdm) tAdm.classList.toggle("active", activePortal === "admin");
    syncAdminControls();
    if (activePortal === "admin" && targetKind === "server_http" && serverAdminAuthed) {
      root.uiAdminRefreshDisputes();
    }
  };

  function escHtml(v) {
    return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  let activeDriverSubTab = "cockpit";
  let pendingDisputeViolation = null;
  let selectedDisputeReason = "FALSE_DETECTION";
  let latestDriverViolations = [];

  root.uiSwitchDriverSubTab = function (sub) {
    activeDriverSubTab = sub === "score" ? "score" : "cockpit";
    const pCockpit = document.getElementById("driver-pane-cockpit");
    const pScore = document.getElementById("driver-pane-score");
    const bCockpit = document.getElementById("drv-sub-cockpit");
    const bScore = document.getElementById("drv-sub-score");
    if (pCockpit) pCockpit.classList.toggle("hidden", activeDriverSubTab !== "cockpit");
    if (pScore) pScore.classList.toggle("hidden", activeDriverSubTab !== "score");
    if (bCockpit) bCockpit.classList.toggle("active", activeDriverSubTab === "cockpit");
    if (bScore) bScore.classList.toggle("active", activeDriverSubTab === "score");
    if (activeDriverSubTab === "score") {
      root.uiRefreshDriverScoreAndViolations();
    }
  };

  function resolveEvidenceFullUrl(relOrAbs, tok) {
    if (!relOrAbs) return "";
    const s = String(relOrAbs).trim();
    const full = s.startsWith("http://") || s.startsWith("https://")
      ? s
      : (activeBaseUrl ? `${activeBaseUrl}${s.startsWith("/") ? "" : "/"}${s}` : s);
    if (!tok || full.includes("token=")) return full;
    return `${full}${full.includes("?") ? "&" : "?"}token=${encodeURIComponent(tok)}`;
  }

  root.uiOpenEvidenceModal = function (title, videoUrl, imageUrl, tok) {
    const modal = document.getElementById("evidence-modal");
    const tEl = document.getElementById("evidence-modal-title");
    const vidEl = document.getElementById("evidence-video");
    const imgEl = document.getElementById("evidence-img");
    const noteEl = document.getElementById("evidence-modal-note");
    if (tEl) tEl.textContent = title || "🎬 Bằng chứng Vi phạm";
    if (vidEl) { vidEl.pause(); vidEl.src = ""; vidEl.style.display = "none"; }
    if (imgEl) { imgEl.src = ""; imgEl.style.display = "none"; }

    const vFull = resolveEvidenceFullUrl(videoUrl, tok);
    const iFull = resolveEvidenceFullUrl(imageUrl, tok);
    if (vFull && vidEl) {
      vidEl.src = vFull;
      vidEl.style.display = "block";
      if (noteEl) noteEl.textContent = "Video 10s ghép từ ảnh keyframe sạch của Pi 4.";
    } else if (iFull && imgEl) {
      imgEl.src = iFull;
      imgEl.style.display = "block";
      if (noteEl) noteEl.textContent = "Ảnh keyframe bằng chứng tại thời điểm phát hiện vi phạm.";
    } else if (noteEl) {
      noteEl.textContent = "Sự kiện này không có video/ảnh đính kèm.";
    }
    if (modal) modal.classList.remove("hidden");
  };

  root.uiCloseEvidenceModal = function () {
    const modal = document.getElementById("evidence-modal");
    const vidEl = document.getElementById("evidence-video");
    if (vidEl) { vidEl.pause(); vidEl.src = ""; }
    if (modal) modal.classList.add("hidden");
  };

  root.uiSelectDisputeReason = function (code) {
    selectedDisputeReason = Object.prototype.hasOwnProperty.call(DISPUTE_REASONS, code) ? code : "FALSE_DETECTION";
    document.querySelectorAll("#dispute-reason-list .reason-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-reason") === selectedDisputeReason);
    });
  };

  root.uiOpenDisputeModal = function (eventId) {
    const v = latestDriverViolations.find((x) => x.event_id === eventId);
    if (!v) return;
    pendingDisputeViolation = v;
    const modal = document.getElementById("dispute-modal");
    const titleEl = document.getElementById("dispute-modal-title");
    const subEl = document.getElementById("dispute-modal-sub");
    const txtEl = document.getElementById("dispute-explanation");
    const errEl = document.getElementById("dispute-modal-err");
    if (titleEl) titleEl.textContent = `⚖️ Khiếu nại Vi phạm ${v.event_id}`;
    if (subEl) subEl.textContent = `Lỗi: ${v.alert_type} (-${v.penalty_points || 0} điểm) • Quyền khiếu nại 1 lần trong 48 giờ (Four-Eyes Check).`;
    if (txtEl) txtEl.value = "";
    if (errEl) errEl.textContent = "";
    root.uiSelectDisputeReason("FALSE_DETECTION");
    if (modal) modal.classList.remove("hidden");
  };

  root.uiCloseDisputeModal = function () {
    pendingDisputeViolation = null;
    const modal = document.getElementById("dispute-modal");
    if (modal) modal.classList.add("hidden");
  };

  root.uiSubmitDispute = async function () {
    const errEl = document.getElementById("dispute-modal-err");
    const txtEl = document.getElementById("dispute-explanation");
    if (errEl) errEl.textContent = "";
    if (!pendingDisputeViolation || !isServerReady()) {
      if (errEl) errEl.textContent = "Cần ghép OTP Server PC để gửi đơn khiếu nại.";
      return;
    }
    const check = validateDisputeSubmission(pendingDisputeViolation.event_id, selectedDisputeReason, txtEl ? txtEl.value : "");
    if (!check.ok) {
      if (errEl) errEl.textContent = check.error;
      return;
    }
    try {
      const r = await fetch(`${activeBaseUrl}/api/v1/phone/disputes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${phoneToken}` },
        body: JSON.stringify({
          vehicle_id: pairedVehicle,
          violation_event_id: check.violation_event_id,
          reason_code: check.reason_code,
          explanation: check.explanation,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      root.uiCloseDisputeModal();
      showTopStatus(`✅ Đã gửi đơn khiếu nại ${data.dispute_id} thành công! Chờ Quản trị viên kiểm duyệt.`, false);
      logLine(`Đã gửi đơn khiếu nại ${data.dispute_id} cho vi phạm ${check.violation_event_id}`);
      root.uiRefreshDriverScoreAndViolations();
    } catch (err) {
      if (errEl) errEl.textContent = `Gửi khiếu nại thất bại: ${err.message}`;
    }
  };

  root.uiRefreshDriverScoreAndViolations = async function () {
    const violHost = document.getElementById("driver-violations-list");
    const tripsHost = document.getElementById("driver-trips-list");
    const cntBadge = document.getElementById("driver-viol-count");
    if (!isServerReady()) {
      if (violHost) {
        violHost.innerHTML = `<div class="hint-line">Cần kết nối &amp; ghép OTP <strong>Server PC (:5000)</strong> ở tab Buồng lái để xem điểm uy tín, lịch sử vi phạm và gửi khiếu nại 48h.</div>`;
      }
      return;
    }
    try {
      const r = await fetch(
        `${activeBaseUrl}/api/v1/phone/score_summary?vehicle_id=${encodeURIComponent(pairedVehicle)}`,
        { headers: { Authorization: `Bearer ${phoneToken}` } }
      );
      if (r.status === 401) {
        clearServerTokenOn401();
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const tier = classifySafetyScoreTier(d.current_score);
      const hero = document.getElementById("score-hero-box");
      const circle = document.getElementById("score-circle");
      const numEl = document.getElementById("driver-score-num");
      const badgeEl = document.getElementById("driver-score-badge");
      const nameEl = document.getElementById("driver-score-name");
      const descEl = document.getElementById("driver-score-desc");
      if (hero) hero.className = tier.heroClass;
      if (circle) circle.style.borderColor = tier.circleColor;
      if (numEl) { numEl.textContent = String(tier.score); numEl.style.color = tier.circleColor; }
      if (badgeEl) { badgeEl.textContent = tier.badgeText; badgeEl.className = tier.badgeClass; }
      if (nameEl) nameEl.textContent = `${d.driver_name || "Tài xế"} · Xe ${d.vehicle_id || pairedVehicle}`;
      if (descEl) descEl.textContent = tier.description;

      if (tripsHost) {
        const trips = Array.isArray(d.trips) ? d.trips.slice(0, 5) : [];
        tripsHost.innerHTML = trips.length
          ? trips.map((t) => {
              const dt = t.started_at ? new Date(Number(t.started_at) * 1000).toLocaleString("vi-VN") : "-";
              return `<div class="v-item">
                <div class="v-head">
                  <div class="v-title">Chuyến ${escHtml(t.trip_id)} (${escHtml(t.status || "ACTIVE")})</div>
                  <span class="badge ${Number(t.safety_score) <= 65 ? "warn" : "ok"}">${Math.round(Number(t.safety_score ?? 100))} đ</span>
                </div>
                <div class="v-meta">Bắt đầu: ${escHtml(dt)} · Quãng đường: ${Number(t.distance_km || 0).toFixed(1)} km · Vi phạm: ${escHtml(t.violation_count || 0)} · Tắt còi: ${escHtml(t.mute_count || 0)}</div>
              </div>`;
            }).join("")
          : `<div class="hint-line">Chưa có lịch sử chuyến đi nào được ghi nhận.</div>`;
      }

      const viols = Array.isArray(d.violations) ? d.violations : [];
      latestDriverViolations = viols;
      if (cntBadge) cntBadge.textContent = `${viols.length} vi phạm`;
      if (violHost) {
        if (!viols.length) {
          violHost.innerHTML = `<div class="v-item"><div class="v-title">Chưa có vi phạm nào</div><div class="v-meta">Lịch sử lái xe của bạn hiện đang an toàn.</div></div>`;
        } else {
          violHost.innerHTML = viols.map((v) => {
            const dt = v.created_at ? new Date(Number(v.created_at) * 1000).toLocaleString("vi-VN") : "-";
            const isRefunded = v.human_review_status === "HUMAN_FALSE_POSITIVE" || v.dispute_status === "APPROVED";
            const penPts = Number(v.penalty_points || 0);
            const penHtml = penPts > 0
              ? `<span class="v-penalty ${isRefunded ? "refunded" : ""}">-${penPts} đ</span>${isRefunded ? `<div style="font-size:11px;color:#86efac;font-weight:700">Đã hoàn điểm</div>` : ""}`
              : `<span class="badge">0đ (Cảnh báo)</span>`;
            let dspBadge = "";
            if (v.dispute_status === "OPEN") dspBadge = `<span class="badge warn">⏳ Đang chờ duyệt khiếu nại</span>`;
            else if (v.dispute_status === "APPROVED") dspBadge = `<span class="badge ok">✅ Khiếu nại ĐƯỢC CHẤP NHẬN</span>`;
            else if (v.dispute_status === "REJECTED") dspBadge = `<span class="badge err">❌ Khiếu nại bị từ chối</span>`;

            const hasMedia = Boolean(v.video_url || v.image_url);
            return `<div class="v-item">
              <div class="v-head">
                <div>
                  <div class="v-title">${escHtml(v.alert_type)} · ${escHtml(v.event_id)}</div>
                  <div class="v-meta">${escHtml(dt)}${v.behavior_detail ? " · " + escHtml(v.behavior_detail) : ""}</div>
                </div>
                <div style="text-align:right">${penHtml}</div>
              </div>
              ${dspBadge ? `<div style="margin-top:6px">${dspBadge}${v.dispute_response_note ? `<div class="v-meta">Phản hồi Admin: ${escHtml(v.dispute_response_note)}</div>` : ""}</div>` : ""}
              <div class="v-actions">
                ${hasMedia ? `<button class="btn btn-ghost" onclick="uiOpenEvidenceModal('${escHtml(v.alert_type + " - " + v.event_id)}','${escHtml(v.video_url || "")}','${escHtml(v.image_url || "")}','${escHtml(phoneToken)}')">🎬 Xem bằng chứng</button>` : ""}
                ${v.can_dispute ? `<button class="btn btn-primary" onclick="uiOpenDisputeModal('${escHtml(v.event_id)}')">⚖️ Khiếu nại (48h)</button>` : ""}
              </div>
            </div>`;
          }).join("");
        }
      }
    } catch (err) {
      if (violHost) violHost.innerHTML = `<div class="hint-line err">Chưa tải được điểm &amp; vi phạm: ${escHtml(err.message)}</div>`;
    }
  };

  root.uiAdminRefreshDisputes = async function () {
    const host = document.getElementById("admin-disputes-list");
    if (!host) return;
    if (targetKind !== "server_http" || !serverAdminAuthed) {
      host.innerHTML = `<div class="hint-line">Đăng nhập Quản trị Server PC để tải và duyệt đơn khiếu nại từ tài xế.</div>`;
      return;
    }
    try {
      const vid = (document.getElementById("admin-vehicle-id")?.value || "").trim();
      const q = vid ? `?vehicle_id=${encodeURIComponent(vid)}` : "";
      const r = await fetch(`${activeBaseUrl}/api/v1/disputes${q}`, {
        headers: { "X-Dashboard-Token": serverAdminToken },
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      const rows = Array.isArray(data.disputes) ? data.disputes : [];
      if (!rows.length) {
        host.innerHTML = `<div class="hint-line ok">Hiện không có đơn khiếu nại nào cần xử lý.</div>`;
        return;
      }
      host.innerHTML = rows.map((d) => {
        const isOpen = d.status === "OPEN";
        const stBadge = d.status === "APPROVED"
          ? `<span class="badge ok">ĐÃ HOÀN ĐIỂM</span>`
          : d.status === "REJECTED"
          ? `<span class="badge err">TỪ CHỐI</span>`
          : `<span class="badge warn">CHỜ DUYỆT</span>`;
        const hasMedia = Boolean(d.video_url || d.image_url);
        return `<div class="v-item">
          <div class="v-head">
            <div>
              <div class="v-title">${escHtml(d.dispute_id)} · ${escHtml(d.alert_type || "VIOLATION")} (-${escHtml(d.penalty_points || 0)}đ)</div>
              <div class="v-meta">Xe ${escHtml(d.vehicle_id)} · Lý do: <strong>${escHtml(d.reason_label || d.reason_code)}</strong></div>
            </div>
            ${stBadge}
          </div>
          <div class="v-meta" style="color:#e2e8f0;margin-top:6px">Giải trình: "${escHtml(d.explanation)}"</div>
          ${d.response_note ? `<div class="v-meta">Phản hồi (${escHtml(d.resolved_by || "admin")}): ${escHtml(d.response_note)}</div>` : ""}
          <div class="v-actions">
            ${hasMedia ? `<button class="btn btn-ghost" onclick="uiOpenEvidenceModal('${escHtml(d.dispute_id)}','${escHtml(d.video_url || "")}','${escHtml(d.image_url || "")}','${escHtml(serverAdminToken)}')">🎬 Xem video</button>` : ""}
            ${isOpen ? `
              <input id="dsp-note-${escHtml(d.dispute_id)}" placeholder="Nhập lý do duyệt / từ chối..." style="flex:1;min-width:160px;padding:7px 9px;font-size:12px" />
              <button class="btn btn-ack" onclick="uiAdminResolveDispute('${escHtml(d.dispute_id)}','APPROVED')">✅ Hoàn điểm</button>
              <button class="btn btn-sos" onclick="uiAdminResolveDispute('${escHtml(d.dispute_id)}','REJECTED')">❌ Từ chối</button>
            ` : ""}
          </div>
        </div>`;
      }).join("");
    } catch (err) {
      host.innerHTML = `<div class="hint-line err">Lỗi tải đơn khiếu nại: ${escHtml(err.message)}</div>`;
    }
  };

  root.uiAdminResolveDispute = async function (disputeId, decision) {
    const noteEl = document.getElementById(`dsp-note-${disputeId}`);
    const note = (noteEl ? noteEl.value : "").trim();
    if (!note) {
      showTopStatus("Bắt buộc nhập nội dung phản hồi cho tài xế trước khi duyệt!", true);
      return;
    }
    try {
      const r = await fetch(`${activeBaseUrl}/api/v1/disputes/${encodeURIComponent(disputeId)}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Dashboard-Token": serverAdminToken },
        body: JSON.stringify({ decision, response_note: note }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      showTopStatus(`Đã xử lý đơn ${disputeId}: ${decision}${data.refunded ? " (Đã hoàn điểm an toàn)" : ""}`, false);
      logLine(`Admin xử lý khiếu nại ${disputeId} -> ${decision}`);
      root.uiAdminRefreshDisputes();
    } catch (err) {
      showTopStatus(`Duyệt khiếu nại lỗi: ${err.message}`, true);
    }
  };

  root.uiDismissDrowsyAlarm = async function () {
    try {
      await dispatchAction("DISMISS_ALARM");
      const ab = document.getElementById("alert-banner");
      if (ab) ab.style.display = "none";
      const dismissBtn = document.getElementById("btn-dismiss-alarm");
      if (dismissBtn) dismissBtn.classList.remove("pulsing");
      speakCalmInfo("Đã tắt loa cảnh báo buồn ngủ, hệ thống đang kiểm tra lại trong vài phút tới");
      setTimeout(() => { pollLiveStatus(); }, 700);
    } catch (err) {
      logLine(`Tắt loa báo động thất bại: ${err.message}`);
    }
  };

  root.uiSetMonitoringMode = async function (cmd) {
    try {
      await dispatchAction(cmd);
      const mapLocal = { MODE_AUTO: "AUTO", MODE_PAUSE: "TEMP_PAUSE_30M", MODE_SLEEP: "REST_SLEEP_OFF" };
      renderMonitoringModeBox({ mode: mapLocal[cmd] || "AUTO" }, true);
      setTimeout(() => { pollLiveStatus(); }, 700);
    } catch (err) {
      logLine(`Đổi chế độ giám sát thất bại (${cmd}): ${err.message}`);
    }
  };

  root.uiLoginAdmin = async function () {
    const inp = document.getElementById("admin-token-input");
    const tok = (inp ? inp.value : "").trim();
    if (!activeBaseUrl) {
      setHint("admin-auth-status", "Hãy kiểm tra địa chỉ Pi hoặc Server ở tab Tài xế trước.", "warn");
      return;
    }
    if (!tok) {
      setHint("admin-auth-status", "Vui lòng nhập token Admin hoặc Operator.", "warn");
      return;
    }
    try {
      if (targetKind === "pi_local") {
        const r = await fetch(`${activeBaseUrl}/api/web_login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: tok }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
        const role = String(data.role || "").toLowerCase();
        if (role !== "admin" && role !== "operator") {
          throw new Error(`Token này chỉ có quyền '${role}', cần operator hoặc admin`);
        }
        piLocalToken = tok;
        piLocalUrl = activeBaseUrl;
        piLocalRole = role;
        piAuthed = true;
        localStorage.setItem("pi_ai_pi_local_token", tok);
        localStorage.setItem("pi_ai_pi_local_url", activeBaseUrl);
        localStorage.setItem("pi_ai_pi_local_role", role);
        if (inp) inp.value = "";
        setHint("admin-auth-status", `Đã đăng nhập Quản trị Pi Cục Bộ (vai trò: ${role}).`, "ok");
        logLine(`Admin đăng nhập Pi Cục Bộ thành công (${role}).`);
        adminConfigHydrated = false;
        syncTopBadgesAndControls();
        pollLiveStatus();
        return;
      }

      // Server PC login (validates via /api/v1/auth/me)
      const r = await fetch(`${activeBaseUrl}/api/v1/auth/me`, {
        headers: { "X-Dashboard-Token": tok },
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      const role = String(data.role || "").toLowerCase();
      if (role !== "admin" && role !== "operator") {
        throw new Error(`Token này chỉ có quyền '${role}', cần operator hoặc admin`);
      }
      serverAdminToken = tok;
      serverAdminRole = role;
      serverAdminAuthed = true;
      sessionStorage.setItem("pi_ai_admin_token", tok);
      sessionStorage.setItem("pi_ai_admin_role", role);
      if (inp) inp.value = "";
      setHint("admin-auth-status", `Đã đăng nhập Quản trị Server PC (vai trò: ${role}).`, "ok");
      logLine(`Admin đăng nhập Server PC thành công (${role}).`);
      adminConfigHydrated = false;
      syncTopBadgesAndControls();
      // Fetch current remote_config for selected vehicle
      const vid = (document.getElementById("admin-vehicle-id")?.value || pairedVehicle || "XE-01").trim();
      const rc = await fetch(`${activeBaseUrl}/api/v1/config/${encodeURIComponent(vid)}`, {
        headers: { "X-Dashboard-Token": serverAdminToken },
      });
      if (rc.ok) hydrateAdminConfigFields(await rc.json());
      root.uiAdminRefreshDisputes();
    } catch (err) {
      setHint("admin-auth-status", `Đăng nhập Quản trị thất bại: ${err.message}`, "err");
      logLine(`Admin đăng nhập lỗi: ${err.message}`);
    }
  };

  root.uiLogoutAdmin = function () {
    serverAdminToken = "";
    serverAdminRole = "";
    serverAdminAuthed = false;
    sessionStorage.removeItem("pi_ai_admin_token");
    sessionStorage.removeItem("pi_ai_admin_role");
    if (targetKind === "pi_local") {
      root.uiClearPiToken();
    }
    setHint("admin-auth-status", "Đã đăng xuất phiên Quản trị.", "");
    syncAdminControls();
  };

  root.uiAdminMintOtp = async function () {
    if (targetKind !== "server_http" || !serverAdminAuthed) return;
    const vid = (document.getElementById("admin-vehicle-id")?.value || pairedVehicle || "XE-01").trim() || "XE-01";
    const drv = (document.getElementById("admin-otp-driver")?.value || "").trim();
    const out = document.getElementById("admin-otp-result");
    try {
      const r = await fetch(`${activeBaseUrl}/api/v1/phone/pair_otp`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Dashboard-Token": serverAdminToken },
        body: JSON.stringify({ vehicle_id: vid, driver_id: drv || undefined }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      if (out) {
        out.style.display = "block";
        out.textContent = `MÃ OTP XE ${vid}: ${data.pair_code} (hết hạn sau 5 phút)`;
      }
      logLine(`Admin đã tạo OTP cho xe ${vid}: ${data.pair_code}`);
    } catch (err) {
      logLine(`Tạo OTP lỗi: ${err.message}`);
      showTopStatus(`Tạo OTP thất bại: ${err.message}`, true);
    }
  };

  root.uiAdminSendCmd = async function (cmd) {
    if (!isAdminReady()) {
      setHint("admin-auth-status", "Hãy đăng nhập Quản trị trước khi gửi lệnh.", "warn");
      return;
    }
    try {
      if (targetKind === "pi_local") {
        const res = await sendViaPiLocal(cmd);
        logLine(`Admin (Pi Cục Bộ) -> ${cmd} (${res.detail || "OK"})`);
        showTopStatus(`Đã gửi lệnh ${cmd} tới Pi (${res.detail || "OK"})`, false);
      } else {
        const vid = (document.getElementById("admin-vehicle-id")?.value || pairedVehicle || "XE-01").trim() || "XE-01";
        const r = await fetch(`${activeBaseUrl}/api/v1/server/command`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Dashboard-Token": serverAdminToken },
          body: JSON.stringify({ vehicle_id: vid, cmd }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        logLine(`Admin (Server) -> ${cmd} tới xe ${vid} (${data.cmd_uid || "PENDING"})`);
        showTopStatus(`Đã xếp hàng lệnh ${cmd} xuống xe ${vid}`, false);
      }
      setTimeout(() => { pollLiveStatus(); }, 800);
    } catch (err) {
      logLine(`Admin gửi lệnh ${cmd} lỗi: ${err.message}`);
      showTopStatus(`Gửi lệnh ${cmd} lỗi: ${err.message}`, true);
    }
  };

  root.uiAdminSaveConfig = async function () {
    if (!isAdminReady()) return;
    const patch = {
      allow_smoking: Boolean(document.getElementById("adm-cfg-smoking")?.checked),
      allow_phone: Boolean(document.getElementById("adm-cfg-phone")?.checked),
      drowsy_window_s: Number(document.getElementById("adm-cfg-drowsy-win")?.value || 180),
      drowsy_escalation_limit: Number(document.getElementById("adm-cfg-drowsy-lim")?.value || 3),
      absence_alert_s: Number(document.getElementById("adm-cfg-absence-s")?.value || 20),
      pause_duration_min: Number(document.getElementById("adm-cfg-pause-min")?.value || 30),
      auto_resume_speed_kmh: Number(document.getElementById("adm-cfg-resume-spd")?.value || 15),
      smoking_conf_threshold: Number(document.getElementById("adm-cfg-smoke-conf")?.value || 0.4),
    };
    try {
      if (targetKind === "pi_local") {
        const r = await fetch(`${activeBaseUrl}/api/config`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Auth-Token": piLocalToken },
          body: JSON.stringify(patch),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
        setHint("admin-cfg-status", "Đã lưu & áp dụng cấu hình trực tiếp lên Pi 4!", "ok");
        logLine("Admin đã cập nhật cấu hình trực tiếp trên Pi 4.");
      } else {
        const vid = (document.getElementById("admin-vehicle-id")?.value || pairedVehicle || "XE-01").trim() || "XE-01";
        const r = await fetch(`${activeBaseUrl}/api/v1/config/${encodeURIComponent(vid)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Dashboard-Token": serverAdminToken },
          body: JSON.stringify(patch),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
        setHint("admin-cfg-status", `Đã lưu Remote Config cho xe ${vid} (Pi tự đồng bộ trong 1.5s)!`, "ok");
        logLine(`Admin đã lưu Remote Config cho xe ${vid}.`);
      }
    } catch (err) {
      setHint("admin-cfg-status", `Lưu cấu hình thất bại: ${err.message}`, "err");
      logLine(`Admin lưu cấu hình lỗi: ${err.message}`);
    }
  };

  function syncCalibrateButtonState() {
    const btn = document.getElementById("btn-calibrate");
    const lbl = document.getElementById("btn-calibrate-label");
    if (!btn) return;
    const canSend = Boolean(resolveTransportForAction("CALIBRATE"));
    if (calibTimer !== null) {
      btn.disabled = true;
      if (lbl) lbl.textContent = `⏳ MediaPipe 10s đang chạy... (${calibRemainingSec}s)`;
    } else {
      btn.disabled = !canSend;
      if (lbl) lbl.textContent = "🎯 Bật MediaPipe 10s (Tạm tắt YOLO & Giữ cấu hình)";
    }
  }

  function setCalibBanner(tone, text) {
    const el = document.getElementById("calib-status");
    if (!el) return;
    el.className = tone ? `calib-status ${tone}` : "calib-status";
    el.textContent = text;
  }

  function stopCalibCountdown() {
    if (calibTimer !== null) {
      clearInterval(calibTimer);
      calibTimer = null;
    }
    calibRemainingSec = 0;
    syncCalibrateButtonState();
  }

  function startCalibCountdown(totalSec, httpOnlyMode = false) {
    if (calibTimer !== null) clearInterval(calibTimer);
    calibRemainingSec = Math.max(1, Math.round(Number(totalSec) || 10));
    syncCalibrateButtonState();
    setCalibBanner(
      "running",
      `MediaPipe 478 đang chạy (${calibRemainingSec}s)... Hãy ngồi thẳng & mở mắt tự nhiên.`
    );
    calibTimer = setInterval(() => {
      calibRemainingSec -= 1;
      if (calibRemainingSec <= 0) {
        stopCalibCountdown();
        const el = document.getElementById("calib-status");
        const curTone = el && el.classList.contains("ok") ? "ok"
          : (el && el.classList.contains("warn") ? "warn" : "running");
        const timeoutNotice = resolveCalibTimeoutNotice(httpOnlyMode, curTone);
        if (timeoutNotice) {
          setCalibBanner(timeoutNotice.tone, timeoutNotice.statusText);
          if (timeoutNotice.ttsText) speakCalmInfo(timeoutNotice.ttsText);
        }
        return;
      }
      syncCalibrateButtonState();
      const el = document.getElementById("calib-status");
      if (el && el.classList.contains("running")) {
        el.textContent = `MediaPipe 478 đang chạy (còn ${calibRemainingSec}s)... Giữ thẳng đầu & mở mắt.`;
      }
    }, 1000);
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

  function speakCalmInfo(text) {
    if (!text) return;
    const now = Date.now();
    if (text === lastCalmTtsText && now - lastCalmTtsMs < 3500) return;
    lastCalmTtsText = text;
    lastCalmTtsMs = now;
    try {
      if (window.speechSynthesis) {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = "vi-VN";
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
      }
    } catch (_) {}
  }

  function showTopStatus(msg, isError) {
    const cb = document.getElementById("compat-banner");
    if (!cb) return;
    if (!msg) { cb.style.display = "none"; return; }
    cb.style.display = "block";
    cb.className = isError ? "banner alert" : "banner warn";
    cb.textContent = msg;
  }

  function updateAuthPanelsVisibility() {
    const piBox = document.getElementById("auth-box-pi");
    const srvBox = document.getElementById("auth-box-server");
    if (piBox) piBox.style.display = targetKind === "pi_local" ? "block" : "none";
    if (srvBox) srvBox.style.display = targetKind === "pi_local" ? "none" : "block";
  }

  function clearServerTokenOn401(reasonMsg) {
    phoneToken = "";
    serverPaired = false;
    localStorage.removeItem("pi_ai_driver_phone_token");
    setHint(
      "pair-status",
      reasonMsg || "Token Server cũ đã hết hạn (401 — có thể bạn vừa đổi Server mới). Vui lòng nhập OTP mới.",
      "warn"
    );
    logLine("Đã tự động xóa token Server cũ do phản hồi 401.");
    syncTopBadgesAndControls();
  }

  function clearPiTokenOnAuthFail(reasonMsg) {
    piLocalToken = "";
    piLocalUrl = "";
    piLocalRole = "";
    piAuthed = false;
    localStorage.removeItem("pi_ai_pi_local_token");
    localStorage.removeItem("pi_ai_pi_local_url");
    localStorage.removeItem("pi_ai_pi_local_role");
    setHint("pi-auth-status", reasonMsg || "Mã truy cập Pi không hợp lệ (401/403). Vui lòng nhập lại.", "warn");
    syncTopBadgesAndControls();
  }

  function applyPiLocalRoleFromPayload(roleRaw) {
    if (!roleRaw) return;
    const normRole = String(roleRaw).trim().toLowerCase();
    if (!normRole) return;
    const changed = normRole !== piLocalRole;
    piLocalRole = normRole;
    localStorage.setItem("pi_ai_pi_local_role", piLocalRole);
    if (piAuthed) {
      if (piLocalRole === "viewer") {
        setHint(
          "pi-auth-status",
          "Đang ở chế độ CHỈ XEM (viewer) — nhập WEB_OPERATOR_TOKEN hoặc WEB_ADMIN_TOKEN để bấm điều khiển!",
          "warn"
        );
      } else {
        setHint("pi-auth-status", `Đã kết nối Pi Cục Bộ (vai trò: ${piLocalRole}).`, "ok");
      }
      if (changed) syncTopBadgesAndControls();
    }
  }

  function renderAiEngineBox(driverState, calibration) {
    const st = formatAiEngineStatus(driverState, calibration);
    const box = document.getElementById("ai-engine-box");
    const titleEl = document.getElementById("ai-engine-title");
    const detailEl = document.getElementById("ai-engine-detail");
    if (box) box.className = `ai-engine-box ${st.tone}`;
    if (titleEl) titleEl.textContent = st.title;
    if (detailEl) detailEl.textContent = st.detail;
  }

  function applyLiveTelemetryToUi(data, sourceLabel) {
    const rootObj = data && data.state && typeof data.state === "object" ? data.state : (data || {});
    const ds = (data && data.driver_state) || rootObj.driver_state || {};
    const cal =
      (data && data.calibration && Object.keys(data.calibration).length ? data.calibration : null) ||
      ds.calibration ||
      {};
    const gps = (data && data.gps) || rootObj.gps || {};
    const pi = (data && data.pi) || rootObj.pi || {};
    const cam = (data && data.camera) || rootObj.camera || {};
    const esp = (data && data.esp) || rootObj.esp || {};
    const dEsc = (data && data.drowsy_escalation) || ds.drowsy_escalation || {};
    const mon = (data && data.monitoring) || ds.monitoring || {};

    renderAiEngineBox(ds, cal);
    renderDrowsyEscalationBox(dEsc);
    renderMonitoringModeBox(mon, Boolean(cam.face_detected));

    const volBadge = document.getElementById("admin-esp-vol-badge");
    if (volBadge && esp && esp.vol != null) {
      volBadge.textContent = `VOL: ${esp.vol}%${esp.alarm_mode ? " · ALARM!" : ""}`;
      volBadge.className = "badge " + (esp.alarm_mode ? "err" : "ok");
    }
    const liveCfg = (data && (data.config || data.remote_config)) || null;
    if (liveCfg) hydrateAdminConfigFields(liveCfg);

    // Update Pi temperature metric
    const tempEl = document.getElementById("pi-temp");
    if (tempEl && pi.cpu_temp_c != null && Number.isFinite(Number(pi.cpu_temp_c))) {
      const tc = Number(pi.cpu_temp_c);
      tempEl.textContent = `${tc.toFixed(1)}°C`;
      tempEl.className = "val " + (tc >= 80 ? "err" : tc >= 70 ? "warn" : "ok");
    }

    // Update GPS metrics from HTTP if BLE GPS is not actively streaming
    const bleGpsFresh = isBleReady() && (Date.now() - lastBleGpsMs < 6000);
    if (!bleGpsFresh && gps && typeof gps === "object" && Object.keys(gps).length > 0) {
      const spdEl = document.getElementById("gps-speed");
      const satEl = document.getElementById("gps-sats");
      const fixEl = document.getElementById("gps-fix");
      const coordsEl = document.getElementById("gps-coords");
      const valid = Boolean(gps.is_valid || gps.fix);
      const spd = Number(gps.speed_kmh ?? gps.spd ?? 0);
      const sats = Number(gps.satellites ?? gps.sats ?? 0);
      const satsView = gps.sats_in_view ?? gps.sats_view ?? null;
      if (spdEl) spdEl.textContent = Number.isFinite(spd) ? spd.toFixed(1) : "0.0";
      if (satEl) satEl.textContent = satsView != null ? `${sats}/${satsView}` : String(sats);
      if (fixEl) {
        fixEl.textContent = valid ? "LOCKED" : "NO FIX";
        fixEl.className = "val " + (valid ? "ok" : "warn");
      }
      if (coordsEl) {
        if (valid && gps.lat != null && gps.lon != null) {
          coordsEl.textContent = `${sourceLabel} GPS: ${Number(gps.lat).toFixed(6)}, ${Number(gps.lon).toFixed(6)}${
            gps.hdop != null ? ` (HDOP ${Number(gps.hdop).toFixed(1)})` : ""
          }`;
        } else {
          coordsEl.textContent = `${sourceLabel} GPS: chưa khóa vệ tinh${satsView != null ? ` (thấy ${satsView} vệ tinh)` : ""}`;
        }
      }
    }

    // Sync calibration countdown / completion from HTTP polling (guarded against stale DONE/FAILED)
    const calState = String(cal.state || "IDLE").toUpperCase();
    if (calState === "RUNNING") {
      calibSawRunning = true;
      const prog = Number(cal.progress_s || 0);
      const dur = Number(cal.duration_s || 10);
      const rem = Math.max(1, Math.ceil(dur - prog));
      if (calibTimer === null) {
        startCalibCountdown(rem, !isBleReady());
      } else {
        calibRemainingSec = Math.min(calibRemainingSec, rem);
      }
      setCalibBanner("running", `MediaPipe 478 đang chạy (${prog.toFixed(1)}s / ${dur.toFixed(0)}s)... YOLO tạm tắt.`);
    } else if (calState === "DONE") {
      if (calibTimer !== null || calibPendingHttpResult || lastHttpCalibState === "RUNNING") {
        if (shouldFinishHttpCalibration(cal, calibSawRunning, calibBaselineProfileTs)) {
          stopCalibCountdown();
          calibPendingHttpResult = false;
          calibSawRunning = false;
          if (cal.locked === true) {
            setCalibBanner("ok", "Đã lưu cấu hình tài xế — YOLO đã bật lại");
            speakCalmInfo("Đã lưu cấu hình tài xế, YOLO đã bật lại");
          } else {
            setCalibBanner("ok", "Đã hoàn tất cân chỉnh 10s — YOLO đã bật lại");
          }
          logLine(`HTTP xác nhận cân chỉnh hoàn tất (${cal.locked ? "locked" : "temp"}).`);
        }
      } else if (cal.locked) {
        setCalibBanner("ok", "Đã lưu cấu hình cá nhân — YOLO đang giám sát.");
      }
    } else if (calState === "FAILED") {
      if (
        (calibTimer !== null || calibPendingHttpResult || lastHttpCalibState === "RUNNING") &&
        shouldFinishHttpCalibration(cal, calibSawRunning, calibBaselineProfileTs)
      ) {
        stopCalibCountdown();
        calibPendingHttpResult = false;
        calibSawRunning = false;
        setCalibBanner("warn", "Cân chỉnh thất bại (chưa thấy rõ mặt/thiếu mẫu) — YOLO đã bật lại, hãy thử lại.");
        speakCalmInfo("Cân chỉnh chưa thành công, vui lòng thử lại");
        logLine("HTTP xác nhận cân chỉnh thất bại (FAILED).");
      }
    }
    if (cal.profile_ts != null) {
      lastKnownCalibProfileTs = cal.profile_ts;
    }
    lastHttpCalibState = calState;
  }

  async function pollLiveStatus() {
    if (pollInFlight) return;
    if (!isPiLocalReady() && !isServerReady()) return;
    pollInFlight = true;
    try {
      if (isPiLocalReady()) {
        let r = await fetch(`${activeBaseUrl}/api/app_status`, {
          headers: { "X-Auth-Token": piLocalToken },
        });
        if (r.status === 404) {
          r = await fetch(`${activeBaseUrl}/api/live_meta`, {
            headers: { "X-Auth-Token": piLocalToken },
          });
        }
        if (r.status === 401 || r.status === 403) {
          clearPiTokenOnAuthFail("Phiên Pi cục bộ hết hạn hoặc sai mã (401/403). Vui lòng nhập lại.");
          return;
        }
        if (r.ok) {
          const data = await r.json();
          if (data && data.role) applyPiLocalRoleFromPayload(data.role);
          applyLiveTelemetryToUi(data, "Pi Cục Bộ");
        }
      } else if (isServerReady()) {
        const url = `${activeBaseUrl}/api/v1/phone/status?vehicle_id=${encodeURIComponent(pairedVehicle)}`;
        const r = await fetch(url, {
          headers: { Authorization: `Bearer ${phoneToken}` },
        });
        if (r.status === 401) {
          clearServerTokenOn401();
          return;
        }
        if (r.ok) {
          const data = await r.json();
          applyLiveTelemetryToUi(data, "Server HTTP");
        }
      }
    } catch (_) {
      // Transient network hiccup — next 2.5s tick retries automatically
    } finally {
      pollInFlight = false;
    }
  }

  function syncStatusPolling() {
    const shouldPoll = isPiLocalReady() || isServerReady();
    if (shouldPoll && statusPollTimer === null) {
      statusPollTimer = setInterval(pollLiveStatus, STATUS_POLL_MS);
      pollLiveStatus();
    } else if (!shouldPoll && statusPollTimer !== null) {
      clearInterval(statusPollTimer);
      statusPollTimer = null;
    }
  }

  root.uiProbeEndpoint = async function (saveUserChoice = false) {
    const input = document.getElementById("conn-base-url");
    const badge = document.getElementById("http-target-badge");
    const rawVal = input ? input.value : activeBaseUrl;
    const norm = normalizeBaseUrl(rawVal);

    if (!norm) {
      activeBaseUrl = "";
      targetKind = "unknown";
      piAuthed = false;
      serverPaired = false;
      if (badge) { badge.textContent = "SAI ĐỊA CHỈ"; badge.className = "badge err"; }
      setHint("http-target-detail", "Địa chỉ phải bắt đầu bằng http:// hoặc https:// (vd http://192.168.1.7:8080).", "err");
      updateAuthPanelsVisibility();
      syncTopBadgesAndControls();
      return;
    }

    if (input) input.value = norm;
    activeBaseUrl = norm;
    if (saveUserChoice) {
      localStorage.setItem("pi_ai_base_url", norm);
      localStorage.setItem("pi_ai_base_url_origin", getPageOrigin());
    }

    if (badge) { badge.textContent = "ĐANG KIỂM TRA..."; badge.className = "badge warn"; }
    setHint("http-target-detail", `Đang kiểm tra ${norm}/api/v1/health ...`, "");

    try {
      const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const to = ctrl ? setTimeout(() => ctrl.abort(), 5000) : null;
      const r = await fetch(`${norm}/api/v1/health`, {
        method: "GET",
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (to) clearTimeout(to);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const health = await r.json();
      targetKind = detectTargetKind(health);
      updateAuthPanelsVisibility();

      if (targetKind === "pi_local") {
        if (health.web_mode === "minimal_server" && health.enable_live_stream === false) {
          piAuthed = false;
          if (badge) { badge.textContent = "PI ẨN WEB"; badge.className = "badge warn"; }
          setHint(
            "http-target-detail",
            "Pi đang chạy chế độ ẩn web (ENABLE_LIVE_STREAM=0) — hãy bật --live-stream trên Pi hoặc chuyển sang Server :5000 / BLE",
            "warn"
          );
          logLine(`Cảnh báo Pi (${norm}): ENABLE_LIVE_STREAM=0 (minimal_server).`);
        } else {
          if (badge) { badge.textContent = "ĐÃ NỐI PI CỤC BỘ"; badge.className = "badge ok"; }
          setHint("http-target-detail", `Đã nối Pi Cục Bộ (${norm}) · chế độ ${health.web_mode || "pi_local"}.`, "ok");
          logLine(`Kiểm tra OK: Pi Cục Bộ tại ${norm}`);
          // Auto-verify saved Pi token for this URL
          if (piLocalToken && (!piLocalUrl || piLocalUrl === norm)) {
            await verifySavedPiToken();
          } else {
            piAuthed = false;
            setHint("pi-auth-status", "Nhập mã truy cập Pi (WEB_OPERATOR_TOKEN / WEB_ADMIN_TOKEN) để điều khiển.", "");
          }
        }
      } else if (targetKind === "server_http") {
        if (badge) { badge.textContent = "ĐÃ NỐI SERVER"; badge.className = "badge ok"; }
        setHint("http-target-detail", `Đã nối Server Trung Tâm (${norm}) · ${health.vehicle_count ?? 0} xe.`, "ok");
        logLine(`Kiểm tra OK: Server Trung Tâm tại ${norm}`);
        if (phoneToken) {
          serverPaired = true;
          setHint("pair-status", `Đã có token Server cho xe ${pairedVehicle} — đang kiểm tra phiên...`, "ok");
          await pollLiveStatus();
        } else {
          serverPaired = false;
        }
      } else {
        if (badge) { badge.textContent = "KHÔNG RÕ NGUỒN"; badge.className = "badge warn"; }
        setHint("http-target-detail", `Máy chủ phản hồi tại ${norm} nhưng không nhận diện được Pi/Server PI_AI.`, "warn");
      }
    } catch (err) {
      targetKind = "unknown";
      piAuthed = false;
      serverPaired = false;
      if (badge) { badge.textContent = "MẤT KẾT NỐI"; badge.className = "badge err"; }
      setHint("http-target-detail", `Không kết nối được ${norm} (${err && err.message ? err.message : err}).`, "err");
      logLine(`Mất kết nối tới ${norm}: ${err && err.message ? err.message : err}`);
      updateAuthPanelsVisibility();
    }
    syncTopBadgesAndControls();
  };

  async function verifySavedPiToken() {
    if (!activeBaseUrl || !piLocalToken) {
      piAuthed = false;
      return;
    }
    try {
      let r = await fetch(`${activeBaseUrl}/api/app_status`, {
        headers: { "X-Auth-Token": piLocalToken },
      });
      if (r.status === 404) {
        r = await fetch(`${activeBaseUrl}/api/live_meta`, {
          headers: { "X-Auth-Token": piLocalToken },
        });
      }
      if (r.ok) {
        const data = await r.json();
        piAuthed = true;
        piLocalUrl = activeBaseUrl;
        localStorage.setItem("pi_ai_pi_local_url", activeBaseUrl);
        if (data && data.role) {
          applyPiLocalRoleFromPayload(data.role);
        } else if (piLocalRole === "viewer") {
          setHint(
            "pi-auth-status",
            "Đang ở chế độ CHỈ XEM (viewer) — nhập WEB_OPERATOR_TOKEN hoặc WEB_ADMIN_TOKEN để bấm điều khiển!",
            "warn"
          );
        } else {
          setHint("pi-auth-status", `Đã đăng nhập Pi Cục Bộ (${activeBaseUrl}).`, "ok");
        }
        applyLiveTelemetryToUi(data, "Pi Cục Bộ");
      } else if (r.status === 401 || r.status === 403) {
        clearPiTokenOnAuthFail("Mã truy cập Pi cũ không còn hợp lệ (401/403). Hãy nhập mã mới.");
      }
    } catch (_) {
      piAuthed = false;
    }
  }

  root.uiUseCurrentOrigin = function () {
    const origin = getPageOrigin();
    const input = document.getElementById("conn-base-url");
    if (!origin) {
      setHint("http-target-detail", "Trang đang mở từ tệp cục bộ (file://) — hãy nhập IP Pi hoặc Server.", "warn");
      return;
    }
    localStorage.removeItem("pi_ai_base_url");
    localStorage.removeItem("pi_ai_base_url_origin");
    if (input) input.value = origin;
    root.uiProbeEndpoint(false);
  };

  root.uiLoginPiLocal = async function () {
    const inp = document.getElementById("pi-token-input");
    const tok = (inp ? inp.value : "").trim();
    if (!activeBaseUrl) {
      setHint("pi-auth-status", "Hãy kiểm tra địa chỉ Pi phía trên trước.", "warn");
      return;
    }
    if (!tok) {
      setHint("pi-auth-status", "Vui lòng nhập WEB_OPERATOR_TOKEN hoặc WEB_ADMIN_TOKEN.", "warn");
      return;
    }
    try {
      const r = await fetch(`${activeBaseUrl}/api/web_login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: tok }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
      piLocalToken = tok;
      piLocalUrl = activeBaseUrl;
      piLocalRole = String(data.role || "").trim().toLowerCase();
      piAuthed = true;
      localStorage.setItem("pi_ai_pi_local_token", tok);
      localStorage.setItem("pi_ai_pi_local_url", activeBaseUrl);
      localStorage.setItem("pi_ai_pi_local_role", piLocalRole);
      if (inp) inp.value = "";
      if (piLocalRole === "viewer") {
        setHint(
          "pi-auth-status",
          "Đang ở chế độ CHỈ XEM (viewer) — nhập WEB_OPERATOR_TOKEN hoặc WEB_ADMIN_TOKEN để bấm điều khiển!",
          "warn"
        );
        logLine("Đăng nhập Pi Cục Bộ ở chế độ CHỈ XEM (viewer).");
      } else {
        setHint("pi-auth-status", `Đã kết nối Pi Cục Bộ (vai trò: ${piLocalRole || "operator"}).`, "ok");
        logLine(`Đăng nhập Pi Cục Bộ thành công (${piLocalRole || "operator"}).`);
      }
      syncTopBadgesAndControls();
    } catch (err) {
      piAuthed = false;
      setHint("pi-auth-status", `Đăng nhập Pi thất bại: ${err.message}`, "err");
      logLine(`Đăng nhập Pi lỗi: ${err.message}`);
      syncTopBadgesAndControls();
    }
  };

  root.uiClearPiToken = function () {
    piLocalToken = "";
    piLocalUrl = "";
    piLocalRole = "";
    piAuthed = false;
    localStorage.removeItem("pi_ai_pi_local_token");
    localStorage.removeItem("pi_ai_pi_local_url");
    localStorage.removeItem("pi_ai_pi_local_role");
    setHint("pi-auth-status", "Đã xóa mã truy cập Pi.", "");
    logLine("Đã xóa mã truy cập Pi cục bộ.");
    syncTopBadgesAndControls();
  };

  root.uiClearServerToken = function () {
    phoneToken = "";
    serverPaired = false;
    localStorage.removeItem("pi_ai_driver_phone_token");
    setHint("pair-status", "Đã xóa token Server cũ — hãy nhập OTP 8 ký tự của Server mới.", "");
    logLine("Đã xóa token OTP Server cũ.");
    syncTopBadgesAndControls();
  };

  async function sendViaPiLocal(mappedCmd) {
    const headers = {
      "Content-Type": "application/json",
      "X-Auth-Token": piLocalToken,
    };
    const body = JSON.stringify({ cmd: mappedCmd });
    let r = await fetch(`${activeBaseUrl}/api/control`, { method: "POST", headers, body });
    if (r.status === 404) {
      r = await fetch(`${activeBaseUrl}/api/app_control`, { method: "POST", headers, body });
    }
    if (r.status === 401) {
      clearPiTokenOnAuthFail("Mã truy cập Pi không hợp lệ hoặc đã hết hạn (401). Vui lòng nhập lại.");
      throw new Error("HTTP 401");
    }
    if (r.status === 403) {
      const msg403 = "Thiếu quyền điều khiển (403) — cần WEB_OPERATOR_TOKEN hoặc WEB_ADMIN_TOKEN";
      setHint("pi-auth-status", msg403, "warn");
      showTopStatus(msg403, false);
      throw new Error(msg403);
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) {
      throw new Error(data.detail || data.error || `HTTP ${r.status}`);
    }
    return data;
  }

  async function sendViaServerHttp(mappedAction) {
    const r = await fetch(`${activeBaseUrl}/api/v1/phone/action`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${phoneToken}`,
      },
      body: JSON.stringify({
        vehicle_id: pairedVehicle,
        action: mappedAction,
        detail: { source: "PWA_HTTP" },
      }),
    });
    if (r.status === 401) {
      clearServerTokenOn401();
      throw new Error("HTTP 401 (Token Server hết hạn)");
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      throw new Error(data.detail || `HTTP ${r.status}`);
    }
    return data;
  }

  async function dispatchAction(cmd) {
    const route = resolveTransportForAction(cmd);
    if (!route) {
      throw new Error(`Chưa có kênh hỗ trợ lệnh ${cmd}`);
    }
    if (route.transport === "ble") {
      await client.sendCommand(route.mapped);
      logLine(`Đã gửi BLE -> ${route.mapped}`);
      return { transport: "ble", mapped: route.mapped };
    }
    if (route.transport === "pi_local") {
      const res = await sendViaPiLocal(route.mapped);
      logLine(`Đã gửi Pi Cục Bộ -> ${route.mapped} (${res.detail || "OK"})`);
      return { transport: "pi_local", mapped: route.mapped, res };
    }
    const res = await sendViaServerHttp(route.mapped);
    logLine(`Đã gửi Server HTTP -> ${route.mapped} (${pairedVehicle})`);
    return { transport: "server_http", mapped: route.mapped, res };
  }

  function onBleState(state, devName) {
    const badge = document.getElementById("ble-status-badge");
    const sub = document.getElementById("device-subtitle");
    if (state === "CONNECTED") {
      if (badge) { badge.textContent = "BLE OK"; badge.className = "badge ok"; }
      if (sub) sub.textContent = `Đang nối BLE: ${devName}`;
      if (calibTimer === null) {
        setCalibBanner("", "Sẵn sàng bật MediaPipe 10s trực tiếp qua Bluetooth với xe.");
      }
      syncWakeLock(true);
      logLine(`Kết nối BLE thành công tới ${devName}`);
    } else {
      if (badge) { badge.textContent = "BLE MẤT NỐI"; badge.className = "badge err"; }
      syncWakeLock(false);
      logLine("Mất kết nối BLE — sẽ tự thử nối lại theo chu kỳ tăng dần...");
    }
    syncTopBadgesAndControls();
  }

  function onBleMessage(msg) {
    if (msg.kind === "GPS") {
      lastBleGpsMs = Date.now();
      const spdEl = document.getElementById("gps-speed");
      const satEl = document.getElementById("gps-sats");
      const fixEl = document.getElementById("gps-fix");
      const coordsEl = document.getElementById("gps-coords");
      if (spdEl) spdEl.textContent = msg.spd.toFixed(1);
      if (satEl) satEl.textContent = msg.satsView != null ? `${msg.sats}/${msg.satsView}` : String(msg.sats);
      if (fixEl) {
        fixEl.textContent = msg.fix ? "LOCKED" : "NO FIX";
        fixEl.className = "val " + (msg.fix ? "ok" : "warn");
      }
      if (coordsEl) {
        coordsEl.textContent = msg.fix
          ? `ESP2 BLE GPS: ${msg.lat.toFixed(6)}, ${msg.lon.toFixed(6)}${msg.hdop != null ? ` (HDOP ${msg.hdop.toFixed(1)})` : ""}`
          : `ESP2 BLE GPS: chưa khóa vệ tinh${msg.satsView != null ? ` (thấy ${msg.satsView} vệ tinh — đưa ăng-ten ra ngoài trời)` : " (ra ngoài trời)"}`;
      }
      return;
    }
    const calNotice = classifyCalibrationNotice(msg);
    if (calNotice) {
      if (calNotice.countdownSec !== null) {
        if (calNotice.countdownSec > 0) {
          if (calibTimer === null) startCalibCountdown(calNotice.countdownSec, false);
        } else {
          stopCalibCountdown();
          calibPendingHttpResult = false;
        }
      }
      setCalibBanner(calNotice.tone, calNotice.statusText);
      if (calNotice.ttsText) speakCalmInfo(calNotice.ttsText);
      logLine(`CALIB/INFO -> ${msg.raw}`);
      return;
    }
    if (msg.kind === "ALERT") {
      const aType = String(msg.alertType || "").toUpperCase();
      if (aType === "MODE_AUTO") {
        showTopStatus(`🟢 ${msg.detail || "Đã tự động bật lại chế độ giám sát AUTO"}`, false);
        renderMonitoringModeBox({ mode: "AUTO" }, true);
        speakCalmInfo("Hệ thống đã tự động bật lại chế độ giám sát");
        logLine(`MODE_AUTO -> ${msg.raw}`);
        return;
      }
      if (aType === "DRIVER_ABSENT") {
        showTopStatus(`🟡 ${msg.detail || "Camera không thấy tài xế — tạm tắt vắng mặt 30 phút"}`, false);
        renderMonitoringModeBox({ mode: "TEMP_PAUSE_30M", presence: "NO_DRIVER", pause_remaining_s: 1800 }, false);
        logLine(`DRIVER_ABSENT -> ${msg.raw}`);
        return;
      }
      if (aType === "DROWSY_LEVEL3" || aType === "DROWSY_RELAPSE") {
        const isRelapse = aType === "DROWSY_RELAPSE";
        renderDrowsyEscalationBox({
          state: isRelapse ? "CONTINUOUS_RELAPSE" : "ALARM_LATCHED",
          count: isRelapse ? 4 : 3,
          limit: 3,
          alarm_active: true,
        });
      }
      const ab = document.getElementById("alert-banner");
      if (ab) {
        ab.style.display = "block";
        ab.textContent = `CẢNH BÁO: ${msg.alertType}${msg.detail ? " — " + msg.detail : ""}`;
      }
      speakAlert(`Cảnh báo lái xe ${msg.alertType}`);
      logLine(`ALERT -> ${msg.raw}`);
      return;
    }
    if (msg.kind === "ACK") {
      logLine(`ESP2 xác nhận: ${msg.action}`);
      if (msg.action === "MUTE_ALERT") {
        const ab = document.getElementById("alert-banner");
        if (ab) ab.style.display = "none";
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

  root.uiConnectBle = async function () {
    const badge = document.getElementById("ble-status-badge");
    try {
      showTopStatus("", false);
      if (badge) { badge.textContent = "ĐANG NỐI BLE..."; badge.className = "badge warn"; }
      if (!client) {
        client = new DriverBleClient({
          onStateChange: onBleState,
          onMessage: onBleMessage,
          onWarn: (w) => { logLine(w); showTopStatus(w, false); },
        });
      }
      await client.connect();
    } catch (err) {
      if (badge) { badge.textContent = "BLE LỖI"; badge.className = "badge err"; }
      const msg = `BLE: ${err && err.message ? err.message : err}`;
      logLine(msg);
      const note = document.getElementById("ble-compat-note");
      if (note) { note.style.display = "block"; note.textContent = msg; }
    }
  };

  root.uiDisconnectBle = function () {
    if (client) client.disconnect();
  };

  root.uiSendAction = async function (cmd) {
    try {
      const out = await dispatchAction(cmd);
      if (String(cmd).toUpperCase() === "MUTE") {
        const ab = document.getElementById("alert-banner");
        if (ab) ab.style.display = "none";
      }
      return out;
    } catch (err) {
      logLine(`Gửi thất bại (${cmd}): ${err.message}`);
      return null;
    }
  };
  // Backward-compatible alias
  root.uiSendBle = root.uiSendAction;

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
    try {
      await dispatchAction("SOS");
    } catch (err) {
      logLine(`Gửi SOS thất bại: ${err.message}`);
    }
  };

  root.uiStartCalibration = async function () {
    if (calibTimer !== null) return;
    calibBaselineProfileTs = lastKnownCalibProfileTs;
    calibSawRunning = false;
    try {
      const out = await dispatchAction("CALIBRATE");
      const httpOnly = out.transport !== "ble";
      calibPendingHttpResult = httpOnly;
      startCalibCountdown(10, httpOnly);
      speakCalmInfo("Bắt đầu nhận diện tư thế mười giây");
      if (httpOnly) {
        setTimeout(() => { pollLiveStatus(); }, 1200);
      }
    } catch (err) {
      setCalibBanner("warn", `Chưa gửi được lệnh cân chỉnh: ${err.message}`);
      logLine(`Gửi CALIBRATE thất bại: ${err.message}`);
    }
  };

  root.uiRedeemOtp = async function () {
    const vidEl = document.getElementById("pair-vehicle");
    const otpEl = document.getElementById("pair-otp");
    const vid = (vidEl ? vidEl.value : "XE-01").trim() || "XE-01";
    const otp = (otpEl ? otpEl.value : "").trim().toUpperCase();
    if (!activeBaseUrl) {
      setHint("pair-status", "Hãy kiểm tra địa chỉ Server phía trên trước khi ghép OTP.", "warn");
      return;
    }
    if (!otp) {
      setHint("pair-status", "Vui lòng nhập mã OTP 8 ký tự do quản trị viên cấp.", "warn");
      return;
    }
    try {
      const r = await fetch(`${activeBaseUrl}/api/v1/phone/pair`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vehicle_id: vid, pair_code: otp }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      phoneToken = data.driver_phone_token;
      pairedVehicle = vid;
      serverPaired = true;
      localStorage.setItem("pi_ai_driver_phone_token", phoneToken);
      localStorage.setItem("pi_ai_driver_vehicle_id", pairedVehicle);
      if (otpEl) otpEl.value = "";
      setHint("pair-status", `Đã ghép Server thành công cho xe ${vid} (${activeBaseUrl}).`, "ok");
      logLine(`Ghép OTP Server thành công cho xe ${vid}.`);
      if (calibTimer === null && !isBleReady()) {
        setCalibBanner("", `Sẵn sàng bật MediaPipe 10s qua Server HTTP cho xe ${vid}.`);
      }
      syncTopBadgesAndControls();
    } catch (err) {
      setHint("pair-status", `Ghép OTP thất bại: ${err.message}`, "err");
      logLine(`Ghép OTP lỗi: ${err.message}`);
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
        if (isServerReady() && now - lastPhoneGeoPostMs >= 3000) {
          lastPhoneGeoPostMs = now;
          try {
            const r = await fetch(`${activeBaseUrl}/api/v1/phone/telemetry`, {
              method: "POST",
              headers: { "Content-Type": "application/json", Authorization: `Bearer ${phoneToken}` },
              body: JSON.stringify(body),
            });
            if (r.status === 401) {
              clearServerTokenOn401();
            } else if (!r.ok && out) {
              out.textContent += ` [Server HTTP ${r.status}]`;
            }
          } catch (_) {}
        } else if (!isServerReady() && out) {
          out.textContent += " (Ghép OTP Server phía trên để gửi GPS điện thoại lên Dashboard)";
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

    // Gentle non-alarming BLE note when opened over plain HTTP
    const sup = DriverBleClient.checkBrowserSupport();
    const bleNote = document.getElementById("ble-compat-note");
    if (!sup.supported && bleNote) {
      bleNote.style.display = "block";
      bleNote.textContent = sup.reason;
    }

    // Smart base URL initialization: always prefer current page origin when opening from a new origin!
    const pageOrigin = getPageOrigin();
    const savedUrl = normalizeBaseUrl(localStorage.getItem("pi_ai_base_url") || "");
    const savedOrigin = localStorage.getItem("pi_ai_base_url_origin") || "";
    const initialUrl =
      pageOrigin && (!savedUrl || savedOrigin !== pageOrigin)
        ? pageOrigin
        : (savedUrl || pageOrigin);

    const urlInp = document.getElementById("conn-base-url");
    if (urlInp && initialUrl) urlInp.value = initialUrl;

    const inpVid = document.getElementById("pair-vehicle");
    if (inpVid) inpVid.value = pairedVehicle;
    const admVid = document.getElementById("admin-vehicle-id");
    if (admVid) admVid.value = pairedVehicle;
    if (serverAdminToken && (serverAdminRole === "admin" || serverAdminRole === "operator")) {
      serverAdminAuthed = true;
    }
    root.uiSwitchPortal(activePortal);

    syncTopBadgesAndControls();
    if (initialUrl) {
      root.uiProbeEndpoint(false);
    }
  });
})(typeof window !== "undefined" ? window : globalThis);
