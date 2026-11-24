/**
 * Automated Node.js unit & adversarial test suite for APP_IOT (Option C1 Driver BLE PWA).
 * Run: node APP_IOT/test_driver_ble.mjs
 */
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  BLE_NUS,
  BleLineFramingParser,
  parseEsp2Notification,
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

console.log("APP_IOT_TESTS_PASS: 8/8 checks OK");
