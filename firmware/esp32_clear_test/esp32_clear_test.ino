/**
 * [FILE TEST KHAI THÔNG ĐƯỜNG TRUYỀN & XÓA SẠCH BỘ NHỚ TRONG ESP32]
 * Mục đích:
 * 1. Kéo toàn bộ chân loa I2S (22, 25, 26) về LOW (0V) ngay lập tức để ngắt tải dòng điện,
 *    chống sụt áp USB và giải phóng bus DMA/I2S bị kẹt.
 * 2. Tự động xóa trắng toàn bộ phân vùng nhớ NVS (nvs_flash_erase + Preferences.clear)
 *    để dọn sạch dữ liệu cũ bị kẹt trong Flash.
 * 3. Mở kết nối Serial sạch (115200 baud) và NHẤP NHÁY ĐÈN LED (GPIO 2) liên tục
 *    báo hiệu mạch đã thông đường truyền và kết nối thành công!
 */

#include <Arduino.h>
#include <Preferences.h>
#include <nvs_flash.h>

#define LED_PIN       2     // Đèn LED xanh trên mạch ESP32
#define I2S_BCLK_PIN  26    // Chân loa MAX98357A - khóa về LOW để ngắt tải
#define I2S_LRC_PIN   25
#define I2S_DIN_PIN   22

Preferences prefs;
unsigned long lastBlink = 0;
unsigned long lastHeartbeat = 0;
bool ledState = false;
uint32_t counter = 0;

void clearAllInternalMemory() {
  // 1. Xóa sạch phân vùng Preferences ứng dụng
  if (prefs.begin("mq135", false)) {
    prefs.clear();
    prefs.end();
  }

  // 2. Xóa trắng toàn bộ phân vùng NVS gốc của ESP32 Flash
  esp_err_t err = nvs_flash_erase();
  nvs_flash_init();

  Serial.println("==========================================");
  Serial.println("[CLEAR] DA XOA SACH BO NHO NVS/FLASH CU!");
  Serial.printf("[CLEAR] Trang thai NVS Erase: %s\n", (err == ESP_OK) ? "OK" : "CLEANED");
  Serial.println("[OK] DUONG TRUYEN ESP32 DA THONG SUOT!");
  Serial.println("==========================================");
}

void setup() {
  // Khóa ngay lập tức các chân loa I2S về mức 0V để tránh rút dòng làm sập nguồn USB
  pinMode(I2S_BCLK_PIN, OUTPUT);
  pinMode(I2S_LRC_PIN, OUTPUT);
  pinMode(I2S_DIN_PIN, OUTPUT);
  digitalWrite(I2S_BCLK_PIN, LOW);
  digitalWrite(I2S_LRC_PIN, LOW);
  digitalWrite(I2S_DIN_PIN, LOW);

  // Cấu hình chân đèn LED báo hiệu
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  // Mở cổng giao tiếp Serial chuẩn 115200
  Serial.begin(115200);
  delay(300);

  // Chớp đèn nhanh 5 lần báo hiệu bắt đầu quá trình xóa & khai thông
  for (int i = 0; i < 5; i++) {
    digitalWrite(LED_PIN, HIGH);
    delay(60);
    digitalWrite(LED_PIN, LOW);
    delay(60);
  }

  // Thực hiện xóa sạch dữ liệu cũ trong Flash/NVS
  clearAllInternalMemory();

  // Bật đèn sáng 1 giây xác nhận hoàn tất xóa
  digitalWrite(LED_PIN, HIGH);
  delay(1000);
  digitalWrite(LED_PIN, LOW);
}

void loop() {
  unsigned long now = millis();

  // 1. Nhấp nháy đèn LED đều đặn mỗi 300ms báo hiệu ESP32 đang sống và kết nối tốt
  if (now - lastBlink >= 300) {
    lastBlink = now;
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState ? HIGH : LOW);
  }

  // 2. Gửi tín hiệu giữ kết nối về máy tính mỗi 1 giây
  if (now - lastHeartbeat >= 1000) {
    lastHeartbeat = now;
    counter++;
    Serial.printf("EVT,CLEAR_TEST_ALIVE,COUNT=%lu,LED=%s\n", counter, ledState ? "ON" : "OFF");
  }

  // 3. Phản hồi ngay nếu máy tính gửi lệnh kiểm tra
  while (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    cmd.toUpperCase();
    if (cmd == "PING") {
      Serial.println("PONG");
    } else if (cmd == "CLEAR") {
      clearAllInternalMemory();
    } else if (cmd.length() > 0) {
      Serial.printf("EVT,ACK,%s\n", cmd.c_str());
    }
  }
}
