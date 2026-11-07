/**
 * ==============================================================================
 * HỆ THỐNG GIÁM SÁT TÀI XẾ & IOT — FIRMWARE ESP32 HOÀN THIỆN GPS + CO2 + I2S
 * Tương thích chuẩn hợp đồng AI/AI (G2.5 - G3) & YOLO26
 * ==============================================================================
 * Bản sửa chống đơ / chập chờn loa:
 *  - Đọc lệnh Serial KHÔNG CHẶN (không dùng readStringUntil -> trước đây có thể treo
 *    tới 1000ms giữa lúc phát -> DMA I2S cạn dữ liệu -> tiếng bị giật/ngắt).
 *  - Trong lúc phát: STOP/MUTE ngắt ngay, PING vẫn trả PONG, VOICE trùng câu trả
 *    "busy" (không phát chồng), VOICE khác câu sẽ chuyển câu ngay. Không còn nuốt mất lệnh.
 *  - Tăng bộ đệm DMA I2S (8 x 256 frame ~128ms) chịu được trễ ngắn.
 *  - Giảm âm lượng (AUDIO_VOLUME_PCT) để hạ dòng đỉnh của MAX98357A, tránh sụt áp
 *    nguồn USB làm ESP32 brownout/reset ("đứng luôn").
 *  - Báo lý do reset lúc khởi động (EVT,RESET_REASON,...) để chẩn đoán brownout.
 */
#include <Arduino.h>
#include <HardwareSerial.h>
#include <math.h>
#include <esp_system.h>
#include "voice_clips.h"

// WiFi uplink thật: bật tự động khi có file wifi_config.h cạnh sketch (xem wifi_config.h.example)
#if defined(__has_include)
  #if __has_include("wifi_config.h")
    #include "wifi_config.h"
    #define ESP_WIFI_UPLINK 1
  #else
    #define ESP_WIFI_UPLINK 0
  #endif
#else
  #define ESP_WIFI_UPLINK 0
#endif
#if ESP_WIFI_UPLINK
  #include <WiFi.h>
  #include <HTTPClient.h>
#endif

// ======================== CẤU HÌNH GIAI ĐOẠN G11 (HIGH-SPEED UART & GATEWAY) ===
#define SERIAL_BAUD_HIGHSPEED 921600  // Chuẩn tốc độ cao G11 (921,600 bps)
#define SERIAL_BAUD_FALLBACK  115200  // Tốc độ tương thích ngược (115,200 bps)
#define SERIAL_BAUD_DEFAULT   SERIAL_BAUD_HIGHSPEED

// Định danh khung tin nhị phân FDFRAME (0xAA 0x55)
#define FDFRAME_MAGIC_0            0xAA
#define FDFRAME_MAGIC_1            0x55
#define FRAME_TYPE_HEARTBEAT       0x00
#define FRAME_TYPE_TELEMETRY       0x01
#define FRAME_TYPE_VIOLATION       0x02
#define FRAME_TYPE_IMAGE_CHUNK     0x03
#define FRAME_TYPE_DOWNLINK_ACK    0x04
#define FRAME_TYPE_COMMAND         0x05
#define FRAME_TYPE_NETWORK_STATUS  0x06

static bool     gatewayFlowPaused = false;
static uint16_t gatewayFrameSeq = 0;
static uint32_t chunksForwardedCount = 0;

// Giải thuật CRC-16-CCITT (Đa thức 0x1021, khởi tạo 0xFFFF) chuẩn G11
static uint16_t crc16_ccitt(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= ((uint16_t)data[i] << 8);
    for (uint8_t b = 0; b < 8; b++) {
      if (crc & 0x8000) crc = (crc << 1) ^ 0x1021;
      else              crc = (crc << 1);
    }
  }
  return crc;
}

// Đóng gói và gửi khung FDFRAME ngược về Raspberry Pi qua UART
static void sendFdFrameToPi(uint8_t frameType, const String &jsonPayload) {
  gatewayFrameSeq++;
  size_t pLen = jsonPayload.length();
  uint8_t header[7];
  header[0] = FDFRAME_MAGIC_0;
  header[1] = FDFRAME_MAGIC_1;
  header[2] = frameType;
  header[3] = (uint8_t)((gatewayFrameSeq >> 8) & 0xFF);
  header[4] = (uint8_t)(gatewayFrameSeq & 0xFF);
  header[5] = (uint8_t)((pLen >> 8) & 0xFF);
  header[6] = (uint8_t)(pLen & 0xFF);

  // Tính CRC16 trên [FrameType + Seq + Len + Payload]
  uint16_t crc = 0xFFFF;
  for (size_t i = 2; i < 7; i++) {
    crc ^= ((uint16_t)header[i] << 8);
    for (uint8_t b = 0; b < 8; b++) {
      crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
    }
  }
  for (size_t i = 0; i < pLen; i++) {
    crc ^= ((uint16_t)((uint8_t)jsonPayload[i]) << 8);
    for (uint8_t b = 0; b < 8; b++) {
      crc = (crc & 0x8000) ? ((crc << 1) ^ 0x1021) : (crc << 1);
    }
  }
  uint8_t crcBytes[3] = { (uint8_t)((crc >> 8) & 0xFF), (uint8_t)(crc & 0xFF), '\n' };
  Serial.write(header, 7);
  if (pLen > 0) Serial.write((const uint8_t *)jsonPayload.c_str(), pLen);
  Serial.write(crcBytes, 3);
}

