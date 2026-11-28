/*
 * ============================================================================
 * PI_AI — ESP32 #2: COMMS_BLE_GPS_HUB (LCK-05 v3)
 * ============================================================================
 * Hardware : ESP32 DevKit / ESP32-WROOM-32
 * Wiring   :
 *   - GPS Module -> UART2: GPIO16 (RX2 <- GPS TX), GPIO17 (TX2 -> GPS RX) @ 9600 baud
 *   - USB UART0  -> Raspberry Pi 4 @ 115200 baud (matches ESP32 #1!)
 *   - Status LED -> GPIO2
 * Arduino IDE Settings (BLE + Wi-Fi + TLS binary is ~1.6 MB):
 *   - Board            : ESP32 Dev Module
 *   - Partition Scheme : Huge APP (3MB No OTA/1MB SPIFFS)
 *   - Upload Speed     : 921600 (or 460800)
 *   - Serial Monitor   : 115200 baud, Newline (\n)
 *   - Extra Libraries  : NONE (100% built-in Espressif ESP32 Core 2.x / 3.x)
 *
 * Roles: 
 *   1. Direct Hardware GPS on UART2 (GPIO16/17 @ 9600):
 *      - Pure C++ NMEA parser ($GPRMC/$GNRMC/$BDRMC, $GPGGA/$GNGGA/$BDGGA)
 *      - Streams live GPS + hub health to Pi every 1s via FDBLE:{...}
 *      - Keeps live GPS working even if Raspberry Pi 4 freezes/powers off!
 *   2. Open BLE Nordic UART Service (NUS) for Driver Mobile App (no PIN):
 *      - Phone -> Hub : MUTE | SOS | ACK | REST_START | REST_END | CALIBRATE | STATUS
 *      - Hub -> Phone : ALERT:... (instant) & GPS:{...} (every 2s while connected)
 *   3. On-Demand Wi-Fi Survival Bridge:
 *      - Wi-Fi stays OFF while Pi sends heartbeats (HB:{...})
 *      - If Pi stops responding >= 3.5s (or TEST_SURVIVAL), turns Wi-Fi STA ON
 *        and posts LIVE UART2 GPS + latest CO2 to POST /api/v1/esp/bridge_telemetry
 *      - Turns Wi-Fi OFF immediately once Pi heartbeats resume
 *   4. Dynamic NVS Config: CFG_SERVER, CFG_TOKEN, CFG_WIFI, CFG_VEHICLE (never echoes secrets)
 * ============================================================================
 */

#include <Arduino.h>
#include <HardwareSerial.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <math.h>
#include <esp_system.h>

#if defined(__has_include)
#if __has_include("wifi_config.h")
#include "wifi_config.h"
#define HAS_FALLBACK_WIFI_CFG 1
#else
#define HAS_FALLBACK_WIFI_CFG 0
#endif
#else
#define HAS_FALLBACK_WIFI_CFG 0
#endif

// ---------------- Hardware & Timing Constants ----------------
#define HUB_SERIAL_BAUD 115200
#define GPS_RX_PIN 16
#define GPS_TX_PIN 17
#define GPS_BAUD 9600
#define LED_PIN 2

#define PI_SILENCE_THRESHOLD_MS 3500UL
#define SURVIVAL_POST_PERIOD_MS 4000UL
#define WIFI_CONNECT_RETRY_MS 6000UL
#define TELEMETRY_PERIOD_MS 1000UL
#define BLE_GPS_PUSH_PERIOD_MS 2000UL
#define GPS_STALE_TIMEOUT_MS 5000UL

// Nordic UART Service (NUS) UUIDs — standard for Mobile Apps, Serial Bluetooth Terminal & nRF Connect
#define NUS_SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_CHAR_RX_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  // Phone writes here
#define NUS_CHAR_TX_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // Hub notifies phone

// ---------------- Direct Hardware GPS (UART2) ----------------
HardwareSerial gpsSerial(2);

struct GpsData {
  bool isValid = false;
  bool hasEverFixed = false;
  double latitude = 0.0;
  double longitude = 0.0;
  float speedKmh = 0.0f;
  float altitudeM = 0.0f;
  float headingDeg = 0.0f;
  float hdop = 99.9f;
  int fixQuality = 0;
  int satellites = 0;
  int satsInView = 0;
  unsigned long lastFixTime = 0;
  unsigned long rxBytes = 0;
  unsigned long nmeaOk = 0;
  unsigned long nmeaBadCs = 0;
};

static GpsData gpsInfo;
static String nmeaBuffer = "";
static unsigned long gpsRawDumpUntilMs = 0;

// ---------------- NVS Persistent Configuration ----------------
static Preferences prefs;
static String cfgVehicleId = "XE-01";
static String cfgServerUrl = "";
static String cfgAuthToken = "";
static String cfgWifiSsid = "";
static String cfgWifiPass = "";

// ---------------- Runtime State ----------------
static BLEServer *pBleServer = nullptr;
static BLECharacteristic *pTxChar = nullptr;
static volatile bool bleConnected = false;
static bool oldBleConnected = false;

