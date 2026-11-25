# PROJECT CONTRACT — HỆ THỐNG `PI_AI` (HỢP ĐỒNG BẤT BIẾN ĐÃ ĐƯỢC MAIN BIỂU QUYẾT)

> **Chỉ thị bắt buộc cho mọi AI Agent & Kỹ sư:** Đọc file này TRƯỚC khi sửa bất kỳ dòng code nào trong `e:\AI\PI_AI`. Mọi thay đổi phá vỡ các điều khoản `LCK-01..LCK-07` bên dưới đều **CẤM tự thực hiện** nếu chưa có phê duyệt mới của MAIN.

---

## 1. Bảy Quyết Định Đã Khóa (`LCK-01` – `LCK-07`)

| Mã | Quyết định đã khóa | Quy tắc cưỡng chế |
|---|---|---|
| **LCK-01** | **Kho gốc bất khả xâm phạm:** `e:\AI\IOTv5` là Golden Baseline chỉ đọc 100%. Mọi xây dựng/vá/tối ưu chỉ diễn ra trong `e:\AI\PI_AI` (có Git cục bộ). | Không ghi/xóa bất kỳ file nào trong `e:\AI\IOTv5`. |
| **LCK-02** | **Kiến trúc 2 Tầng AI:** **Pi 4 (`pi_edge/`)** = Tầng 1 Edge Safety Reflex (YOLO + BlazeFace 1-pass + `SensorFusion`, phát loa ESP32 `<5ms` cả khi offline, gom chuỗi **10–20 ảnh JPEG keyframe sạch** — KHÔNG chạy software MP4 encode hay CNN nặng trên Pi). **`Server of AI` (`server_ai/`)** = Tầng 2 Deep AI Brain (chạy `DeepVerifier` CNN chuyên sâu bất đồng bộ xác nhận/bác bỏ `EDGE_TRIGGERED → SERVER_CNN_CONFIRMED / SERVER_CNN_REJECTED`, ghép MP4 trên Server, lưu mẫu khó *hard-negative*, Fleet Dashboard, Analytics). | Không nhét CNN chuyên sâu hoặc `cv2.VideoWriter` nén MP4 vào vòng lặp Pi ở `RUN_MODE=server`. |
| **LCK-03** | **Monorepo 4 nhánh tách bạch:** `contracts/`, `server_ai/`, `pi_edge/`, `firmware/`. | Mọi thay đổi trường JSON phải cập nhật `contracts/pi_server_schema.json` và vượt qua `contracts/test_cross_contract.py`. |
| **LCK-04 (v2 — Phê duyệt 21/11/2026)** | **Một nguồn cấu hình `RUN_MODE=local\|server`:** Đọc từ `pi.env`; gỡ ép cứng `WEB_PORT=0` khỏi `driver-monitor.service`. `local` = Dashboard đầy đủ + `/stream` + **0 kết nối ra Server**; `server` = Web siêu nhẹ chỉ mở `/api/v1/health`, `/api/esp_bridge` (**tắt `/stream` live mặc định `ENABLE_LIVE_STREAM=0`** để giảm tải CPU/băng thông Pi 4 qua 4G, chỉ gửi clip vi phạm & sự kiện). | Test network egress bắt buộc chứng minh `local` = 0 socket ra ngoài; mặc định `server` mode không nén MJPEG live. |
| **LCK-05 (v3 — Phê duyệt 22/11/2026)** | **Tuyến truyền tải chính = Pi HTTP(S) Keep-Alive (LAN hoặc Cloudflare Tunnel) & Tách đôi 2 ESP32 đồng nhất UART `115200`:** Pi gửi Telemetry JSON & Violation 10s JPEG Burst (`multipart/form-data`, không nhồi Base64 vào HTTP Header) qua **1 SQLite Spool duy nhất có trần 512 MB** + `Idempotency-Key` (tự reset hạn retry khi reboot). **Phân tách phần cứng 2 ESP32:** (1) **ESP32 #1 (`AUDIO_MQ135_NODE`, `115200`)** = Pure Cabin Safety Node: Loa I2S `MAX98357A` (`GPIO26/25/22`, `<5ms`, `VOL:10..100`, phần mềm `MUTE`, **bỏ nút MUTE vật lý**) + `MQ-135` (`GPIO34`, warm-up 25s), **tắt hẳn radio Wi-Fi/BT & GPS** để tối ưu điện năng và triệt tiêu brownout; (2) **ESP32 #2 (`COMMS_BLE_GPS_HUB`, `115200`)** = Tầng Kết nối & Định vị: **BLE NUS mở (không PIN)** kết nối App điện thoại + **GPS thật UART2 (`GPIO16/17 @ 9600`)** + **On-Demand Wi-Fi Survival Bridge** (`/api/v1/esp/bridge_telemetry`, chỉ bật khi Pi ngưng `>= 3.5s` và bắn tọa độ GPS sống từ UART2 + NVS Flash). | Không gộp Wi-Fi/BT/GPS vào ESP32 #1; không đánh dấu gửi thành công khi chưa nhận ACK đúng tầng/sequence; phân biệt 2 bo bằng `udev KERNELS` + `WHOAMI`. |
| **LCK-06** | **Ngữ nghĩa cảnh báo & Bảo mật Strict:** Tách `alert_state` khỏi `verification_mode`; rút tổng vùng giữ mù `<= 0.8s`; tách riêng 4 tầng FPS (`camera_fps`, `loop_fps`, `ai_fps`, `stream_fps`) + `alert_latency_p95_ms`. **RBAC `strict` từ lúc boot** (thiếu token = từ chối khởi động; chế độ `lab` chỉ bind `127.0.0.1`; bỏ `LOCALHOST_LEGACY_EXEMPT` chống tunnel bypass; tách `audit_permissions.jsonl` khỏi thư mục tải xuống; khóa OTP `/api/v1/phone/pair`). | Không cho phép `OPEN_LAB_NO_TOKENS` ở production. |
| **LCK-07** | **Kiểm thử trên thiết bị thật (không máy ảo) & Gate Runner Fail-Closed:** Chạy trên PC chỉ mang nhãn `PC_PREFLIGHT`; chỉ gắn `REAL_HARDWARE_VERIFIED` sau khi có log thực địa trên PC + Pi 4 + Camera RTSP + ESP32 #1 (và Pha 6: ESP32 #2 + Điện thoại). | Gate Runner chặn cứng `0 tests`, thiếu evidence, `recovered=False`, hoặc code thay đổi sau khi test. |