// ======================== CẤU HÌNH GPS SERIAL2 ========================
#define GPS_RX_PIN  16   // Nối vào chân TX của Module GPS
#define GPS_TX_PIN  17   // Nối vào chân RX của Module GPS
#define GPS_BAUD    9600 // Tốc độ chuẩn của NEO-6M / NEO-8M / ATGM336H

HardwareSerial gpsSerial(2);

// ======================== CẤU HÌNH MẠCH ÂM THANH MAX98357A I2S ========
#define I2S_BCLK_PIN    26
#define I2S_LRC_PIN     25
#define I2S_DIN_PIN     22
#define I2S_SAMPLE_RATE 16000
#define I2S_DMA_BUF_COUNT 8
#define I2S_DMA_BUF_LEN   256

// Âm lượng phần mềm (%). 100 = to nhất nhưng dòng đỉnh lớn dễ sụt áp nguồn USB.
// Nếu đã cấp nguồn 5V riêng cho MAX98357A có thể tăng lên 70-100.
#define AUDIO_VOLUME_PCT 45

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  #include "driver/i2s_std.h"
#else
  #include "driver/i2s.h"
  #define I2S_NUM I2S_NUM_0
#endif

// ======================== CHÂN CẢM BIẾN & LED =========================
#define MQ_PIN     34   // Đọc Analog MQ-135 qua cầu chia áp
#define LED_PIN    2    // LED báo trạng thái
#define MUTE_PIN   27   // Nút nhấn tắt chuông tạm thời (Active LOW)

// ======================== BIẾN ĐO ĐẠC GPS CHỐNG LỖI VẶT =================
struct GpsData {
  bool   isValid = false;
  double latitude = 0.0;
  double longitude = 0.0;
  float  speedKmh = 0.0;
  float  altitudeM = 0.0;
  float  headingDeg = 0.0;
  int    satellites = 0;
  unsigned long lastFixTime = 0;
};

GpsData gpsInfo;
String  nmeaBuffer = "";

// ======================== BIẾN CẢM BIẾN MQ-135 & CẢNH BÁO ĐỘC LẬP =====
#define CO2_WARN_PPM        1000.0  // Cảnh báo bí khí: nhắc mở cửa sổ
#define CO2_CRIT_PPM        1500.0  // Cảnh báo nguy hiểm: yêu cầu mở cửa ngay
#define CO2_ALERT_COOLDOWN  15000   // Khoảng cách giữa 2 lần nhắc lại giọng nói (15s)

float ppmFiltered = 400.0;
int   adcRaw = 0;
int   co2Level = 0; // 0: SAFE, 1: WARN, 2: CRIT
const float EMA_ALPHA = 0.35;
unsigned long lastSample = 0;
unsigned long lastTelemetry = 0;
unsigned long lastCo2Alert = 0;
unsigned long seqCounter = 0;

// ======================== ĐIỀU KHIỂN CÒI BÁO ===========================
int  cmdLevel = 0;
unsigned long cmdUntil = 0;
unsigned long sleepUntil = 0;
unsigned long muteUntil = 0;
bool isI2sReady = false;
volatile bool stopRequested = false;
volatile bool isPlayingVoice = false;
int  pendingVoice = 0;   // Câu thoại chờ phát (khi đang phát nhận VOICE khác)
#if ESP_ARDUINO_VERSION_MAJOR >= 3
i2s_chan_handle_t i2sTxChan = NULL;
#endif

// Bộ đệm dòng lệnh Serial không chặn (hỗ trợ song song ASCII & Khung nhị phân FDFRAME 4KB)
static char     rxLine[128];
static size_t   rxLen = 0;
static uint8_t  fdRxBuf[4120];
static size_t   fdRxLen = 0;
static size_t   fdExpectedLen = 0;
static bool     inFdFrame = false;
static unsigned long fdFrameStartMs = 0;

static void handleBinaryFdFrame(uint8_t fType, uint16_t seq, const uint8_t *payloadPtr, uint16_t pLen);

// -----------------------------------------------------------------------------
// GIẢI MÃ G.711 U-LAW SANG 16-BIT PCM (VOICE CLIPS)
// -----------------------------------------------------------------------------
static inline int16_t ulawDecode(uint8_t u_val) {
  u_val = ~u_val;
  int16_t t = ((u_val & 0x0F) << 3) + 0x84;
  t <<= ((u_val & 0x70) >> 4);
  return (u_val & 0x80) ? (0x84 - t) : (t - 0x84);
}

static inline int16_t applyVolume(int32_t s) {
  s = (s * AUDIO_VOLUME_PCT) / 100;
  if (s > 32767) s = 32767;
  if (s < -32768) s = -32768;
  return (int16_t)s;
}

