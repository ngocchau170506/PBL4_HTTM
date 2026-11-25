/*
 * ============================================================================
 * PI_AI — ESP32 #1: AUDIO_MQ135_NODE (LCK-05 v3 — Pure Real-Time Cabin Safety)
 * ============================================================================
 * Hardware : ESP32 DevKit / ESP32-WROOM-32
 * Roles    :
 *   1. Real-Time I2S Speaker MAX98357A (<5ms voice reflex, u-law 16kHz)
 *      - BCLK = GPIO26 | LRC = GPIO25 | DIN = GPIO22
 *      - Software volume control VOL:<10..100> persisted in NVS (default 65%)
 *      - Software MUTE (30s) via UART from Pi / Server Dashboard / Phone BLE
 *        (physical MUTE button removed in v3)
 *   2. Cabin Air Quality Sensor MQ-135 (GPIO34 via 10k/20k + 100nF divider)
 *      - 25s heater warm-up guard (no false CO2 alerts during cold boot)
 *      - EMA filter + standalone offline CO2 voice warning (VOICE:4 / VOICE:5)
 *   3. ZERO Radio & ZERO GPS:
 *      - Wi-Fi & Bluetooth physically disabled -> minimal power, 0 brownout
 *      - GPS moved to ESP32 #2 (COMMS_BLE_GPS_HUB)
 *   4. Unified UART: 115200 baud (matches ESP32 #2 for clean wiring & debug)
 * ============================================================================
 */

#include <Arduino.h>
#include <Preferences.h>
#include <math.h>
#include <esp_system.h>
#include "voice_clips.h"

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  #include "driver/i2s_std.h"
#else
  #include "driver/i2s.h"
  #define I2S_NUM I2S_NUM_0
#endif

// ---------------- Hardware Pins & Constants ----------------
#define SERIAL_BAUD        115200
#define I2S_BCLK_PIN       26
#define I2S_LRC_PIN        25
#define I2S_DIN_PIN        22
#define I2S_SAMPLE_RATE    16000
#define I2S_DMA_BUF_COUNT  8
#define I2S_DMA_BUF_LEN    256

#define MQ_PIN             34
#define LED_PIN            2

#define DEFAULT_VOL_PCT    65     // USB-safe default; raise to 85-95 via VOL:90 on dedicated 5V/3A Buck
#define VOICE_GAIN_PCT     100
#define CO2_WARN_PPM       1000.0f
#define CO2_CRIT_PPM       1500.0f
#define CO2_ALERT_COOLDOWN 15000UL
#define MQ_WARMUP_MS       25000UL
#define MUTE_DURATION_MS   30000UL

// ---------------- Runtime State ----------------
Preferences prefs;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
static i2s_chan_handle_t i2sTxChan = NULL;
#endif
static bool          isI2sReady      = false;
static int           audioVolumePct  = DEFAULT_VOL_PCT;
static bool          isPlayingVoice  = false;
static bool          stopRequested   = false;
static int           pendingVoice    = 0;

static float         ppmFiltered     = 400.0f;
static int           adcRaw          = 0;
static int           co2Level        = 0;
static const float   EMA_ALPHA       = 0.35f;

static unsigned long bootMs          = 0;
static unsigned long lastSampleMs    = 0;
static unsigned long lastTelemetryMs = 0;
static unsigned long lastCo2AlertMs  = 0;
static unsigned long muteUntilMs     = 0;
static unsigned long cmdUntilMs      = 0;
static unsigned long seqCounter      = 0;

static char          rxLine[256];
static size_t        rxLen           = 0;
static bool          rxOverflow      = false;

// ---------------- Helpers ----------------
static inline bool isBeforeDeadline(unsigned long now, unsigned long deadline) {
  return deadline != 0 && ((int32_t)(now - deadline) < 0);
}