static volatile bool bleActionPending = false;
static char bleActionBuf[96];
static portMUX_TYPE bleMux = portMUX_INITIALIZER_UNLOCKED;

static bool piEverSeen = false;
static unsigned long lastPiHbMs = 0;
static bool survivalForced = false;
static bool survivalWifiOn = false;
static bool survivalServerOk = false;
static unsigned long lastWifiAttemptMs = 0;
static unsigned long lastSurvivalPostMs = 0;
static unsigned long lastTelemetryMs = 0;
static unsigned long lastBleGpsPushMs = 0;
static unsigned long lastLedBlinkMs = 0;
static unsigned long survivalSeq = 0;

// Latest cabin CO2 mirrored from Pi heartbeats (used during Pi-offline survival uplink)
static float lastCo2Ppm = 400.0f;

static char rxLine[384];
static size_t rxLen = 0;
static bool rxOverflow = false;

// ---------------- Helpers ----------------
static const char *resetReasonName(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SW: return "SW";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    default: return "OTHER";
  }
}

static int hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
  if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
  return -1;
}

static bool nmeaChecksumValid(const String &s) {
  int star = s.indexOf('*');
  // Reject truncated sentences missing *HH checksum (F-033)
  if (star <= 0 || star + 2 >= (int)s.length()) return false;
  int hi = hexVal(s[star + 1]);
  int lo = hexVal(s[star + 2]);
  if (hi < 0 || lo < 0) return false;
  uint8_t expected = (uint8_t)((hi << 4) | lo);
  uint8_t calc = 0;
  int startIdx = (s[0] == '$') ? 1 : 0;
  for (int i = startIdx; i < star; i++) {
    calc ^= (uint8_t)s[i];
  }
  return calc == expected;
}

static double parseNmeaCoord(const String &val, const String &hemi) {
  if (val.length() < 4) return 0.0;
  int dotIdx = val.indexOf('.');
  if (dotIdx < 2) return 0.0;
  int degDigits = dotIdx - 2;
  double degrees = val.substring(0, degDigits).toDouble();
  double minutes = val.substring(degDigits).toDouble();
  double dec = degrees + (minutes / 60.0);
  if (hemi == "S" || hemi == "W") dec = -dec;
  return dec;
}

static double haversineMeters(double lat1, double lon1, double lat2, double lon2) {
  const double R = 6371008.8;
  const double deg2rad = 0.017453292519943295;
  double p1 = lat1 * deg2rad;
  double p2 = lat2 * deg2rad;
  double dp = (lat2 - lat1) * deg2rad;
  double dl = (lon2 - lon1) * deg2rad;
  double sd = sin(dp * 0.5);
  double sl = sin(dl * 0.5);
  double a = sd * sd + cos(p1) * cos(p2) * sl * sl;
  if (a > 1.0) a = 1.0;
  if (a < 0.0) a = 0.0;
  return 2.0 * R * asin(sqrt(a));
}

static void applyValidGpsCoord(double lat, double lon, float spdKmh) {
  if (!gpsInfo.hasEverFixed) {
    gpsInfo.latitude = lat;
    gpsInfo.longitude = lon;
    gpsInfo.hasEverFixed = true;
    return;
  }
  double distM = haversineMeters(gpsInfo.latitude, gpsInfo.longitude, lat, lon);
  // Real non-freezing filter: update immediately if moving >=1.5 km/h OR displaced >=4.0m (walking/slow drive);
  // when stationary (<4m jitter), lightly EMA-smooth so cold-start outliers converge onto true location!
  if (spdKmh >= 1.5f || distM >= 4.0) {
    gpsInfo.latitude = lat;
    gpsInfo.longitude = lon;
  } else {
    gpsInfo.latitude = 0.8 * gpsInfo.latitude + 0.2 * lat;
    gpsInfo.longitude = 0.8 * gpsInfo.longitude + 0.2 * lon;
  }
}