// -----------------------------------------------------------------------------
// GIẢI MÃ NMEA GPS TỰ NHẬN (ĐỘ CHÍNH XÁC DOUBLE, CHỐNG TRÔI KHI DỪNG XE)
// -----------------------------------------------------------------------------
double parseNmeaCoord(String val, String hemi) {
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

void parseNmeaSentence(String sentence) {
  // 1. Bắt chuỗi $GPRMC / $GNRMC / $BDRMC (Tọa độ, Vận tốc, Góc hướng di chuyển)
  if (sentence.startsWith("$GPRMC") || sentence.startsWith("$GNRMC") || sentence.startsWith("$BDRMC")) {
    int commaIdx[14];
    int count = 0;
    for (int i = 0; i < sentence.length() && count < 14; i++) {
      if (sentence[i] == ',') commaIdx[count++] = i;
    }
    if (count >= 8) {
      char status = sentence[commaIdx[1] + 1];
      if (status == 'A') { // 'A' = Tín hiệu GPS hợp lệ (Fix thành công)
        String latStr  = sentence.substring(commaIdx[2] + 1, commaIdx[3]);
        String latHemi = sentence.substring(commaIdx[3] + 1, commaIdx[4]);
        String lonStr  = sentence.substring(commaIdx[4] + 1, commaIdx[5]);
        String lonHemi = sentence.substring(commaIdx[5] + 1, commaIdx[6]);
        String spdStr  = sentence.substring(commaIdx[6] + 1, commaIdx[7]);

        double lat = parseNmeaCoord(latStr, latHemi);
        double lon = parseNmeaCoord(lonStr, lonHemi);
        // Tốc độ trong NMEA là knots -> đổi sang km/h (1 knot = 1.852 km/h)
        float spd = spdStr.toFloat() * 1.852;

        // BỘ LỌC CHỐNG TRÔI TỌA ĐỘ KHI XE DỪNG ĐÈN ĐỎ (DEADBAND FILTER):
        // Dưới 1.8 km/h -> Ép về 0.0 km/h để không trôi bản đồ
        if (spd < 1.8) {
          spd = 0.0;
        } else {
          gpsInfo.latitude  = lat;
          gpsInfo.longitude = lon;
        }
        if (gpsInfo.latitude == 0.0 && lat != 0.0) {
          gpsInfo.latitude  = lat;
          gpsInfo.longitude = lon;
        }

        gpsInfo.speedKmh = spd;

        // Góc hướng di chuyển (Course/Heading 0 - 360 độ)
        if (count >= 9) {
          String headStr = sentence.substring(commaIdx[7] + 1, commaIdx[8]);
          if (headStr.length() > 0) {
            gpsInfo.headingDeg = headStr.toFloat();
          }
        }

        gpsInfo.isValid   = true;
        gpsInfo.lastFixTime = millis();
      } else {
        // Tín hiệu tạm mất (Void): Có độ trễ 5 giây (Hysteresis) trước khi coi là mất Fix
        // để tránh nhấp nháy trạng thái khi xe đi qua tán cây/mép cửa sổ
        if (millis() - gpsInfo.lastFixTime > 5000) {
          gpsInfo.isValid = false;
        }
        gpsInfo.speedKmh = 0.0;
      }
    }
  }
  // 2. Bắt chuỗi $GPGGA / $GNGGA / $BDGGA: Số lượng vệ tinh (Satellites) & Cao độ (Altitude)
  else if (sentence.startsWith("$GPGGA") || sentence.startsWith("$GNGGA") || sentence.startsWith("$BDGGA")) {
    int commaIdx[15];
    int count = 0;
    for (int i = 0; i < sentence.length() && count < 15; i++) {
      if (sentence[i] == ',') commaIdx[count++] = i;
    }
    if (count >= 10) {
      char fixQuality = sentence[commaIdx[5] + 1];
      if (fixQuality != '0') {
        String satsStr = sentence.substring(commaIdx[6] + 1, commaIdx[7]);
        String altStr  = sentence.substring(commaIdx[8] + 1, commaIdx[9]);
        if (satsStr.length() > 0) gpsInfo.satellites = satsStr.toInt();
        if (altStr.length() > 0)  gpsInfo.altitudeM  = altStr.toFloat();
      }
    }
  }
}

void updateGps() {
  while (gpsSerial.available() > 0) {
    char c = gpsSerial.read();
    if (c == '\n' || c == '\r') {
      if (nmeaBuffer.length() > 10) {
        parseNmeaSentence(nmeaBuffer);
      }
      nmeaBuffer = "";
    } else if (nmeaBuffer.length() < 120) {
      nmeaBuffer += c;
    }
  }
  // Mất tín hiệu quá 5 giây thì coi như mất fix
  if (millis() - gpsInfo.lastFixTime > 5000) {
    gpsInfo.isValid = false;
  }
}

// -----------------------------------------------------------------------------
// ĐỌC DÒNG LỆNH SERIAL & KHUNG NHỊ PHÂN FDFRAME KHÔNG CHẶN
// - Tự động phân tách khung nhị phân FDFRAME (0xAA 0x55) dài tới 4KB, bảo toàn
//   dữ liệu chứa byte 0x00, chữ thường JSON và Base64, kiểm tra CRC-16-CCITT.
// - Các lệnh văn bản ASCII (VOICE:1, STOP, PING...) trả về qua `out` khi gặp '\n'.
// -----------------------------------------------------------------------------
bool serialReadLine(String &out) {
  // Timeout chống treo khung nhị phân dở dang quá 200ms
  if (inFdFrame && (millis() - fdFrameStartMs > 200)) {
    inFdFrame = false;
    fdRxLen = 0;
    fdExpectedLen = 0;
  }

  while (Serial.available() > 0) {
    uint8_t b = (uint8_t)Serial.read();

    // 1. Đang thu thập khung nhị phân FDFRAME (0xAA 0x55)
    if (inFdFrame) {
      if (fdRxLen < sizeof(fdRxBuf)) {
        fdRxBuf[fdRxLen++] = b;
      } else {
        inFdFrame = false;
        fdRxLen = 0;
        fdExpectedLen = 0;
        continue;
      }

      if (fdRxLen == 2 && b != FDFRAME_MAGIC_1) {
        // Không phải 0xAA 0x55 -> thoát chế độ khung nhị phân
        inFdFrame = false;
        fdRxLen = 0;
        continue;
      }

      if (fdRxLen == 7) {
        uint16_t pLen = ((uint16_t)fdRxBuf[5] << 8) | (uint16_t)fdRxBuf[6];
        if (pLen > 4096) {
          // Chiều dài vượt trần cho phép -> hủy khung lỗi
          inFdFrame = false;
          fdRxLen = 0;
          fdExpectedLen = 0;
          continue;
        }
        fdExpectedLen = 7 + (size_t)pLen + 2;
      }

      if (fdExpectedLen > 0 && fdRxLen == fdExpectedLen) {
        uint8_t  fType = fdRxBuf[2];
        uint16_t seq   = ((uint16_t)fdRxBuf[3] << 8) | (uint16_t)fdRxBuf[4];
        uint16_t pLen  = ((uint16_t)fdRxBuf[5] << 8) | (uint16_t)fdRxBuf[6];
        uint16_t rxCrc = ((uint16_t)fdRxBuf[7 + pLen] << 8) | (uint16_t)fdRxBuf[8 + pLen];
        uint16_t calcCrc = crc16_ccitt(&fdRxBuf[2], 5 + pLen);

        inFdFrame = false;
        fdRxLen = 0;
        fdExpectedLen = 0;

        if (rxCrc == calcCrc) {
          handleBinaryFdFrame(fType, seq, &fdRxBuf[7], pLen);
        } else {
          Serial.printf("EVT,ERR,FDFRAME_CRC,rx=0x%04X,calc=0x%04X\n", rxCrc, calcCrc);
        }
      }
      continue;
    }

    // 2. Phát hiện byte mở đầu khung nhị phân FDFRAME (0xAA)
    if (b == FDFRAME_MAGIC_0 && rxLen == 0) {
      inFdFrame = true;
      fdRxBuf[0] = b;
      fdRxLen = 1;
      fdExpectedLen = 0;
      fdFrameStartMs = millis();
      continue;
    }

    // 3. Chế độ dòng lệnh văn bản ASCII thông thường
    char ch = (char)b;
    if (ch == '\r') continue;
    if (ch == '\n') {
      rxLine[rxLen] = '\0';
      out = String(rxLine);
      rxLen = 0;
      out.trim();
      out.toUpperCase();
      if (out.length() == 0) continue;
      return true;
    }
    if (rxLen < sizeof(rxLine) - 1) {
      rxLine[rxLen++] = ch;
    } else {
      rxLen = 0; // Dòng quá dài/nhiễu -> bỏ
    }
  }
  return false;
}

// -----------------------------------------------------------------------------
// KHỞI TẠO MẠCH ÂM THANH I2S
// -----------------------------------------------------------------------------
void initI2S() {
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
  // Core Arduino-ESP32 >= 3.x: dùng driver i2s_std (BUG-001)
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

static void i2sFlushSilence() {
  int16_t zero[128];
  memset(zero, 0, sizeof(zero));
  // Đẩy đủ im lặng để xả hết DMA, tránh tiếng rè/lặp đoạn cuối
  for (int i = 0; i < (I2S_DMA_BUF_COUNT * I2S_DMA_BUF_LEN * 2) / 128 + 1; i++) {
    i2sWriteBlock(zero, sizeof(zero), 20);
  }
}

// -----------------------------------------------------------------------------
// KIỂM TRA LỆNH / NÚT TRONG LÚC ĐANG PHÁT (KHÔNG CHẶN)
// Trả về true nếu phải dừng phát ngay.
// currentClip: id câu đang phát (0 = tiếng bíp)
// -----------------------------------------------------------------------------
bool pollDuringAudio(int currentClip) {
  if (digitalRead(MUTE_PIN) == LOW) {
    muteUntil = millis() + 30000;
    Serial.println("EVT,MUTE_PRESSED");
    return true;
  }
  String cmd;
  while (serialReadLine(cmd)) {
    if (cmd == "STOP") {
      cmdLevel = 0;
      cmdUntil = 0;
      pendingVoice = 0;
      Serial.println("EVT,ACK,STOP_INTERRUPT");
      return true;
    } else if (cmd == "MUTE") {
      muteUntil = millis() + 30000;
      pendingVoice = 0;
      Serial.println("EVT,ACK,MUTE_INTERRUPT");
      return true;
    } else if (cmd == "PING") {
      Serial.println("PONG");
    } else if (cmd == "CONNECT") {
      Serial.println("EVT,ACK,CONNECT");
    } else if (cmd.startsWith("VOICE:") || cmd == "SLEEP") {
      int cid = (cmd == "SLEEP") ? 1 : cmd.substring(6).toInt();
      if (cid < 1 || cid > VOICE_CLIP_COUNT) {
        Serial.printf("EVT,NACK,VOICE,%d\n", cid);
      } else if (cid == currentClip) {
        // Đang phát đúng câu này -> không phát chồng, vẫn ACK để PC không gửi lại
        Serial.printf("EVT,ACK,VOICE,%d,busy\n", cid);
      } else {
        pendingVoice = cid;
        cmdLevel = 2;
        cmdUntil = millis() + 5000;
        Serial.printf("EVT,ACK,VOICE,%d,i2s=%d\n", cid, isI2sReady ? 1 : 0);
        return true; // Chuyển sang câu mới ngay
      }
    } else if (cmd.startsWith("ALERT:")) {
      int lv = cmd.substring(6, 7).toInt();
      Serial.printf("EVT,ACK,ALERT,%d,busy\n", lv);
    }
  }
  return false;
}

void playBeepTone(int freqHz, int durationMs) {
  if (!isI2sReady || millis() < muteUntil) return;
  int samples = (I2S_SAMPLE_RATE * durationMs) / 1000;
  int16_t buf[128];
  int idx = 0;
  const int32_t amp = (16000L * AUDIO_VOLUME_PCT) / 100;

  stopRequested = false;

  for (int i = 0; i < samples; i++) {
    if (stopRequested || millis() < muteUntil) break;

    int16_t sample = (int16_t)(sin(2.0 * PI * freqHz * i / I2S_SAMPLE_RATE) * amp);
    buf[idx++] = sample; // Kênh L
    buf[idx++] = sample; // Kênh R
    if (idx >= 128) {
      i2sWriteBlock(buf, sizeof(buf), 100);
      idx = 0;
      if (pollDuringAudio(0)) {
        stopRequested = true;
        break;
      }
    }
  }
  i2sFlushSilence();
  stopRequested = false;
}

void playVoiceClip(int clipId) {
  if (!isI2sReady || millis() < muteUntil) return;
  if (clipId < 1 || clipId > VOICE_CLIP_COUNT) return;

  int cIdx = clipId - 1;
  const uint8_t *data = VOICE_CLIPS[cIdx];
  uint32_t len = VOICE_LENS[cIdx];
  if (!data || len == 0) return;

  stopRequested = false;
  isPlayingVoice = true;
  digitalWrite(LED_PIN, HIGH);

  int16_t buf[128];
  int bufIdx = 0;

  for (uint32_t i = 0; i < len; i++) {
    if (stopRequested || millis() < muteUntil) {
      break;
    }
    uint8_t u_byte = pgm_read_byte(&data[i]);
    int16_t sample = applyVolume(ulawDecode(u_byte));

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

  i2sFlushSilence();
  digitalWrite(LED_PIN, LOW);
  isPlayingVoice = false;
  stopRequested = false;
}

// -----------------------------------------------------------------------------
// CHUYỂN TIẾP VIỄN THÔNG ĐA ĐÍCH QUA UART CAO TỐC 921,600 BPS (G11 OFFLOADER)
// -----------------------------------------------------------------------------
// -----------------------------------------------------------------------------
// UPLINK WIFI THẬT (ESP32 gánh việc mạng thay Pi) - chạy ở FreeRTOS task riêng trên core 0
//  - Bật bằng cách tạo file wifi_config.h (xem wifi_config.h.example). Không có file -> tắt hẳn,
//    ESP báo wifi_connected=false để Pi tự dùng HTTP trực tiếp (không còn báo giả "online").
//  - Loop() (core 1: UART + loa I2S + GPS) KHÔNG BAO GIỜ chờ HTTP: chỉ đẩy vào hàng đợi.
//  - Hàng đợi 6 gói, mỗi gói <= 4KB cấp phát động; đầy/heap thấp -> is_paused=true để Pi dừng đẩy.
// -----------------------------------------------------------------------------
struct UplinkMsg {
  char   *body;
  size_t  len;
  uint8_t endpointId;  // 0 = telemetry, 1 = violation
};

static volatile bool    uplinkWifiUp = false;
static volatile bool    uplinkServerOk = false;
static volatile int8_t  uplinkRssi = 0;
static volatile uint32_t uplinkQueueDepth = 0;
static char             uplinkAckBuf[420];
static volatile bool    uplinkAckPending = false;

#if ESP_WIFI_UPLINK
static QueueHandle_t uplinkQueue = NULL;

static void uplinkTask(void *arg) {
  (void)arg;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);                 // giảm dòng đỉnh, tránh sụt áp làm loa/ESP reset
  WiFi.setTxPower(WIFI_POWER_11dBm);
  unsigned long lastBegin = 0;
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      uplinkWifiUp = false;
      uplinkServerOk = false;
      if (millis() - lastBegin > 8000 || lastBegin == 0) {
        lastBegin = millis();
        WiFi.disconnect(false);
        WiFi.begin(UPLINK_WIFI_SSID, UPLINK_WIFI_PASS);
      }
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    uplinkWifiUp = true;
    uplinkRssi = (int8_t)WiFi.RSSI();

    UplinkMsg msg;
    if (xQueueReceive(uplinkQueue, &msg, pdMS_TO_TICKS(500)) != pdTRUE) continue;
    uplinkQueueDepth = uxQueueMessagesWaiting(uplinkQueue);

    HTTPClient http;
    String url = String(UPLINK_SERVER_URL) + (msg.endpointId == 1 ? "/api/v1/violation" : "/api/v1/telemetry");
    http.setConnectTimeout(1500);
    http.setTimeout(2500);
    if (http.begin(url)) {
      http.addHeader("Content-Type", "application/json; charset=utf-8");
      int code = http.POST((uint8_t *)msg.body, msg.len);
      uplinkServerOk = (code >= 200 && code < 300);
      if (uplinkServerOk && msg.endpointId == 0 && !uplinkAckPending) {
        String resp = http.getString();
        if (resp.length() > 0 && resp.length() < 380) {
          // Chuyển nguyên phản hồi Server (kèm downlink_xyz / commands) cho Pi
          snprintf(uplinkAckBuf, sizeof(uplinkAckBuf), "{\"status\":\"ok\",\"http\":%d,\"resp\":%s}", code, resp.c_str());
          uplinkAckPending = true;
        }
      }
      http.end();
    } else {
      uplinkServerOk = false;
    }
    free(msg.body);
  }
}

static void uplinkStart() {
  uplinkQueue = xQueueCreate(6, sizeof(UplinkMsg));
  xTaskCreatePinnedToCore(uplinkTask, "uplink", 12288, NULL, 1, NULL, 0);
}

static bool uplinkEnqueue(uint8_t endpointId, const String &jsonBody) {
  if (uplinkQueue == NULL || !uplinkWifiUp) return false;
  size_t n = jsonBody.length();
  if (n == 0 || n > 4096 || ESP.getFreeHeap() < 45000) return false;
  UplinkMsg msg;
  msg.body = (char *)malloc(n + 1);
  if (msg.body == NULL) return false;
  memcpy(msg.body, jsonBody.c_str(), n + 1);
  msg.len = n;
  msg.endpointId = endpointId;
  if (xQueueSend(uplinkQueue, &msg, 0) != pdTRUE) {
    free(msg.body);
    return false;
  }
  uplinkQueueDepth = uxQueueMessagesWaiting(uplinkQueue);
  return true;
}
#else
static void uplinkStart() {}
static bool uplinkEnqueue(uint8_t, const String &) { return false; }
#endif

static bool forwardJsonToServer(const String &endpoint, const String &jsonBody) {
  chunksForwardedCount++;
  uint8_t id = (endpoint == "/api/v1/violation") ? 1 : 0;
  if (endpoint != "/api/v1/telemetry" && endpoint != "/api/v1/violation") return false;  // chunk ảnh: chưa hỗ trợ
  return uplinkEnqueue(id, jsonBody);
}

// -----------------------------------------------------------------------------
// XỬ LÝ KHUNG NHỊ PHÂN FDFRAME ĐÃ XÁC THỰC CRC-16-CCITT (G11 OFFLOADER)
// -----------------------------------------------------------------------------
static void handleBinaryFdFrame(uint8_t fType, uint16_t seq, const uint8_t *payloadPtr, uint16_t pLen) {
  String payload = "";
  if (pLen > 0 && payloadPtr != NULL) {
    payload.reserve(pLen + 1);
    for (uint16_t i = 0; i < pLen; i++) {
      payload += (char)payloadPtr[i];
    }
  }
  Serial.printf("EVT,ACK,FDFRAME,type=%u,seq=%u,len=%u\n", fType, seq, pLen);

  if (fType == FRAME_TYPE_VIOLATION) {
    // Pi đã tự gửi lệnh VOICE tương ứng; KHÔNG tự phát câu 1 ở đây nữa (trước đây mọi vi phạm
    // đều bị phát "ngủ gật" và chồng với câu Pi gửi). Đường dẫn đúng của Server là /api/v1/violation.
    forwardJsonToServer("/api/v1/violation", payload);
  } else if (fType == FRAME_TYPE_IMAGE_CHUNK) {
    forwardJsonToServer("/api/v1/chunks", payload);
  } else if (fType == FRAME_TYPE_TELEMETRY) {
    forwardJsonToServer("/api/v1/telemetry", payload);
  } else if (fType == FRAME_TYPE_HEARTBEAT) {
    sendFdFrameToPi(FRAME_TYPE_HEARTBEAT, "{\"status\":\"alive\"}");
  } else if (fType == FRAME_TYPE_COMMAND) {
    if (payload.indexOf("BUZZER_OFF") >= 0 || payload.indexOf("STOP") >= 0) {
      cmdLevel = 0;
      cmdUntil = 0;
      pendingVoice = 0;
      stopRequested = true;
    }
  }
}

// -----------------------------------------------------------------------------
// XỬ LÝ LỆNH VĂN BẢN ASCII TỪ PI / PC (Serial UART 921,600 / 115,200 bps) - KHÔNG CHẶN
// -----------------------------------------------------------------------------
void handleCommand(const String &cmd) {
  if (cmd.startsWith("BAUD:")) {
    long newBaud = cmd.substring(5).toInt();
    if (newBaud == 921600 || newBaud == 460800 || newBaud == 230400 || newBaud == 115200) {
      Serial.printf("EVT,ACK,BAUD,%ld\n", newBaud);
      Serial.flush();
      delay(20);
      Serial.updateBaudRate(newBaud);
    }
  } else if (cmd.startsWith("SERVER:")) {
    Serial.printf("EVT,ACK,SERVER,%s\n", cmd.substring(7).c_str());
  } else if (cmd.startsWith("ALERT:")) {
    int lv = cmd.substring(6, 7).toInt();
    int durMs = 0;
    int p = cmd.indexOf(':', 6);
    if (p > 0) durMs = cmd.substring(p + 1).toInt();
    if (lv == 1 || lv == 2) {
      if (durMs <= 0) durMs = (lv == 1) ? 300 : 500;
      if (durMs > 1500) durMs = 1500;
      cmdLevel = lv;
      cmdUntil = millis() + 2000;
      Serial.printf("EVT,ACK,ALERT,%d,i2s=%d\n", lv, isI2sReady ? 1 : 0);
      playBeepTone(lv == 1 ? 700 : 1100, durMs);
    } else {
      Serial.printf("EVT,NACK,ALERT,%d\n", lv);
    }
  } else if (cmd.startsWith("VOICE:")) {
    int cid = cmd.substring(6).toInt();
    if (cid < 1 || cid > VOICE_CLIP_COUNT) {
      Serial.printf("EVT,NACK,VOICE,%d\n", cid);
      return;
    }
    cmdLevel = 2;
    cmdUntil = millis() + 5000;
    Serial.printf("EVT,ACK,VOICE,%d,i2s=%d\n", cid, isI2sReady ? 1 : 0);
    pendingVoice = cid;
  } else if (cmd == "SLEEP") {
    cmdLevel = 2;
    cmdUntil = millis() + 5000;
    Serial.printf("EVT,ACK,SLEEP,i2s=%d\n", isI2sReady ? 1 : 0);
    pendingVoice = 1;
  } else if (cmd == "STOP") {
    cmdLevel = 0;
    cmdUntil = 0;
    pendingVoice = 0;
    stopRequested = true;
    Serial.println("EVT,ACK,STOP");
  } else if (cmd == "CONNECT") {
    Serial.println("EVT,ACK,CONNECT");
  } else if (cmd == "MUTE") {
    muteUntil = millis() + 30000;
    pendingVoice = 0;
    stopRequested = true;
    Serial.println("EVT,MUTE");
  } else if (cmd == "PING") {
    Serial.println("PONG");
  }
}

void readSerialCommands() {
  String cmd;
  while (serialReadLine(cmd)) {
    handleCommand(cmd);
  }
  // Phát câu thoại đang chờ (có thể chuyển tiếp câu khác nếu bị VOICE mới ngắt)
  while (pendingVoice > 0) {
    int cid = pendingVoice;
    pendingVoice = 0;
    playVoiceClip(cid);
  }
}

static const char *resetReasonName(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:  return "POWERON";
    case ESP_RST_SW:       return "SOFTWARE";
    case ESP_RST_PANIC:    return "PANIC";
    case ESP_RST_INT_WDT:  return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT:      return "WDT";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_EXT:      return "EXTERNAL";
    default:               return "OTHER";
  }
}