static inline int16_t ulawDecode(uint8_t u_val) {
  u_val = ~u_val;
  int16_t t = ((u_val & 0x0F) << 3) + 0x84;
  t <<= ((u_val & 0x70) >> 4);
  return (u_val & 0x80) ? (0x84 - t) : (t - 0x84);
}

static inline int16_t applyVolume(int32_t s) {
  s = (s * audioVolumePct * VOICE_GAIN_PCT) / 10000L;
  if (s > 32767) s = 32767;
  if (s < -32767) s = -32767;
  return (int16_t)s;
}

static const char *resetReasonName(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:  return "POWERON";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SW:       return "SW";
    case ESP_RST_PANIC:    return "PANIC";
    case ESP_RST_INT_WDT:  return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT:      return "WDT";
    default:               return "OTHER";
  }
}

static bool serialReadLine(String &out) {
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
      out.toUpperCase();
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

// ---------------- I2S Audio Engine (ESP32 Core 2.x & 3.x) ----------------
static void initI2S() {
#if ESP_ARDUINO_VERSION_MAJOR < 3
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = I2S_SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = I2S_DMA_BUF_COUNT,
    .dma_buf_len = I2S_DMA_BUF_LEN,
    .use_apll = false,
    .tx_desc_auto_clear = true
  };
  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_BCLK_PIN,
    .ws_io_num = I2S_LRC_PIN,
    .data_out_num = I2S_DIN_PIN,
    .data_in_num = I2S_PIN_NO_CHANGE
  };
  if (i2s_driver_install(I2S_NUM, &i2s_config, 0, NULL) == ESP_OK) {
    i2s_set_pin(I2S_NUM, &pin_config);
    i2s_zero_dma_buffer(I2S_NUM);
    isI2sReady = true;
  }
#else
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num  = I2S_DMA_BUF_COUNT;
  chan_cfg.dma_frame_num = I2S_DMA_BUF_LEN;
  if (i2s_new_channel(&chan_cfg, &i2sTxChan, NULL) != ESP_OK) return;

  i2s_std_config_t std_cfg = {};
  std_cfg.clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(I2S_SAMPLE_RATE);
  std_cfg.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);
  std_cfg.gpio_cfg.mclk = I2S_GPIO_UNUSED;
  std_cfg.gpio_cfg.bclk = (gpio_num_t)I2S_BCLK_PIN;
  std_cfg.gpio_cfg.ws   = (gpio_num_t)I2S_LRC_PIN;
  std_cfg.gpio_cfg.dout = (gpio_num_t)I2S_DIN_PIN;
  std_cfg.gpio_cfg.din  = I2S_GPIO_UNUSED;

  if (i2s_channel_init_std_mode(i2sTxChan, &std_cfg) == ESP_OK &&
      i2s_channel_enable(i2sTxChan) == ESP_OK) {
    isI2sReady = true;
  }
#endif
}

static inline void i2sWriteBlock(const int16_t *buf, size_t bytes, uint32_t timeoutMs) {
  size_t bytesWritten = 0;
#if ESP_ARDUINO_VERSION_MAJOR < 3
  i2s_write(I2S_NUM, buf, bytes, &bytesWritten, pdMS_TO_TICKS(timeoutMs));
#else
  i2s_channel_write(i2sTxChan, buf, bytes, &bytesWritten, timeoutMs);
#endif
}

static void publishTelemetryIfDue(unsigned long now);
static bool pollDuringAudio(int currentClip);

static void i2sFlushSilence() {
  int16_t zero[128];
  memset(zero, 0, sizeof(zero));
  for (int i = 0; i < (I2S_DMA_BUF_COUNT * I2S_DMA_BUF_LEN * 2) / 128 + 1; i++) {
    i2sWriteBlock(zero, sizeof(zero), 20);
    // Keep draining UART RX during DMA flush so STOP/MUTE/WHOAMI never stall
    pollDuringAudio(-1);
  }
}