static void parseNmeaSentence(const String &sentence) {
  if (!nmeaChecksumValid(sentence)) {
    gpsInfo.nmeaBadCs++;
    return;
  }
  gpsInfo.nmeaOk++;
  if (gpsRawDumpUntilMs != 0 && ((int32_t)(millis() - gpsRawDumpUntilMs) < 0)) {
    Serial.print("GPSRAW:");
    Serial.println(sentence);
  }

  if (sentence.startsWith("$GPRMC") || sentence.startsWith("$GNRMC") || sentence.startsWith("$BDRMC")) {
    int commaIdx[14];
    int count = 0;
    for (int i = 0; i < (int)sentence.length() && count < 14; i++) {
      if (sentence[i] == ',') commaIdx[count++] = i;
    }
    if (count >= 8 && commaIdx[1] + 1 < (int)sentence.length()) {
      char status = sentence[commaIdx[1] + 1];
      if (status == 'A') {
        String latStr = sentence.substring(commaIdx[2] + 1, commaIdx[3]);
        String latHemi = sentence.substring(commaIdx[3] + 1, commaIdx[4]);
        String lonStr = sentence.substring(commaIdx[4] + 1, commaIdx[5]);
        String lonHemi = sentence.substring(commaIdx[5] + 1, commaIdx[6]);
        String spdStr = sentence.substring(commaIdx[6] + 1, commaIdx[7]);

        double lat = parseNmeaCoord(latStr, latHemi);
        double lon = parseNmeaCoord(lonStr, lonHemi);
        bool coordsOk = isfinite(lat) && isfinite(lon)
                        && fabs(lat) <= 90.0 && fabs(lon) <= 180.0
                        && (fabs(lat) > 1e-6 || fabs(lon) > 1e-6);
        if (!coordsOk) {
          if (millis() - gpsInfo.lastFixTime > GPS_STALE_TIMEOUT_MS) gpsInfo.isValid = false;
          gpsInfo.speedKmh = 0.0f;
          return;
        }

        float spd = spdStr.toFloat() * 1.852f;
        if (spd < 1.2f) spd = 0.0f;
        applyValidGpsCoord(lat, lon, spd);
        gpsInfo.speedKmh = spd;

        if (count >= 9) {
          String headStr = sentence.substring(commaIdx[7] + 1, commaIdx[8]);
          if (headStr.length() > 0) gpsInfo.headingDeg = headStr.toFloat();
        }
        gpsInfo.isValid = true;
        if (gpsInfo.fixQuality == 0) gpsInfo.fixQuality = 1;
        gpsInfo.lastFixTime = millis();
      } else {
        if (millis() - gpsInfo.lastFixTime > GPS_STALE_TIMEOUT_MS) gpsInfo.isValid = false;
        gpsInfo.speedKmh = 0.0f;
      }
    }
  } else if (sentence.startsWith("$GPGGA") || sentence.startsWith("$GNGGA") || sentence.startsWith("$BDGGA")) {
    int commaIdx[15];
    int count = 0;
    for (int i = 0; i < (int)sentence.length() && count < 15; i++) {
      if (sentence[i] == ',') commaIdx[count++] = i;
    }
    if (count >= 10 && commaIdx[5] + 1 < (int)sentence.length()) {
      int fq = sentence.substring(commaIdx[5] + 1, commaIdx[6]).toInt();
      gpsInfo.fixQuality = fq;
      String satsStr = sentence.substring(commaIdx[6] + 1, commaIdx[7]);
      String hdopStr = sentence.substring(commaIdx[7] + 1, commaIdx[8]);
      String altStr = sentence.substring(commaIdx[8] + 1, commaIdx[9]);
      if (satsStr.length() > 0) gpsInfo.satellites = satsStr.toInt();
      if (hdopStr.length() > 0) gpsInfo.hdop = hdopStr.toFloat();
      if (fq > 0) {
        if (altStr.length() > 0) gpsInfo.altitudeM = altStr.toFloat();
        // Also parse GGA lat/lon so position fixes cleanly even before RMC arrives
        String latStr = sentence.substring(commaIdx[1] + 1, commaIdx[2]);
        String latHemi = sentence.substring(commaIdx[2] + 1, commaIdx[3]);
        String lonStr = sentence.substring(commaIdx[3] + 1, commaIdx[4]);
        String lonHemi = sentence.substring(commaIdx[4] + 1, commaIdx[5]);
        double lat = parseNmeaCoord(latStr, latHemi);
        double lon = parseNmeaCoord(lonStr, lonHemi);
        if (isfinite(lat) && isfinite(lon) && fabs(lat) <= 90.0 && fabs(lon) <= 180.0
            && (fabs(lat) > 1e-6 || fabs(lon) > 1e-6)) {
          applyValidGpsCoord(lat, lon, gpsInfo.speedKmh);
          gpsInfo.isValid = true;
          gpsInfo.lastFixTime = millis();
        }
      }
    }
  } else if (sentence.startsWith("$GPGSV") || sentence.startsWith("$GNGSV") || sentence.startsWith("$BDGSV")) {
    int commaIdx[5];
    int count = 0;
    for (int i = 0; i < (int)sentence.length() && count < 5; i++) {
      if (sentence[i] == ',') commaIdx[count++] = i;
    }
    if (count >= 4) {
      String viewStr = sentence.substring(commaIdx[2] + 1, commaIdx[3]);
      if (viewStr.length() > 0) {
        int v = viewStr.toInt();
        if (v >= 0 && v <= 64) gpsInfo.satsInView = v;
      }
    }
  }
}

static void updateGps() {
  while (gpsSerial.available() > 0) {
    char c = (char)gpsSerial.read();
    gpsInfo.rxBytes++;
    if (c == '\n' || c == '\r') {
      if (nmeaBuffer.length() > 10) parseNmeaSentence(nmeaBuffer);
      nmeaBuffer = "";
    } else if (nmeaBuffer.length() < 120) {
      nmeaBuffer += c;
    }
  }
  if (gpsInfo.isValid && (millis() - gpsInfo.lastFixTime > GPS_STALE_TIMEOUT_MS)) {
    gpsInfo.isValid = false;
    gpsInfo.fixQuality = 0;
    gpsInfo.satellites = 0;
    gpsInfo.speedKmh = 0.0f;
  }
}

