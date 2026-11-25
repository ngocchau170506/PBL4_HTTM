# BÀN GIAO HỆ THỐNG `PI_AI` CHO NHÓM AI (`CNN_IOT/` — TẦNG 2 PHƯƠNG ÁN B1)

> **Dành cho thành viên AI & AI Coding Agent phía bạn:**
> Hãy đọc kỹ tài liệu này và [contracts_snapshot.json](./contracts_snapshot.json) **TRƯỚC KHI** chỉnh sửa code để hiểu toàn bộ hệ thống IoT trên Raspberry Pi 4 + 2 ESP32 + Server AI đã hoàn thiện tới đâu, **không làm đứt các kết nối đã nghiệm thu thực tế**.

---

## 1. Toàn cảnh hệ thống IoT đã hoàn thiện & chạy sống thực tế (Pi 4 + 2 ESP32 + Camera + Server)

```
[Camera IP RTSP 192.168.1.210:554] ──(LAN eth0 192.168.1.209/30)──┐
                                                                  ▼
[ESP32 #1: AUDIO_MQ135_NODE] ◄──(USB /dev/esp_audio @115200)──► [Raspberry Pi 4 — pi_edge/]
  • Loa I2S MAX98357A (<5ms)                                     • Tier-1 AI: YOLO best.pt (320) + BlazeFace
  • Cảm biến khí MQ-135 (GPIO34)                                 • SensorFusion + BlindHold <= 0.75s
  • Wi-Fi/BT/GPS = TẮT (0 brownout)                              • Gom burst 10-20 ảnh JPEG 800x448 (~10s)
                                                                 • SQLite Spool 512MB (chống mất gói khi rớt mạng)
[ESP32 #2: COMMS_BLE_GPS_HUB] ◄──(USB /dev/esp_ble @115200)────► • systemd: driver-monitor.service (active)
  • BLE NUS mở (PI_AI_HUB_XE-01)                                  │
  • GPS cứng UART2 (GPIO16/17)                                    │ HTTP(S) Keep-Alive (LAN hoặc Cloudflare Tunnel)
  • On-Demand Wi-Fi Survival (khi Pi tắt >=3.5s)                  ▼
                                                       [Server of AI — server_ai/ (:5000)]
                                                         • Nhận POST /api/v1/telemetry (1.5s/lần)
                                                         • Nhận POST /api/v1/violation_burst (multipart JPEG)
                                                         • Đẩy vào DeepVerifierEngine (2 luồng worker)
                                                           └──► GỌI MODULE CỦA BẠN: CNN_IOT/tier2_b1_verifier.py
                                                         • Nếu CONFIRMED -> ghép MP4/WebM trên Server + tính điểm AT
                                                         • Nếu REJECTED  -> lưu mẫu khó vào data/hard_negatives/
                                                         • Fleet Dashboard (:5000) + SSE + Optional PBL4 Bridge (:3000)
```

### Số liệu đã nghiệm thu chạy thật trên phần cứng Pi 4 (`192.168.1.9`) ↔ Server PC (`192.168.1.7:5000`)
- **Camera RTSP (`192.168.1.210:554`):** `800×448` (16:9, đã xoay `180°` sẵn trên Pi trước khi gửi ảnh!), tốc độ ổn định `20.0 FPS`.
- **Pi 4 Tier-1 Edge AI:** `ai_fps = 4.0 FPS`, `loop_fps = 25.0 FPS`, `RAM = 466 MB`, `throttled = 0x0`, ổn định liên tục >18 phút.
- **2 bo ESP32 (`FW=PI_AI_3.0`, đồng nhất `115200` baud):** Đã khóa cứng cổng bằng udev (`1-1.4 → /dev/esp_audio`, `1-1.3 → /dev/esp_ble`) + tự nhận diện `WHOAMI` nếu cắm đảo cổng; `5/5` lệnh từ xa (`PING`, `VOL:65`, `VOICE:1`, `STOP`, `MUTE`) đã `EXECUTED_OK`.

---

## 2. Những hợp đồng BẤT BIẾN (AI Agent bên bạn TUYỆT ĐỐI KHÔNG ĐƯỢC PHÁ)