static bool pollDuringAudio(int currentClip) {
  unsigned long now = millis();
  publishTelemetryIfDue(now);

  String cmd;
  while (serialReadLine(cmd)) {
    if (cmd == "STOP") {
      cmdUntilMs = 0;
      pendingVoice = 0;
      Serial.println("EVT,ACK,STOP_INTERRUPT");
      return true;
    } else if (cmd == "MUTE") {
      muteUntilMs = millis() + MUTE_DURATION_MS;
      pendingVoice = 0;
      Serial.println("EVT,ACK,MUTE_INTERRUPT");
      return true;
    } else if (cmd == "PING") {
      Serial.println("PONG");
    } else if (cmd == "WHOAMI") {
      Serial.println("EVT,WHOAMI,ROLE=AUDIO_MQ135_NODE,FW=PI_AI_3.0");
    } else if (cmd.startsWith("VOICE:") || cmd == "SLEEP") {
      int cid = (cmd == "SLEEP") ? 1 : cmd.substring(6).toInt();
      if (cid < 1 || cid > VOICE_CLIP_COUNT) {
        Serial.printf("EVT,NACK,VOICE,%d\n", cid);
      } else if (cid == currentClip) {
        Serial.printf("EVT,ACK,VOICE,%d,busy\n", cid);
      } else {
        pendingVoice = cid;
        cmdUntilMs = millis() + 5000;
        Serial.printf("EVT,ACK,VOICE,%d,i2s=%d\n", cid, isI2sReady ? 1 : 0);
        return true;
      }
    } else if (cmd.startsWith("VOL:")) {
      int v = cmd.substring(4).toInt();
      if (v >= 10 && v <= 100) {
        audioVolumePct = v;
        prefs.begin("pi_ai_esp1", false);
        prefs.putInt("vol", audioVolumePct);
        prefs.end();
        Serial.printf("EVT,ACK,VOL,%d\n", audioVolumePct);
      } else {
        Serial.println("EVT,NACK,VOL,RANGE_10_100");
      }
    } else if (cmd.startsWith("ALERT:")) {
      int lv = cmd.substring(6, 7).toInt();
      Serial.printf("EVT,ACK,ALERT,%d,busy\n", lv);
    }
  }
  return false;
}

static void playBeepTone(int freqHz, int durationMs) {
  unsigned long now = millis();
  if (!isI2sReady || isBeforeDeadline(now, muteUntilMs)) return;
  int samples = (I2S_SAMPLE_RATE * durationMs) / 1000;
  int16_t buf[128];
  int idx = 0;
  const int32_t amp = (28000L * audioVolumePct) / 100;

  stopRequested = false;
  for (int i = 0; i < samples; i++) {
    if (stopRequested || isBeforeDeadline(millis(), muteUntilMs)) break;
    int16_t sample = (int16_t)(sin(2.0 * PI * freqHz * i / I2S_SAMPLE_RATE) * amp);
    buf[idx++] = sample;
    buf[idx++] = sample;
    if (idx >= 128) {
      i2sWriteBlock(buf, sizeof(buf), 100);
      idx = 0;
      if (pollDuringAudio(0)) {
        stopRequested = true;
        break;
      }
    }
  }
  if (!stopRequested && !isBeforeDeadline(millis(), muteUntilMs) && idx > 0) {
    i2sWriteBlock(buf, (size_t)idx * sizeof(int16_t), 100);
  }
  i2sFlushSilence();
  stopRequested = false;
}