// ---------------- BLE Helper ----------------
static void bleNotifyText(const String &msg) {
  if (!bleConnected || pTxChar == nullptr || msg.length() == 0) return;
  String line = msg;
  if (!line.endsWith("\n")) line += "\n";
  // F-015: Chunk BLE notifications to 20 bytes so GPS: (~70B) & STATUS: (~400B) NEVER truncate at MTU 23!
  const uint8_t *ptr = (const uint8_t *)line.c_str();
  size_t total = line.length();
  const size_t chunkMax = 20;
  for (size_t offset = 0; offset < total; offset += chunkMax) {
    if (!bleConnected) break;
    size_t n = (total - offset > chunkMax) ? chunkMax : (total - offset);
    pTxChar->setValue((uint8_t *)(ptr + offset), n);
    pTxChar->notify();
    if (offset + n < total) {
      delay(6);
    }
  }
}

static void emitStatusJson(bool alsoBle) {
  unsigned long now = millis();
  unsigned long silentMs = piEverSeen ? (now - lastPiHbMs) : 0;
  bool piAlive = piEverSeen && (silentMs < PI_SILENCE_THRESHOLD_MS);
  char buf[620];
  snprintf(
    buf,
    sizeof(buf),
    "{\"role\":\"COMMS_BLE_GPS_HUB\",\"fw\":\"PI_AI_3.1\",\"vehicle_id\":\"%s\","
    "\"ble_connected\":%s,\"wifi_survival_on\":%s,\"wifi_up\":%s,\"server_ok\":%s,"
    "\"pi_alive\":%s,\"pi_silent_ms\":%lu,\"has_server\":%s,\"has_token\":%s,\"has_wifi\":%s,"
    "\"gps_fix\":%s,\"lat\":%.6f,\"lon\":%.6f,\"speed_kmh\":%.1f,\"alt_m\":%.1f,\"heading\":%.1f,"
    "\"hdop\":%.2f,\"fix_q\":%d,\"sats\":%d,\"sats_view\":%d,"
    "\"gps_rx_bytes\":%lu,\"nmea_ok\":%lu,\"nmea_bad\":%lu,\"free_heap\":%lu}",
    cfgVehicleId.c_str(),
    bleConnected ? "true" : "false",
    survivalWifiOn ? "true" : "false",
    (WiFi.status() == WL_CONNECTED) ? "true" : "false",
    survivalServerOk ? "true" : "false",
    piAlive ? "true" : "false",
    silentMs,
    (cfgServerUrl.length() > 0) ? "true" : "false",
    (cfgAuthToken.length() > 0) ? "true" : "false",
    (cfgWifiSsid.length() > 0) ? "true" : "false",
    gpsInfo.isValid ? "true" : "false",
    gpsInfo.isValid ? gpsInfo.latitude : 0.0,
    gpsInfo.isValid ? gpsInfo.longitude : 0.0,
    gpsInfo.isValid ? gpsInfo.speedKmh : 0.0f,
    gpsInfo.isValid ? gpsInfo.altitudeM : 0.0f,
    gpsInfo.isValid ? gpsInfo.headingDeg : 0.0f,
    (gpsInfo.hdop < 99.0f) ? gpsInfo.hdop : 99.9f,
    gpsInfo.fixQuality,
    gpsInfo.satellites,
    gpsInfo.satsInView,
    gpsInfo.rxBytes,
    gpsInfo.nmeaOk,
    gpsInfo.nmeaBadCs,
    (unsigned long)ESP.getFreeHeap());
  Serial.print("FDBLE:");
  Serial.println(buf);
  if (alsoBle) {
    bleNotifyText(String("STATUS:") + buf);
  }
}

// ---------------- BLE Callbacks ----------------
class HubServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *server) override {
    (void)server;
    bleConnected = true;
  }
  void onDisconnect(BLEServer *server) override {
    (void)server;
    bleConnected = false;
  }
};

class HubRxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *pChar) override {
    const uint8_t *raw = pChar->getData();
    size_t n = pChar->getLength();
    if (!raw || n == 0) return;
    if (n >= sizeof(bleActionBuf)) n = sizeof(bleActionBuf) - 1;
    portENTER_CRITICAL(&bleMux);
    memcpy(bleActionBuf, raw, n);
    bleActionBuf[n] = '\0';
    bleActionPending = true;
    portEXIT_CRITICAL(&bleMux);
  }
};

