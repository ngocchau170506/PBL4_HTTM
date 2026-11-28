/**
 * Automated Node.js unit & adversarial test suite for APP_IOT (3-Source Standalone Driver PWA).
 * Run: node APP_IOT/test_driver_ble.mjs
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
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
} = require("./driver_ble.js");

// 1. Verify exact Nordic UART UUIDs match firmware/esp32_ble_wifi_hub/esp32_ble_wifi_hub.ino
assert.equal(BLE_NUS.SERVICE_UUID, "6e400001-b5a3-f393-e0a9-e50e24dcca9e");
assert.equal(BLE_NUS.CHAR_RX_UUID, "6e400002-b5a3-f393-e0a9-e50e24dcca9e");
assert.equal(BLE_NUS.CHAR_TX_UUID, "6e400003-b5a3-f393-e0a9-e50e24dcca9e");

// 2. Adversarial BLE 20-byte MTU fragmentation & reassembly test
const framer = new BleLineFramingParser();
const fullGps = 'GPS:{"fix":true,"lat":16.061234,"lon":108.212345,"spd":42.5,"sats":9}\r\n';
let collected = [];
for (let i = 0; i < fullGps.length; i += 20) {
  collected.push(...framer.pushChunk(fullGps.slice(i, i + 20)));
}
assert.equal(collected.length, 1);
const gpsParsed = parseEsp2Notification(collected[0]);
assert.equal(gpsParsed.kind, "GPS");
assert.equal(gpsParsed.fix, true);
assert.equal(gpsParsed.sats, 9);
assert.equal(gpsParsed.spd, 42.5);

// 3. Parse ALERT, STATUS, OK, ERR and malformed JSON safely (never throws!)
const alertP = parseEsp2Notification("ALERT:DROWSY|Eye_Closed 2.1s");
assert.equal(alertP.kind, "ALERT");
assert.equal(alertP.alertType, "DROWSY");
assert.equal(alertP.detail, "Eye_Closed 2.1s");

const statusP = parseEsp2Notification('STATUS:{"role":"COMMS_BLE_GPS_HUB","pi_alive":true}');
assert.equal(statusP.kind, "STATUS");
assert.equal(statusP.data.pi_alive, true);

assert.equal(parseEsp2Notification("OK:MUTE_ALERT").kind, "ACK");
assert.equal(parseEsp2Notification("ERR:UNKNOWN_CMD").kind, "ERROR");
assert.equal(parseEsp2Notification("GPS:{broken_json").kind, "PARSE_ERROR");
assert.equal(parseEsp2Notification("STATUS:[1,2,3]").kind, "PARSE_ERROR");

// 4. Command whitelist encoding & injection rejection
assert.equal(encodeBleCommand("mute"), "MUTE\n");
assert.equal(encodeBleCommand("  SOS  "), "SOS\n");
assert.throws(() => encodeBleCommand("CFG_TOKEN:LEAK"), /Command not allowed/);
assert.throws(() => encodeBleCommand("MUTE\nSOS"), /Command not allowed/);

// 5. Buffer overflow guard on runaway stream without newline
const ovFramer = new BleLineFramingParser(256);
ovFramer.pushChunk("X".repeat(1000));
const recovered = ovFramer.pushChunk("\nOK:ACK_AWAKE\n");
assert.ok(recovered.some((l) => l === "OK:ACK_AWAKE"));

// 6. WARN: survival notification parsed cleanly
const warnP = parseEsp2Notification("WARN:Pi silent >=3.5s -> Wi-Fi Survival ON");
assert.equal(warnP.kind, "WARN");
assert.ok(warnP.message.includes("Wi-Fi Survival ON"));

// 7. DriverBleClient._writeChain recovers cleanly after a GATT write error (F-014)
let writeAttempts = 0;
const cli = new DriverBleClient();
cli.connected = true;
cli.rxChar = {
  async writeValueWithoutResponse() {
    writeAttempts += 1;
    if (writeAttempts === 1) throw new Error("GATT busy");
  },
  async writeValueWithResponse() {
    if (writeAttempts === 1) throw new Error("GATT busy fallback");
  },
};
await assert.rejects(() => cli.sendCommand("MUTE"), /GATT busy/);
await cli.sendCommand("ACK");
assert.equal(writeAttempts, 2);

// 8. Real phone GNSS telemetry builder & extended ESP2 GPS fields (rejects 0,0 and >500m coarse fix)
const extGps = parseEsp2Notification('GPS:{"fix":true,"lat":16.068500,"lon":108.221500,"spd":36.0,"hdg":142.5,"hdop":1.2,"sats":9,"sats_view":12}');
assert.equal(extGps.kind, "GPS");
assert.equal(extGps.hdg, 142.5);
assert.equal(extGps.hdop, 1.2);
assert.equal(extGps.satsView, 12);

assert.equal(buildPhoneGpsTelemetry("XE-01", { coords: { latitude: 0, longitude: 0, accuracy: 5 } }), null);
assert.equal(buildPhoneGpsTelemetry("XE-01", { coords: { latitude: 16.0685, longitude: 108.2215, accuracy: 900 } }), null);
const realPh = buildPhoneGpsTelemetry("XE-01", {
  coords: { latitude: 16.0685, longitude: 108.2215, accuracy: 6.4, altitude: 14.2, speed: 10.0, heading: 90.0 },
});
assert.equal(realPh.vehicle_id, "XE-01");
assert.equal(realPh.phone_gps.is_valid, true);
assert.equal(realPh.phone_gps.speed_kmh, 36.0);
assert.equal(realPh.phone_gps.accuracy_m, 6.4);

// 9. 10s MediaPipe Calibration BLE command & friendly non-siren CALIB_* / OK:CALIBRATE_LOCK handling
assert.equal(encodeBleCommand("calibrate"), "CALIBRATE\n");
assert.equal(encodeBleCommand("  CALIBRATE "), "CALIBRATE\n");

const ackCal = parseEsp2Notification("OK:CALIBRATE_LOCK");
assert.equal(ackCal.kind, "ACK");
assert.equal(ackCal.action, "CALIBRATE_LOCK");
const ackNotice = classifyCalibrationNotice(ackCal);
assert.equal(ackNotice.handled, true);
assert.equal(ackNotice.triggerDangerSiren, false);
assert.equal(ackNotice.countdownSec, 10);

const calStart = parseEsp2Notification("ALERT:CALIB_START|10s");
assert.equal(calStart.kind, "ALERT");
assert.equal(calStart.alertType, "CALIB_START");
assert.equal(calStart.detail, "10s");
assert.equal(calStart.isCalibration, true);
assert.equal(calStart.isDangerAlert, false);
const startNotice = classifyCalibrationNotice(calStart);
assert.equal(startNotice.triggerDangerSiren, false);
assert.equal(startNotice.tone, "running");
assert.equal(startNotice.countdownSec, 10);
assert.equal(startNotice.ttsText, "Bắt đầu nhận diện tư thế mười giây");

const calProg = parseEsp2Notification("ALERT:CALIB_PROGRESS|6s");
assert.equal(calProg.isCalibration, true);
assert.equal(calProg.isDangerAlert, false);
const progNotice = classifyCalibrationNotice(calProg);
assert.equal(progNotice.triggerDangerSiren, false);
assert.equal(progNotice.tone, "running");
assert.equal(progNotice.ttsText, "");

const calDone = parseEsp2Notification("ALERT:CALIB_DONE|locked");
assert.equal(calDone.isCalibration, true);
assert.equal(calDone.isDangerAlert, false);
const doneNotice = classifyCalibrationNotice(calDone);
assert.equal(doneNotice.triggerDangerSiren, false);
assert.equal(doneNotice.tone, "ok");
assert.equal(doneNotice.countdownSec, 0);
assert.equal(doneNotice.statusText, "Đã nhận diện & khóa cấu hình tài xế!");
assert.equal(doneNotice.ttsText, "Đã lưu cấu hình tài xế");

const calFail = parseEsp2Notification("ALERT:CALIB_FAIL|NO_FACE");
assert.equal(calFail.isCalibration, true);
assert.equal(calFail.isDangerAlert, false);
const failNotice = classifyCalibrationNotice(calFail);
assert.equal(failNotice.triggerDangerSiren, false);
assert.equal(failNotice.tone, "warn");
assert.equal(failNotice.countdownSec, 0);
assert.ok(failNotice.statusText.includes("Không thấy rõ khuôn mặt"));

// F-05: Local 10s countdown expiry MUST NEVER fabricate "ok" / saved TTS without real Pi confirmation
const httpTimeout = resolveCalibTimeoutNotice(true, "running");
assert.equal(httpTimeout.tone, "warn");
assert.equal(httpTimeout.statusText, "Đã gửi lệnh cân chỉnh 10s qua Server — vui lòng kiểm tra trạng thái trên xe/Dashboard");
assert.equal(httpTimeout.ttsText, "");

const bleUnconfirmedTimeout = resolveCalibTimeoutNotice(false, "running");
assert.equal(bleUnconfirmedTimeout.tone, "warn");
assert.equal(bleUnconfirmedTimeout.statusText, "Đang chờ Pi xác nhận kết quả cân chỉnh...");
assert.equal(bleUnconfirmedTimeout.ttsText, "");
assert.equal(resolveCalibTimeoutNotice(false, "ok"), null);

// Real danger alerts MUST still return null from classifyCalibrationNotice & keep isDangerAlert = true
assert.equal(alertP.isDangerAlert, true);
assert.equal(alertP.isCalibration, false);
assert.equal(classifyCalibrationNotice(alertP), null);

// 10. Contract JSON stays aligned with driver_ble.js ALLOWED_CMDS
const contractJson = require("./ble_protocol_contract.json");
const contractCmds = new Set(contractJson.commands_phone_to_esp2.map((c) => c.cmd));
for (const cmd of BLE_NUS.ALLOWED_CMDS) {
  assert.ok(contractCmds.has(cmd), `Missing ${cmd} in ble_protocol_contract.json`);
}

// 11. normalizeBaseUrl validates http/https only, trims whitespace and trailing slashes
assert.equal(normalizeBaseUrl("  http://192.168.1.7:8080/  "), "http://192.168.1.7:8080");
assert.equal(normalizeBaseUrl("https://demo.trycloudflare.com///"), "https://demo.trycloudflare.com");
assert.equal(normalizeBaseUrl("ftp://192.168.1.7:21"), "");
assert.equal(normalizeBaseUrl("192.168.1.7:8080"), "");
assert.equal(normalizeBaseUrl("javascript:alert(1)"), "");
assert.equal(normalizeBaseUrl(""), "");

// 12. detectTargetKind distinguishes pi_local, server_http, and unknown cleanly
assert.equal(detectTargetKind({ ok: true, target: "pi_local" }), "pi_local");
assert.equal(detectTargetKind({ ok: true, web_mode: "full_local" }), "pi_local");
assert.equal(detectTargetKind({ ok: true, service: "pi_ai_central_server" }), "server_http");
assert.equal(detectTargetKind({ ok: true }), "unknown");
assert.equal(detectTargetKind(null), "unknown");

// 13. formatAiEngineStatus covers MEDIAPIPE_10S, NO_CAMERA, and YOLO_ACTIVE (locked/unlocked)
const mpRunning = formatAiEngineStatus({ alert_type: "NORMAL" }, { state: "RUNNING", progress_s: 4.5, duration_s: 10.0 });
assert.equal(mpRunning.mode, "MEDIAPIPE_10S");
assert.equal(mpRunning.tone, "running");
assert.ok(mpRunning.title.includes("MediaPipe 478"));
assert.ok(mpRunning.detail.includes("4.5s") && mpRunning.detail.includes("10s"));

const noCam = formatAiEngineStatus({ alert_reason: "NO_CAMERA" }, { state: "IDLE" });
assert.equal(noCam.mode, "NO_CAMERA");
assert.equal(noCam.tone, "warn");
assert.ok(noCam.title.includes("Chưa có tín hiệu Camera"));

const yoloUnlocked = formatAiEngineStatus({ alert_type: "NORMAL" }, { state: "IDLE", locked: false });
assert.equal(yoloUnlocked.mode, "YOLO_ACTIVE");
assert.equal(yoloUnlocked.tone, "ok");
assert.equal(yoloUnlocked.title, "YOLO đang giám sát (MediaPipe đã tắt)");

const yoloLocked = formatAiEngineStatus({ alert_type: "NORMAL" }, { state: "DONE", locked: true, ear_closed_threshold: 0.19 });
assert.equal(yoloLocked.mode, "YOLO_ACTIVE");
assert.equal(yoloLocked.tone, "ok");
assert.ok(yoloLocked.title.includes("Đã khóa cấu hình"));

// 14. mapActionForTransport maps commands cleanly across ble, pi_local, and server_http
assert.equal(mapActionForTransport("MUTE", "ble"), "MUTE");
assert.equal(mapActionForTransport("STATUS", "ble"), "STATUS");
assert.equal(mapActionForTransport("INVALID", "ble"), null);

assert.equal(mapActionForTransport("MUTE", "pi_local"), "MUTE");
assert.equal(mapActionForTransport("CALIBRATE", "pi_local"), "CALIBRATE");
assert.equal(mapActionForTransport("REST_START", "pi_local"), "REST_BREAK_REQUEST");
assert.equal(mapActionForTransport("STATUS", "pi_local"), "PING");
assert.equal(mapActionForTransport("SOS", "pi_local"), null);
assert.equal(mapActionForTransport("ACK", "pi_local"), null);
assert.equal(mapActionForTransport("REST_END", "pi_local"), null);

assert.equal(mapActionForTransport("MUTE", "server_http"), "MUTE_ALERT");
assert.equal(mapActionForTransport("SOS", "server_http"), "SOS");
assert.equal(mapActionForTransport("ACK", "server_http"), "ACK_AWAKE");
assert.equal(mapActionForTransport("REST_START", "server_http"), "REST_BREAK_START");
assert.equal(mapActionForTransport("REST_END", "server_http"), "REST_BREAK_END");
assert.equal(mapActionForTransport("CALIBRATE", "server_http"), "CALIBRATE_LOCK");
assert.equal(mapActionForTransport("STATUS", "server_http"), null);

// 15. R3-01: shouldFinishHttpCalibration blocks stale DONE/FAILED until RUNNING observed or profile_ts changes
assert.equal(shouldFinishHttpCalibration({ state: "DONE", profile_ts: 1000.0, locked: true }, false, 1000.0), false);
assert.equal(shouldFinishHttpCalibration({ state: "DONE", profile_ts: null, locked: false }, false, null), false);
assert.equal(shouldFinishHttpCalibration({ state: "FAILED", profile_ts: 1000.0 }, false, 1000.0), false);
assert.equal(shouldFinishHttpCalibration({ state: "FAILED", profile_ts: null }, false, null), false);
assert.equal(shouldFinishHttpCalibration({ state: "RUNNING", profile_ts: 2000.0 }, true, 1000.0), false);
assert.equal(shouldFinishHttpCalibration({ state: "DONE", profile_ts: 1000.0, locked: true }, true, 1000.0), true);
assert.equal(shouldFinishHttpCalibration({ state: "FAILED", profile_ts: null }, true, null), true);
assert.equal(shouldFinishHttpCalibration({ state: "DONE", profile_ts: 1025.5, locked: true }, false, 1000.0), true);
assert.equal(shouldFinishHttpCalibration({ state: "DONE", profile_ts: 1025.5, locked: true }, false, null), true);

// 16. New Driver/Admin actions map cleanly across BLE, Pi Local, and Server HTTP
assert.equal(encodeBleCommand("DISMISS_ALARM"), "DISMISS_ALARM\n");
assert.equal(encodeBleCommand("MODE_AUTO"), "MODE_AUTO\n");
assert.equal(encodeBleCommand("MODE_PAUSE"), "MODE_PAUSE\n");
assert.equal(encodeBleCommand("MODE_SLEEP"), "MODE_SLEEP\n");
assert.equal(mapActionForTransport("DISMISS_ALARM", "pi_local"), "DISMISS_DROWSY_ALARM");
assert.equal(mapActionForTransport("DISMISS_ALARM", "server_http"), "DISMISS_DROWSY_ALARM");
assert.equal(mapActionForTransport("MODE_PAUSE", "pi_local"), "MODE_PAUSE_30M");
assert.equal(mapActionForTransport("MODE_SLEEP", "server_http"), "MODE_REST_OFF");

// 17. Drowsiness 3-strike meter, 3-mode presence formatter & Admin role guard
const drL3 = formatDrowsyEscalationStatus({ state: "ALARM_LATCHED", count: 3, limit: 3, window_s: 180, alarm_active: true });
assert.equal(drL3.alarmActive, true);
assert.ok(drL3.badgeText.includes("3/3"));

const drProb = formatDrowsyEscalationStatus({ state: "PROBATION", count: 3, limit: 3, probation_remaining_s: 125 });
assert.equal(drProb.alarmActive, false);
assert.ok(drProb.badgeText.includes("2:05"));

const drRel = formatDrowsyEscalationStatus({ state: "CONTINUOUS_RELAPSE", count: 4, limit: 3 });
assert.equal(drRel.alarmActive, true);
assert.ok(drRel.title.includes("LIÊN TỤC"));

const monPause = formatMonitoringModeStatus({ mode: "TEMP_PAUSE_30M", presence: "DRIVER_PRESENT", pause_remaining_s: 900 }, true);
assert.equal(monPause.mode, "TEMP_PAUSE_30M");
assert.ok(monPause.presenceText.includes("THẤY NGƯỜI"));

const monSleep = formatMonitoringModeStatus({ mode: "REST_SLEEP_OFF", presence: "DRIVER_PRESENT", auto_resume_speed_kmh: 15 }, true);
assert.equal(monSleep.mode, "REST_SLEEP_OFF");
assert.ok(monSleep.detail.includes("15 km/h"));

assert.equal(canAccessAdminActions("pi_local", true, "viewer", false, ""), false);
assert.equal(canAccessAdminActions("pi_local", true, "admin", false, ""), true);
assert.equal(canAccessAdminActions("server_http", false, "", true, "operator"), true);
assert.equal(canAccessAdminActions("server_http", false, "", true, "driver_phone"), false);

// 18. Unified PBL4_HTTM Safety Score tiers (>65 / <=65 / <=20) & 48h Dispute validation
assert.equal(classifySafetyScoreTier(92).tier, "GREEN_GOOD");
assert.equal(classifySafetyScoreTier(65).tier, "YELLOW_WARN");
assert.equal(classifySafetyScoreTier(20).tier, "RED_LOCKED");
assert.ok(DISPUTE_REASONS.FALSE_DETECTION && DISPUTE_REASONS.SENSOR_ERROR && DISPUTE_REASONS.NOT_ME && DISPUTE_REASONS.OTHER);
assert.equal(validateDisputeSubmission("", "FALSE_DETECTION", "Loi AI").ok, false);
assert.equal(validateDisputeSubmission("EVT_1", "BAD_CODE", "Giai trinh hop le").ok, false);
assert.equal(validateDisputeSubmission("EVT_1", "FALSE_DETECTION", "abc").ok, false);
const goodDsp = validateDisputeSubmission("EVT_1", "FALSE_DETECTION", "Chói nắng kính nên AI báo nhầm mắt nhắm");
assert.equal(goodDsp.ok, true);
assert.equal(goodDsp.reason_code, "FALSE_DETECTION");

console.log("APP_IOT_TESTS_PASS: 18/18 checks OK");