static void playVoiceClip(int clipId) {
  unsigned long now = millis();
  if (!isI2sReady || isBeforeDeadline(now, muteUntilMs)) return;
  if (clipId < 1 || clipId > VOICE_CLIP_COUNT) return;

  const uint8_t *data = VOICE_CLIPS[clipId - 1];
  uint32_t len = VOICE_LENS[clipId - 1];
  if (!data || len == 0) return;

  stopRequested = false;
  isPlayingVoice = true;
  digitalWrite(LED_PIN, HIGH);

  int16_t buf[128];
  int bufIdx = 0;
  for (uint32_t i = 0; i < len; i++) {
    if (stopRequested || isBeforeDeadline(millis(), muteUntilMs)) break;
    int16_t sample = applyVolume(ulawDecode(pgm_read_byte(&data[i])));
    buf[bufIdx++] = sample;
    buf[bufIdx++] = sample;
    if (bufIdx >= 128) {
      i2sWriteBlock(buf, sizeof(buf), 100);
      bufIdx = 0;
      if (pollDuringAudio(clipId)) {
        stopRequested = true;
        break;
      }
    }
  }
  if (!stopRequested && !isBeforeDeadline(millis(), muteUntilMs) && bufIdx > 0) {
    i2sWriteBlock(buf, (size_t)bufIdx * sizeof(int16_t), 100);
  }
  i2sFlushSilence();
  isPlayingVoice = false;
  stopRequested = false;
  digitalWrite(LED_PIN, LOW);
}

// ---------------- Serial Command Handler ----------------
static void handleCommand(const String &cmd) {
  if (cmd == "PING") {
    Serial.println("PONG");
  } else if (cmd == "WHOAMI") {
    Serial.println("EVT,WHOAMI,ROLE=AUDIO_MQ135_NODE,FW=PI_AI_3.0");
  } else if (cmd == "STOP") {
    cmdUntilMs = 0;
    pendingVoice = 0;
    stopRequested = true;
    Serial.println("EVT,ACK,STOP");
  } else if (cmd == "MUTE") {
    muteUntilMs = millis() + MUTE_DURATION_MS;
    pendingVoice = 0;
    stopRequested = true;
    Serial.println("EVT,ACK,MUTE");
  } else if (cmd.startsWith("VOL:")) {
    int v = cmd.substring(4).toInt();
    if (v >= 10 && v <= 100) {
      audioVolumePct = v;
      prefs.begin("pi_ai_esp1", false);
      prefs.putInt("vol", audioVolumePct);
      prefs.end();
      Serial.printf("EVT,ACK,VOL,%d\n", audioVolumePct);
    } else {
      Serial.println("EVT,NACK,VOL,RANGE_10_100");
    }
  } else if (cmd.startsWith("VOICE:") || cmd == "SLEEP") {
    int cid = (cmd == "SLEEP") ? 1 : cmd.substring(6).toInt();
    if (cid < 1 || cid > VOICE_CLIP_COUNT) {
      Serial.printf("EVT,NACK,VOICE,%d\n", cid);
      return;
    }
    cmdUntilMs = millis() + 5000;
    Serial.printf("EVT,ACK,VOICE,%d,i2s=%d\n", cid, isI2sReady ? 1 : 0);
    playVoiceClip(cid);
  } else if (cmd.startsWith("ALERT:")) {
    int lv = cmd.substring(6, 7).toInt();
    int durMs = 400;
    int c2 = cmd.indexOf(':', 6);
    if (c2 > 0) durMs = constrain(cmd.substring(c2 + 1).toInt(), 50, 5000);
    cmdUntilMs = millis() + (unsigned long)durMs;
    Serial.printf("EVT,ACK,ALERT,%d\n", lv);
    playBeepTone((lv >= 2) ? 1200 : 880, durMs);
  } else if (cmd.startsWith("CFG_")) {
    // Never echo CFG_TOKEN / CFG_WIFI secrets if ESP1/ESP2 ports were accidentally swapped!
    Serial.println("EVT,NACK,WRONG_ROLE_CFG");
  } else {
    Serial.printf("EVT,NACK,UNKNOWN,%s\n", cmd.substring(0, 32).c_str());
  }
}