static void initBleGatt() {
  String devName = "PI_AI_HUB_" + cfgVehicleId;
  BLEDevice::init(devName.c_str());
  BLEDevice::setMTU(185);
  pBleServer = BLEDevice::createServer();
  pBleServer->setCallbacks(new HubServerCallbacks());

  BLEService *pService = pBleServer->createService(NUS_SERVICE_UUID);
  pTxChar = pService->createCharacteristic(
    NUS_CHAR_TX_UUID,
    BLECharacteristic::PROPERTY_NOTIFY);
  pTxChar->addDescriptor(new BLE2902());

  BLECharacteristic *pRxChar = pService->createCharacteristic(
    NUS_CHAR_RX_UUID,
    BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  pRxChar->setCallbacks(new HubRxCallbacks());

  pService->start();
  BLEAdvertising *pAdv = BLEDevice::getAdvertising();
  pAdv->addServiceUUID(NUS_SERVICE_UUID);
  pAdv->setScanResponse(true);
  pAdv->setMinPreferred(0x06);
  BLEDevice::startAdvertising();
}

static void processBleEvents(unsigned long now) {
  if (bleConnected && !oldBleConnected) {
    oldBleConnected = true;
    Serial.println("EVT,BLE_STATE,PHONE_CONNECTED");
    bleNotifyText("PI_AI_HUB CONNECTED (" + cfgVehicleId + ") - Cmds: MUTE, DISMISS_ALARM, MODE_AUTO, MODE_PAUSE, MODE_SLEEP, SOS, ACK, REST_START, REST_END, CALIBRATE, STATUS");
  } else if (!bleConnected && oldBleConnected) {
    oldBleConnected = false;
    Serial.println("EVT,BLE_STATE,PHONE_DISCONNECTED");
    delay(100);
    BLEDevice::startAdvertising();
  }

  // Push lightweight live GPS JSON to Driver Mobile App every 2s while BLE connected
  if (bleConnected && (now - lastBleGpsPushMs >= BLE_GPS_PUSH_PERIOD_MS)) {
    lastBleGpsPushMs = now;
    char gbuf[190];
    snprintf(
      gbuf,
      sizeof(gbuf),
      "GPS:{\"fix\":%s,\"lat\":%.6f,\"lon\":%.6f,\"spd\":%.1f,\"hdg\":%.1f,\"hdop\":%.1f,\"sats\":%d,\"sats_view\":%d}",
      gpsInfo.isValid ? "true" : "false",
      gpsInfo.isValid ? gpsInfo.latitude : 0.0,
      gpsInfo.isValid ? gpsInfo.longitude : 0.0,
      gpsInfo.isValid ? gpsInfo.speedKmh : 0.0f,
      gpsInfo.isValid ? gpsInfo.headingDeg : 0.0f,
      (gpsInfo.hdop < 99.0f) ? gpsInfo.hdop : 99.9f,
      gpsInfo.satellites,
      gpsInfo.satsInView);
    bleNotifyText(String(gbuf));
  }

  char localCmd[96];
  bool hasAction = false;
  portENTER_CRITICAL(&bleMux);
  if (bleActionPending) {
    strncpy(localCmd, bleActionBuf, sizeof(localCmd) - 1);
    localCmd[sizeof(localCmd) - 1] = '\0';
    bleActionPending = false;
    hasAction = true;
  }
  portEXIT_CRITICAL(&bleMux);

  if (!hasAction) return;
  String s = String(localCmd);
  s.trim();
  String up = s;
  up.toUpperCase();
  if (up.length() == 0) return;

  if (up == "MUTE" || up == "MUTE_ALERT" || up == "M") {
    Serial.println("EVT,BLE_ACTION,MUTE_ALERT");
    bleNotifyText("OK:MUTE_ALERT");
  } else if (up == "DISMISS_ALARM" || up == "ALARM_OFF" || up == "STOP_ALARM" || up == "DISMISS_DROWSY_ALARM") {
    Serial.println("EVT,BLE_ACTION,DISMISS_DROWSY_ALARM");
    bleNotifyText("OK:DISMISS_DROWSY_ALARM");
  } else if (up == "MODE_AUTO" || up == "AUTO") {
    Serial.println("EVT,BLE_ACTION,MODE_AUTO");
    bleNotifyText("OK:MODE_AUTO");
  } else if (up == "MODE_PAUSE" || up == "MODE_PAUSE_30M") {
    Serial.println("EVT,BLE_ACTION,MODE_PAUSE_30M");
    bleNotifyText("OK:MODE_PAUSE_30M");
  } else if (up == "MODE_SLEEP" || up == "MODE_REST_OFF" || up == "REST_OFF") {
    Serial.println("EVT,BLE_ACTION,MODE_REST_OFF");
    bleNotifyText("OK:MODE_REST_OFF");
  } else if (up == "SOS" || up == "HELP") {
    Serial.println("EVT,BLE_ACTION,SOS");
    bleNotifyText("OK:SOS");
  } else if (up == "ACK" || up == "ACK_AWAKE" || up == "OK") {
    Serial.println("EVT,BLE_ACTION,ACK_AWAKE");
    bleNotifyText("OK:ACK_AWAKE");
  } else if (up == "REST_START" || up == "REST_BREAK_START") {
    Serial.println("EVT,BLE_ACTION,REST_BREAK_START");
    bleNotifyText("OK:REST_BREAK_START");
  } else if (up == "REST_END" || up == "REST_BREAK_END") {
    Serial.println("EVT,BLE_ACTION,REST_BREAK_END");
    bleNotifyText("OK:REST_BREAK_END");
  } else if (up == "CALIBRATE" || up == "CALIBRATE_LOCK" || up == "CAL") {
    Serial.println("EVT,BLE_ACTION,CALIBRATE_LOCK");
    bleNotifyText("OK:CALIBRATE_LOCK");
  } else if (up == "STATUS" || up == "?") {
    emitStatusJson(true);
  } else {
    bleNotifyText("ERR:UNKNOWN_CMD (" + s + ") Use: MUTE|DISMISS_ALARM|MODE_AUTO|MODE_PAUSE|MODE_SLEEP|SOS|ACK|REST_START|REST_END|CALIBRATE|STATUS");
  }
}

// ---------------- Lightweight JSON float extractor for Pi HB ----------------
static bool extractJsonFloat(const String &json, const char *key, double &outVal) {
  String pat = String("\"") + key + "\":";
  int idx = json.indexOf(pat);
  if (idx < 0) return false;
  idx += pat.length();
  while (idx < (int)json.length() && (json[idx] == ' ' || json[idx] == '\t')) idx++;
  if (idx >= (int)json.length() || json[idx] == 'n') return false;
  int end = idx;
  while (end < (int)json.length() && (isDigit(json[end]) || json[end] == '-' || json[end] == '+' || json[end] == '.')) {
    end++;
  }
  if (end <= idx) return false;
  outVal = json.substring(idx, end).toDouble();
  return true;
}

// ---------------- NVS Config Persistence ----------------
static void loadConfigFromNvs() {
  prefs.begin("pi_ai_hub", true);
  cfgVehicleId = prefs.getString("vehicle_id", "");
  cfgServerUrl = prefs.getString("server_url", "");
  cfgAuthToken = prefs.getString("auth_token", "");
  cfgWifiSsid = prefs.getString("wifi_ssid", "");
  cfgWifiPass = prefs.getString("wifi_pass", "");
  prefs.end();

#if HAS_FALLBACK_WIFI_CFG
  if (cfgVehicleId.length() == 0) {
#ifdef UPLINK_VEHICLE_ID
    cfgVehicleId = String(UPLINK_VEHICLE_ID);
#endif
  }
  if (cfgServerUrl.length() == 0) {
#ifdef UPLINK_SERVER_URL
    cfgServerUrl = String(UPLINK_SERVER_URL);
#endif
  }
  if (cfgAuthToken.length() == 0) {
#ifdef UPLINK_AUTH_TOKEN
    cfgAuthToken = String(UPLINK_AUTH_TOKEN);
#endif
  }
  if (cfgWifiSsid.length() == 0) {
#ifdef UPLINK_WIFI_SSID
    cfgWifiSsid = String(UPLINK_WIFI_SSID);
    cfgWifiPass = String(UPLINK_WIFI_PASS);
#endif
  }
#endif
  if (cfgVehicleId.length() == 0) cfgVehicleId = "XE-01";
}

static void saveNvsKey(const char *k, const String &v) {
  prefs.begin("pi_ai_hub", false);
  prefs.putString(k, v);
  prefs.end();
}

// ---------------- On-Demand Wi-Fi Survival Bridge (with LIVE UART2 GPS!) ----------------
static void turnSurvivalWifiOff() {
  if (!survivalWifiOn) return;
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  survivalWifiOn = false;
  survivalServerOk = false;
  Serial.println("EVT,SURVIVAL_WIFI,OFF");
}

static void tickSurvivalBridge(unsigned long now) {
  bool piTimedOut = piEverSeen && ((now - lastPiHbMs) >= PI_SILENCE_THRESHOLD_MS);
  bool needSurvival = piTimedOut || survivalForced;

  if (!needSurvival) {
    turnSurvivalWifiOff();
    return;
  }

  if (cfgWifiSsid.length() == 0 || cfgServerUrl.length() == 0) {
    return;
  }

  if (!survivalWifiOn) {
    survivalWifiOn = true;
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(true);
    WiFi.setTxPower(WIFI_POWER_11dBm);
    WiFi.begin(cfgWifiSsid.c_str(), cfgWifiPass.c_str());
    lastWifiAttemptMs = now;
    Serial.println("EVT,SURVIVAL_WIFI,ON");
    bleNotifyText("WARN:Pi silent >=3.5s -> Wi-Fi Survival ON (Live GPS)");
    return;
  }

  if (WiFi.status() != WL_CONNECTED) {
    survivalServerOk = false;
    if (now - lastWifiAttemptMs >= WIFI_CONNECT_RETRY_MS) {
      lastWifiAttemptMs = now;
      WiFi.disconnect(false);
      WiFi.begin(cfgWifiSsid.c_str(), cfgWifiPass.c_str());
    }
    return;
  }

  if (now - lastSurvivalPostMs < SURVIVAL_POST_PERIOD_MS) return;
  lastSurvivalPostMs = now;
  survivalSeq++;

  char body[760];
  snprintf(
    body,
    sizeof(body),
    "{\"schema_version\":\"1.0.0\",\"vehicle_id\":\"%s\",\"timestamp_ms\":%lu,"
    "\"camera\":{\"status\":\"OFFLINE\",\"fps\":0.0,\"face_detected\":false,\"is_stale\":true},"
    "\"pi\":{\"thermal_level\":0,\"throttled\":\"0x0\"},"
    "\"driver_state\":{\"alert_state\":\"NORMAL\",\"alert_type\":\"NORMAL\","
    "\"verification_mode\":\"ESP32_SURVIVAL\",\"detected_classes\":[]},"
    "\"esp\":{\"connected\":true,\"direct_wifi\":true,\"hub_role\":\"COMMS_BLE_GPS_HUB\",\"seq\":%lu,\"ble_connected\":%s},"
    "\"gps\":{\"lat\":%.6f,\"lon\":%.6f,\"speed_kmh\":%.1f,\"altitude_m\":%.1f,\"heading_deg\":%.1f,\"hdop\":%.2f,\"fix_quality\":%d,\"satellites\":%d,\"sats_in_view\":%d,\"source\":\"esp32_hub_gps_survival\",\"is_valid\":%s},"
    "\"mq135\":{\"co2_ppm\":%.1f,\"raw_adc\":0}}",
    cfgVehicleId.c_str(),
    now,
    survivalSeq,
    bleConnected ? "true" : "false",
    gpsInfo.isValid ? gpsInfo.latitude : 0.0,
    gpsInfo.isValid ? gpsInfo.longitude : 0.0,
    gpsInfo.isValid ? gpsInfo.speedKmh : 0.0f,
    gpsInfo.isValid ? gpsInfo.altitudeM : 0.0f,
    gpsInfo.isValid ? gpsInfo.headingDeg : 0.0f,
    (gpsInfo.hdop < 99.0f) ? gpsInfo.hdop : 99.9f,
    gpsInfo.fixQuality,
    gpsInfo.satellites,
    gpsInfo.satsInView,
    gpsInfo.isValid ? "true" : "false",
    lastCo2Ppm);

  String url = cfgServerUrl;
  while (url.endsWith("/")) url.remove(url.length() - 1);
  url += "/api/v1/esp/bridge_telemetry";

  HTTPClient http;
  WiFiClientSecure tlsClient;
  http.setConnectTimeout(1500);
  http.setTimeout(2500);
  bool began = false;
  if (url.startsWith("https://")) {
    tlsClient.setInsecure();
    began = http.begin(tlsClient, url);
  } else {
    began = http.begin(url);
  }

  if (began) {
    http.addHeader("Content-Type", "application/json; charset=utf-8");
    if (cfgAuthToken.length() > 0) {
      http.addHeader("X-Auth-Token", cfgAuthToken);
      http.addHeader("Authorization", "Bearer " + cfgAuthToken);
    }
    int code = http.POST((uint8_t *)body, strlen(body));
    survivalServerOk = (code >= 200 && code < 300);
    Serial.printf("EVT,SURVIVAL_POST,http=%d,seq=%lu\n", code, survivalSeq);
    http.end();
  } else {
    survivalServerOk = false;
    Serial.println("EVT,SURVIVAL_POST,BEGIN_FAILED");
  }
}

// ---------------- Serial Command Parser (Preserves payload case!) ----------------
static bool readSerialLine(String &out) {
  while (Serial.available() > 0) {
    char ch = (char)Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      if (rxOverflow) {
        rxOverflow = false;
        rxLen = 0;
        Serial.println("EVT,NACK,RX_OVERFLOW");
        continue;
      }
      rxLine[rxLen] = '\0';
      out = String(rxLine);
      rxLen = 0;
      out.trim();
      if (out.length() == 0) continue;
      return true;
    }
    if (rxOverflow) continue;
    if (rxLen < sizeof(rxLine) - 1) {
      rxLine[rxLen++] = ch;
    } else {
      rxOverflow = true;
      rxLen = 0;
    }
  }
  return false;
}