1. **KHÔNG chuyển model nặng hay ghép MP4 xuống Raspberry Pi 4 (`LCK-02`):** Pi 4 chỉ làm phản xạ `<5ms` và gửi 10–20 ảnh JPEG thô. Mọi AI Tầng 2 chạy 100% trên `server_ai` (PC/Cloud).
2. **KHÔNG đổi chữ ký hàm đầu vào/đầu ra:**
   - Đầu vào của `verify_burst(frame_paths: List[Path], meta: Dict[str, Any])`:
     - `frame_paths`: danh sách file `.jpg` `800×448` (có thể rỗng `[]` đối với `SOS`, `AUTO_CO2_CRIT`, `AUTO_CO2_WARN`, `ESP_BROWNOUT`!).
     - `meta`: JSON chứa `alert_type`, `duration_seconds`, `confidence`, `verification_mode`, `vehicle_id`, `gps`, `co2_ppm`.
   - Đầu ra PHẢI trả về object có đủ 5 trường: `status`, `confidence` (`0.0..1.0`), `model_version`, `latency_ms`, `detail`.
   - `status` **CHỈ ĐƯỢC** nhận đúng 2 giá trị: `"SERVER_CNN_CONFIRMED"` hoặc `"SERVER_CNN_REJECTED"`.
3. **BẮT BUỘC Thread-Safe (`threading.Lock`):** `DeepVerifierEngine` trên Server chạy **2 worker thread song song** (`DEEP_VERIFIER_WORKERS=2`). Mọi lần gọi `cv2.dnn.Net`, `YOLO`, `MediaPipe` PHẢI nằm trong `Lock`, nếu không sẽ gây SIGSEGV!
4. **Chính sách miễn phạt điểm (đã chốt bởi MAIN):**
   - `SOS`, `ESP_BROWNOUT`, `ESP_HUB_BROWNOUT`, `AUTO_CO2_CRIT`, `AUTO_CO2_WARN`, `CO2_CRIT`, `CO2_WARN` là cảnh báo an toàn/khí cabin — **KHÔNG trừ điểm an toàn tài xế và KHÔNG tăng số lần vi phạm của tài xế**. Module của bạn nên passthrough `SERVER_CNN_CONFIRMED` với nhãn non-penalty như đã cài sẵn trong `tier2_b1_verifier.py`.
5. **Quy ước `model_version` để Dashboard hiển thị trung thực:**
   - B1 Pretrained HD: bắt đầu bằng `"b1-pretrained:"` → `/api/v1/health` báo `deep_verifier_mode = "PRETRAINED_B1"`.
   - Custom ONNX CNN: bắt đầu bằng `"onnx-cnn:"` → `/api/v1/health` báo `deep_verifier_mode = "CNN_ONNX"`.

---

## 3. Bên trong thư mục `CNN_IOT/` có gì & Cách chỉnh sửa

| File | Vai trò | Bạn có thể sửa? |
|---|---|---|
| `tier2_b1_verifier.py` | Bộ hậu kiểm **Phương án B1** hoàn chỉnh: soi riêng từng `alert_type` bằng **MediaPipe FaceLandmarker HD** (tính EAR/MAR/Yaw theo chuỗi frame) cho `DROWSY/YAWNING/DISTRACTED` + **YOLO HD (`imgsz=640`)** cho `PHONE/SMOKING` + khe cắm `onnx_scorer` tùy chọn | **CÓ** (giữ nguyên chữ ký `verify_burst` và thread-safety) |
| `verifier_config.json` | Toàn bộ ngưỡng (`drowsy_ear_max`, `yawning_mar_min`, `distracted_yaw_ratio_min`, `phone_conf_min`, đường dẫn weights) | **CÓ** (chỉnh ngưỡng tại đây, không hard-code) |
| `contracts_snapshot.json` | Hợp đồng JSON chuẩn giữa Pi Edge ↔ Server AI ↔ `CNN_IOT` | **CHỈ ĐỌC** (đối chiếu để không lệch schema) |
| `test_b1_verifier.py` | Bộ 5 bài test tự chạy (kể cả test ảnh đen, JPEG hỏng, chớp mắt `<0.35s`, CO2/SOS không phạt, 4 luồng đồng thời) | **CÓ** (chạy kiểm tra trước khi gửi trả) |