static void publishTelemetryIfDue(unsigned long now) {
  if (now - lastTelemetryMs < 500) return;
  lastTelemetryMs = now;
  seqCounter++;

  if (!isPlayingVoice) {
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));
  }

  bool warmingUp = (now - bootMs) < MQ_WARMUP_MS;
  bool muted = isBeforeDeadline(now, muteUntilMs);
  char buf[220];
  snprintf(
    buf,
    sizeof(buf),
    "{\"seq\":%lu,\"role\":\"AUDIO_MQ135_NODE\",\"co2_ppm\":%.1f,\"raw_adc\":%d,"
    "\"co2_level\":%d,\"warmup\":%s,\"muted\":%s,\"vol\":%d,\"free_heap\":%lu}",
    seqCounter,
    ppmFiltered,
    adcRaw,
    co2Level,
    warmingUp ? "true" : "false",
    muted ? "true" : "false",
    audioVolumePct,
    (unsigned long)ESP.getFreeHeap()
  );
  Serial.print("FDESP:");
  Serial.println(buf);
}

// ---------------- Arduino Lifecycle ----------------
void setup() {
  // Physically disable Bluetooth radio; Wi-Fi stack is not even compiled in!
  btStop();

  Serial.setRxBufferSize(2048);
  Serial.begin(SERIAL_BAUD);
  Serial.setTimeout(15);

  pinMode(LED_PIN, OUTPUT);
  pinMode(MQ_PIN, INPUT);
  digitalWrite(LED_PIN, LOW);

  prefs.begin("pi_ai_esp1", true);
  audioVolumePct = constrain(prefs.getInt("vol", DEFAULT_VOL_PCT), 10, 100);
  prefs.end();

  initI2S();
  bootMs = millis();

  digitalWrite(LED_PIN, HIGH);
  delay(150);
  digitalWrite(LED_PIN, LOW);

  Serial.println("EVT,BOOT_AUDIO_MQ135_READY,115200");
  Serial.println("EVT,WHOAMI,ROLE=AUDIO_MQ135_NODE,FW=PI_AI_3.0");
  esp_reset_reason_t rr = esp_reset_reason();
  Serial.printf("EVT,RESET_REASON,%s\n", resetReasonName(rr));
  if (rr == ESP_RST_BROWNOUT) {
    Serial.println("EVT,WARN,BROWNOUT_SUT_AP_NGUON_LOA");
  }
}

void loop() {
  String cmd;
  while (serialReadLine(cmd)) {
    handleCommand(cmd);
  }
  while (pendingVoice > 0) {
    int cid = pendingVoice;
    pendingVoice = 0;
    playVoiceClip(cid);
  }

  unsigned long now = millis();
  if (now - lastSampleMs >= 100) {
    lastSampleMs = now;
    adcRaw = analogRead(MQ_PIN);
    float ppmApprox = (adcRaw / 4095.0f) * 2000.0f;
    ppmFiltered = EMA_ALPHA * ppmApprox + (1.0f - EMA_ALPHA) * ppmFiltered;

    if (ppmFiltered >= CO2_CRIT_PPM) {
      co2Level = 2;
    } else if (ppmFiltered >= CO2_WARN_PPM) {
      co2Level = 1;
    } else {
      co2Level = 0;
    }

    bool warmingUp = (now - bootMs) < MQ_WARMUP_MS;
    if (!warmingUp && co2Level > 0 && (now - lastCo2AlertMs >= CO2_ALERT_COOLDOWN)
        && !isBeforeDeadline(now, muteUntilMs) && !isPlayingVoice && !isBeforeDeadline(now, cmdUntilMs)) {
      lastCo2AlertMs = now;
      if (co2Level == 2) {
        Serial.printf("EVT,AUTO_ALERT,CO2_CRIT,%.1f\n", ppmFiltered);
        playVoiceClip(5);
      } else {
        Serial.printf("EVT,AUTO_ALERT,CO2_WARN,%.1f\n", ppmFiltered);
        playVoiceClip(4);
      }
      now = millis();
    }
  }

  publishTelemetryIfDue(now);
  vTaskDelay(pdMS_TO_TICKS(2));
}