static void handleSerialCommand(const String &raw) {
  String up = raw;
  up.toUpperCase();

  if (up == "PING") {
    Serial.println("PONG");
    return;
  }
  if (up == "WHOAMI") {
    Serial.println("EVT,WHOAMI,ROLE=COMMS_BLE_GPS_HUB,FW=PI_AI_3.1");
    return;
  }
  if (up == "STATUS") {
    emitStatusJson(false);
    return;
  }
  if (up == "GPS_RAW") {
    gpsRawDumpUntilMs = millis() + 3000UL;
    Serial.printf("EVT,ACK,GPS_RAW,rx=%lu,ok=%lu,bad=%lu\n", gpsInfo.rxBytes, gpsInfo.nmeaOk, gpsInfo.nmeaBadCs);
    return;
  }
  if (up == "TEST_SURVIVAL") {
    survivalForced = true;
    lastSurvivalPostMs = 0;
    Serial.println("EVT,ACK,TEST_SURVIVAL");
    return;
  }
  if (up == "STOP_SURVIVAL") {
    survivalForced = false;
    turnSurvivalWifiOff();
    Serial.println("EVT,ACK,STOP_SURVIVAL");
    return;
  }
  if (up.startsWith("HB:")) {
    piEverSeen = true;
    lastPiHbMs = millis();
    survivalForced = false;
    String body = raw.substring(3);
    double v = 0.0;
    if (extractJsonFloat(body, "co2_ppm", v)) lastCo2Ppm = (float)v;
    Serial.println("EVT,ACK,HB");
    return;
  }
  if (up.startsWith("NOTIFY:")) {
    String msg = raw.substring(7);
    msg.trim();
    bleNotifyText("ALERT:" + msg);
    Serial.println("EVT,ACK,NOTIFY");
    return;
  }
  if (up.startsWith("CFG_SERVER:")) {
    String url = raw.substring(11);
    url.trim();
    if (url.startsWith("http://") || url.startsWith("https://")) {
      cfgServerUrl = url;
      saveNvsKey("server_url", cfgServerUrl);
      Serial.println("EVT,ACK,CFG_SERVER");
    } else {
      Serial.println("EVT,NACK,CFG_SERVER,INVALID_URL");
    }
    return;
  }
  if (up.startsWith("CFG_TOKEN:")) {
    String tok = raw.substring(10);
    tok.trim();
    if (tok.length() >= 8) {
      cfgAuthToken = tok;
      saveNvsKey("auth_token", cfgAuthToken);
      // NEVER echo plaintext token back to Serial!
      Serial.println("EVT,ACK,CFG_TOKEN");
    } else {
      Serial.println("EVT,NACK,CFG_TOKEN,TOO_SHORT");
    }
    return;
  }
  if (up.startsWith("CFG_WIFI:")) {
    String rest = raw.substring(9);
    int sep = rest.indexOf('|');
    if (sep > 0) {
      cfgWifiSsid = rest.substring(0, sep);
      cfgWifiPass = rest.substring(sep + 1);
      cfgWifiSsid.trim();
      saveNvsKey("wifi_ssid", cfgWifiSsid);
      saveNvsKey("wifi_pass", cfgWifiPass);
      // NEVER echo Wi-Fi password to Serial!
      Serial.println("EVT,ACK,CFG_WIFI");
    } else {
      Serial.println("EVT,NACK,CFG_WIFI,EXPECTED_SSID_PIPE_PASS");
    }
    return;
  }
  if (up.startsWith("CFG_VEHICLE:")) {
    String vid = raw.substring(12);
    vid.trim();
    if (vid.length() > 0 && vid.length() <= 32) {
      cfgVehicleId = vid;
      saveNvsKey("vehicle_id", cfgVehicleId);
      Serial.println("EVT,ACK,CFG_VEHICLE");
    } else {
      Serial.println("EVT,NACK,CFG_VEHICLE,INVALID");
    }
    return;
  }

  Serial.printf("EVT,NACK,UNKNOWN,%s\n", up.substring(0, 32).c_str());
}