### Cách kích hoạt & kiểm thử ngay trên PC
```bash
# 1. Chạy bộ test độc lập của CNN_IOT (bắt buộc PASS 5/5 trước khi gửi lại)
python CNN_IOT/test_b1_verifier.py

# 2. Chạy Server of AI ở chế độ B1 (dùng CNN_IOT/tier2_b1_verifier.py)
set TIER2_VERIFIER_MODE=b1
python -m uvicorn server_ai.api_server:app --host 0.0.0.0 --port 5000
```
- Nếu bạn muốn dùng model YOLO mạnh hơn (ví dụ `yolov8m.pt` hoặc model tự train cho `phone`/`smoking`): đặt file vào `CNN_IOT/models/server_yolo.pt` (ưu tiên tải trước `pi_edge/models/best.pt`).
- Nếu bạn muốn gắn thêm/thay thế bằng model `.onnx` tự train: truyền `onnx_scorer` hoặc bật `use_custom_onnx: true` trong `verifier_config.json` (khuyến nghị crop vùng mặt + multi-class, **tránh** nhồi ảnh `800×448` nguyên khung vào CNN nhị phân 2-class chung cho mọi lỗi).

---

## 4. Kiểm tra thay đổi của `server_ai/` so với bản Server trước đây

So với bản Server trước đây (mốc `d50ea70` / `2a97e17` ngày 22/11/2026):
- **KHÔNG xóa, KHÔNG đổi tên** bất kỳ REST API endpoint nào cũ (`/api/v1/telemetry`, `/api/v1/violation_burst`, `/api/v1/violations`, `/api/v1/server/command`, `/api/v1/phone/*`, `/api/v1/stream/events` giữ nguyên 100%).
- **Các thay đổi & bổ sung mới trên `server_ai/` cần nắm rõ:**
  1. **Thêm `HEAD /evidence/{rel_path:path}`** để trình duyệt seek video MP4/WebM (HTTP 206) ổn định.
  2. **Thêm route PWA tài xế:** `/driver` (chuyển hướng tới `/app_iot/driver.html`) khi có thư mục `APP_IOT/`.
  3. **Thêm khe cắm `TIER2_VERIFIER_MODE=b1`** trong `deep_verifier.py` (mặc định `auto` giữ tương thích ngược 100%).
  4. **Tích hợp `server_ai/server_core/pbl4_bridge.py` (Phương án A2):** Tắt mặc định (`PBL4_BACKEND_URL=""`), khi bật sẽ tự đẩy bất đồng bộ (HTTP Keep-Alive + retry 5xx) sang Backend Node.js `PBL4_HTTM` (:3000) và không gửi `AUTO_CO2_*` vào vi phạm phạt trừ khi bật `PBL4_FORWARD_NON_PENALTY=1`.
  5. **Siết chặt giới hạn JSON & chống lỗi 500:** Mọi JSON sai định dạng hoặc không phải object (`[]`) trả `HTTP 400` thay vì `500`; payload vượt quá `2 MB` (JSON) / `15 MB` (upload) trả `HTTP 413` kể cả chunked transfer.
  6. **Hỗ trợ burst `0` frame tại `/api/v1/violation_burst`:** Cho phép sự kiện `SOS`, `AUTO_CO2_CRIT/WARN`, `ESP_BROWNOUT` gửi qua multipart/urlencoded/JSON mà không bị HTTP 400.
  7. **Miễn trừ điểm an toàn (`NON_PENALTY_ALERT_TYPES`):** `SOS`, `ESP_BROWNOUT`, `ESP_HUB_BROWNOUT`, `AUTO_CO2_CRIT`, `AUTO_CO2_WARN`, `CO2_CRIT`, `CO2_WARN` không bị trừ điểm ở cả luồng AI tự động, duyệt tay `HUMAN_CONFIRMED`, và bảng thống kê tài xế `/api/v1/drivers/summary`.
  8. **Chống lỗi đa luồng & trùng dữ liệu:** Khóa `_net_lock` trên `OnnxCnnVerifier`, transaction `BEGIN IMMEDIATE` trên SQLite (OTP, lệnh, duyệt vi phạm, upsert xe), khử trùng lặp MUTE trong cửa sổ `5.0s`, và tách ngưỡng nhiệt cảnh báo Pi `70°C` + giải mã bitmask `throttled & 0xF` (chỉ báo lỗi đang xảy ra, không spam từ lịch sử sticky `0x50000`).
  9. **Trạng thái sinh tồn ESP32 #2:** `/api/v1/esp/bridge_telemetry` đặt `status="SURVIVAL_ESP2"`, tạo sự cố `PI_OFFLINE_SURVIVAL_MODE`, và chỉ bật `bt_phone_connected=1` khi có điện thoại thật kết nối BLE (không báo ảo khi chỉ cắm USB hub).