// -----------------------------------------------------------------------------
// SETUP & LOOP CHÍNH (CHUẨN G11: UART 921,600 BPS + FDFRAME + TELEMATICS GATEWAY)
// -----------------------------------------------------------------------------
void setup() {
  // Mở rộng bộ đệm RX lên 4096 bytes để hứng trọn các mảnh ảnh 2KB-4KB ở tốc độ 921,600 bps
  Serial.setRxBufferSize(4096);
  Serial.begin(SERIAL_BAUD_DEFAULT);
  Serial.setTimeout(15);
  gpsSerial.setRxBufferSize(1024);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  pinMode(LED_PIN, OUTPUT);
  pinMode(MUTE_PIN, INPUT_PULLUP);
  pinMode(MQ_PIN, INPUT);

  initI2S();
  uplinkStart();

  digitalWrite(LED_PIN, HIGH);
  delay(200);
  digitalWrite(LED_PIN, LOW);

  Serial.println("EVT,BOOT_GPS_READY,G11_921600");
  esp_reset_reason_t rr = esp_reset_reason();
  Serial.printf("EVT,RESET_REASON,%s\n", resetReasonName(rr));
  if (rr == ESP_RST_BROWNOUT) {
    Serial.println("EVT,WARN,BROWNOUT_SUT_AP_NGUON_LOA");
  }
}