// ---------------- Arduino Lifecycle ----------------
void setup() {
  Serial.setRxBufferSize(2048);
  Serial.begin(HUB_SERIAL_BAUD);
  Serial.setTimeout(15);

  gpsSerial.setRxBufferSize(1024);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // Wi-Fi stays OFF at boot (On-Demand Survival only — saves power & keeps BLE clean!)
  WiFi.mode(WIFI_OFF);

  loadConfigFromNvs();
  initBleGatt();

  digitalWrite(LED_PIN, HIGH);
  delay(150);
  digitalWrite(LED_PIN, LOW);

  Serial.println("EVT,BOOT_BLE_GPS_HUB_READY,115200");
  Serial.println("EVT,WHOAMI,ROLE=COMMS_BLE_GPS_HUB,FW=PI_AI_3.1");
  esp_reset_reason_t rr = esp_reset_reason();
  Serial.printf("EVT,RESET_REASON,%s\n", resetReasonName(rr));
  if (rr == ESP_RST_BROWNOUT) {
    Serial.println("EVT,WARN,BROWNOUT_HUB");
  }
}

void loop() {
  updateGps();

  String line;
  while (readSerialLine(line)) {
    handleSerialCommand(line);
  }

  unsigned long now = millis();
  processBleEvents(now);
  tickSurvivalBridge(now);

  // Stream live GPS + hub health to Pi every 1s
  if (now - lastTelemetryMs >= TELEMETRY_PERIOD_MS) {
    lastTelemetryMs = now;
    emitStatusJson(false);
  }

  // LED indicator: solid ON when phone BLE connected, fast blink in survival Wi-Fi, slow blink idle
  if (bleConnected) {
    digitalWrite(LED_PIN, HIGH);
  } else {
    unsigned long period = survivalWifiOn ? 200UL : 1000UL;
    if (now - lastLedBlinkMs >= period) {
      lastLedBlinkMs = now;
      digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    }
  }

  delay(5);
}