---

## 2. Quy tắc bảo vệ phần cứng & mạng Pi 4 (Bất biến)

- **TUYỆT ĐỐI KHÔNG** chạy `nmcli`, sửa Wi-Fi, VLAN, `netplan-eth0` (`192.168.1.209/32` + route `192.168.1.210/32` + IP cứu hộ `192.168.137.7/24`) từ script Python hay Bash.
- **Bảo vệ ESP32:** `MQ-135` 5V qua cầu phân áp `10k/20k + 100nF` vào `GPIO34`; loa `MAX98357A` cấp nguồn Buck `5V/3A` riêng chung GND; không gộp Bluetooth/BLE vào ESP32 #1.
- **Nhãn bằng chứng bắt buộc trong mọi báo cáo:** `[RUN]` · `[SRC]` · `[DOC]` · `[INFER]` · `[NOT RUN]` · `[BLOCKED]`.

---

## 3. Các Quyết Định Bổ Sung Đã Được MAIN Phê Duyệt (24/11/2026)

1. **Ngưỡng nhiệt Pi 4 (`THERMAL_CRIT_C = 70.0°C`):** Khóa chính thức mức bảo vệ chủ động `70°C` (L3) thay cho ngưỡng cũ `80°C`, đi kèm yêu cầu lắp tản nhiệt chủ động (heatsink + quạt) trên xe.
2. **BLE NUS mở trên ESP32 #2 (`LCK-05 v3`):** Giữ chế độ mở (không PIN bắt buộc) để bảo đảm kết nối thông suốt `App ↔ ESP32 #2 ↔ Server`; ưu tiên sửa trọn vẹn MTU framing, OTP pairing và độ bền kết nối.
3. **Chính sách mất mặt (`ENABLE_NO_FACE_TAMPER_ALERT = 0`):** Giữ nguyên chế độ tắt hoàn toàn để tránh làm phiền tài xế (rủi ro vận hành đã được MAIN xem xét và chấp thuận).
4. **Hạ tầng Tunnel & App di động:** Tên miền Named Tunnel & ứng dụng di động chính thức thuộc phạm vi nhóm đối tác App (`APP_IOT/` là gói bàn giao tham chiếu chuẩn giao thức).
5. **AI Tầng 2 CNN (`LCK-02` điều chỉnh phạm vi):** Ngưng phát triển/huấn luyện thêm model CNN `.onnx` mới ở hiện tại; giữ sạch gói bàn giao `CNN_IOT/` & giao diện cắm Tầng 2 để nhóm có thể kích hoạt về sau nếu cần, tập trung độ chính xác thực chiến vào **AI Tầng 1 (`pi_edge/`)**.