void loop() {
  unsigned long now = millis();

  // 1. Luôn cập nhật GPS liên tục
  updateGps();

  // 2. Tiếp nhận lệnh còi báo & khung FDFRAME từ Pi/PC (không chặn)
  readSerialCommands();
  now = millis();

  // 3. Đọc nút Mute (chống dội, không delay chặn)
  static unsigned long lastMuteEvt = 0;
  if (digitalRead(MUTE_PIN) == LOW && now - lastMuteEvt > 300) {
    lastMuteEvt = now;
    muteUntil = now + 30000;
    Serial.println("EVT,MUTE_PRESSED");
  }

  // 4. Lấy mẫu cảm biến CO2 mỗi 100ms & Tự động cảnh báo độc lập ra loa
  if (now - lastSample >= 100) {
    lastSample = now;
    adcRaw = analogRead(MQ_PIN);
    float ppmApprox = (adcRaw / 4095.0) * 2000.0;
    ppmFiltered = EMA_ALPHA * ppmApprox + (1.0 - EMA_ALPHA) * ppmFiltered;

    if (ppmFiltered >= CO2_CRIT_PPM) {
      co2Level = 2;
    } else if (ppmFiltered >= CO2_WARN_PPM) {
      co2Level = 1;
    } else {
      co2Level = 0;
    }

    if (co2Level > 0 && (now - lastCo2Alert >= CO2_ALERT_COOLDOWN) && (now >= muteUntil)
        && !isPlayingVoice && now >= cmdUntil) {
      lastCo2Alert = now;
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

  // 5. Xuất bản tin Telemetry mỗi 500ms (Chuẩn FDESP + FDFRAME G11)
  if (now - lastTelemetry >= 500) {
    lastTelemetry = now;
    seqCounter++;
    gatewayFlowPaused = (ESP.getFreeHeap() < 45000) || (uplinkQueueDepth >= 5); // Chống tràn RAM / hàng đợi uplink

    digitalWrite(LED_PIN, !digitalRead(LED_PIN));

    char jsonBuf[280];
    snprintf(jsonBuf, sizeof(jsonBuf),
      "{\"seq\":%lu,\"co2_ppm\":%.1f,\"co2_level\":%d,\"vibration_g\":0.02,\"gps_ready\":%s,\"gps_lat\":%.6f,\"gps_lon\":%.6f,\"speed_kmh\":%.1f,\"heading\":%.1f,\"altitude\":%.1f,\"sats\":%d}",
      seqCounter,
      ppmFiltered,
      co2Level,
      gpsInfo.isValid ? "1.0" : "0.0",
      gpsInfo.isValid ? gpsInfo.latitude : 0.0,
      gpsInfo.isValid ? gpsInfo.longitude : 0.0,
      gpsInfo.isValid ? gpsInfo.speedKmh : 0.0,
      gpsInfo.isValid ? gpsInfo.headingDeg : 0.0,
      gpsInfo.isValid ? gpsInfo.altitudeM : 0.0,
      gpsInfo.satellites
    );

    // A. Xuất chuỗi FDESP: chuẩn hợp đồng logic.py
    Serial.print("FDESP:");
    Serial.println(jsonBuf);

    // B. Xuất chuỗi tương thích ngược
    Serial.printf("CO2:%d,ADC:%d,GPS_FIX:%d,SPD:%.1f,SAT:%d,ALT:%.1f,LV:%d\n",
      (int)ppmFiltered,
      adcRaw,
      gpsInfo.isValid ? 1 : 0,
      gpsInfo.isValid ? gpsInfo.speedKmh : 0.0,
      gpsInfo.satellites,
      gpsInfo.isValid ? gpsInfo.altitudeM : 0.0,
      co2Level
    );

    // C. Báo cáo trạng thái mạng Gateway mỗi 5 chu kỳ (2.5s) bằng khung FDFRAME (số liệu THẬT)
    if (seqCounter % 5 == 0) {
      char netStatusBuf[200];
      snprintf(netStatusBuf, sizeof(netStatusBuf),
        "{\"wifi_connected\":%s,\"partner_server_online\":%s,\"rssi_dbm\":%d,\"is_paused\":%s,\"pending_chunks\":%lu,\"uptime_sec\":%lu}",
        uplinkWifiUp ? "true" : "false",
        uplinkServerOk ? "true" : "false",
        (int)uplinkRssi,
        gatewayFlowPaused ? "true" : "false",
        (unsigned long)uplinkQueueDepth,
        now / 1000
      );
      sendFdFrameToPi(FRAME_TYPE_NETWORK_STATUS, String(netStatusBuf));
    }

    // D. Chuyển phản hồi Server (do task uplink thu) về Pi - chỉ loop() được ghi UART
    if (uplinkAckPending) {
      sendFdFrameToPi(FRAME_TYPE_DOWNLINK_ACK, String(uplinkAckBuf));
      uplinkAckPending = false;
    }
  }

  vTaskDelay(pdMS_TO_TICKS(2));
}
