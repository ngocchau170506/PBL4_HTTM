# BÀN GIAO HỆ THỐNG `PI_AI` CHO NHÓM APP / BLE (`APP_IOT/` — PHƯƠNG ÁN C1 PWA WEB BLUETOOTH)

> **Dành cho thành viên App & AI Coding Agent phía bạn:**
> Hãy đọc kỹ tài liệu này và [ble_protocol_contract.json](./ble_protocol_contract.json) **TRƯỚC KHI** chỉnh sửa giao diện hoặc chuyển sang app di động để đảm bảo tương thích 100% với **ESP32 #2 (`COMMS_BLE_GPS_HUB`)**, **Raspberry Pi 4 (`pi_edge/`)** và **Server AI (`server_ai/`)**.

---

## 1. Toàn cảnh hệ thống IoT đã hoàn thiện & chạy sống thực tế

```
                     ┌──(BLE NUS mở, không PIN: PI_AI_HUB_XE-01)──► [ĐIỆN THOẠI TÀI XẾ: APP_IOT/]
                     │   • Gửi: MUTE | SOS | ACK | REST_START | REST_END | STATUS
                     │   • Nhận: ALERT:... | GPS:{...} (2s) | STATUS:{...} | OK:...
                     ▼
[ESP32 #2: COMMS_BLE_GPS_HUB] ◄──(USB /dev/esp_ble @115200)──► [Raspberry Pi 4 — pi_edge/]
  • GPS cứng UART2 (GPIO16/17)                                   • Nhận EVT,BLE_ACTION,... từ ESP2
  • On-Demand Wi-Fi Survival                                     • MUTE -> gửi ngay MUTE sang ESP1 tắt loa <5ms
    (chỉ bật khi Pi tắt >=3.5s)                                  • SOS  -> gửi ngay sự cố CRITICAL lên Server
                                                                 • Camera RTSP 192.168.1.210 (800x448 @20FPS)
[ESP32 #1: AUDIO_MQ135_NODE] ◄──(USB /dev/esp_audio @115200)───► • AI Tier-1: YOLO + BlazeFace
  • Loa I2S MAX98357A (GPIO26/25/22)                              │
  • MQ-135 (GPIO34), Wi-Fi/BT TẮT                                 │ HTTP(S) Keep-Alive
                                                                  ▼
                                                       [Server of AI — server_ai/ (:5000)]
                                                         • /driver -> phục vụ trực tiếp APP_IOT/driver.html
                                                         • /api/v1/phone/pair (OTP 5 phút dùng 1 lần)
                                                         • /api/v1/phone/telemetry (GPS điện thoại dự phòng)
                                                         • /api/v1/phone/action & /api/v1/phone/notifications
```

### Trạng thái phần cứng đã nghiệm thu thật trên Pi 4 (`192.168.1.9`)
- **ESP32 #2 (`COMMS_BLE_GPS_HUB`, `FW=PI_AI_3.0`, `/dev/esp_ble -> /dev/ttyUSB1`, cổng USB `1-1.3`, `115200` baud):**
  - Đang phát sóng BLE công khai tên **`PI_AI_HUB_XE-01`** (mở không cần mã PIN theo `LCK-05 v3`).
  - `free_heap = 107,516 B` ổn định 100% suốt >18 phút, `0` brownout, `0` reset, Wi-Fi Survival tự tắt khi Pi đang sống (`survival_wifi_on = false`).
  - Khi nhận lệnh BLE từ điện thoại, ESP2 in `EVT,BLE_ACTION,<ACTION>` qua USB sang Pi 4 và trả ngay `OK:<ACTION>\n` về điện thoại.
- **ESP32 #1 (`AUDIO_MQ135_NODE`, `FW=PI_AI_3.0`, `/dev/esp_audio -> /dev/ttyUSB0`, cổng USB `1-1.4`, `115200` baud):** Loa MAX98357A đã kiểm chứng `5/5` lệnh từ xa (`PING`, `VOL:65`, `VOICE:1`, `STOP`, `MUTE`) đều `EXECUTED_OK`.

---

## 2. Hợp đồng BLE NUS & Server BẮT BUỘC GIỮ NGUYÊN (Không được sửa sai!)

| Thành phần | Giá trị chính xác | Ghi chú bắt buộc cho AI Agent bên bạn |
|---|---|---|
| Tên thiết bị BLE | `PI_AI_HUB_<vehicle_id>` (mặc định `PI_AI_HUB_XE-01`) | Filter theo `namePrefix: "PI_AI_HUB_"` hoặc Service UUID |
| Service UUID (NUS) | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` | Chữ thường theo chuẩn Web Bluetooth API |
| RX Characteristic (Phone → ESP2) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` | Ghi ASCII **bắt buộc kết thúc bằng `\n`**, xếp hàng tuần tự tránh lỗi GATT busy |
| TX Characteristic (ESP2 → Phone) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` | **QUAN TRỌNG:** Gói notify BLE bị cắt theo MTU `20 byte` → **PHẢI gom buffer tới ký tự `\n`** rồi mới `JSON.parse` (xem `BleLineFramingParser` trong `driver_ble.js`) |
| 6 lệnh được phép gửi | `MUTE`, `SOS`, `ACK`, `REST_START`, `REST_END`, `STATUS` | Không gửi lệnh hệ thống khác qua BLE |
| Chuỗi ESP2 đẩy về | `ALERT:<TYPE>\|<DETAIL>`, `GPS:{...}`, `STATUS:{...}`, `OK:<ACT>`, `ERR:...` | Xem mẫu đầy đủ trong `ble_protocol_contract.json` |

### Giới hạn Web Bluetooth (Phương án C1) cần lưu ý khi chạy thật
1. **Bắt buộc chạy trên HTTPS** (dùng `server_ai/run_tunnel.ps1` qua Cloudflare Tunnel `https://...trycloudflare.com/driver`, hoặc `http://localhost:5000/driver` trên máy tính). Nếu mở qua `http://192.168.1.7:5000/driver` trên điện thoại, Chrome Android sẽ **chặn Web Bluetooth** (UI đã tự hiện banner cảnh báo rõ ràng).
2. **Hệ điều hành:** Chạy mượt trên **Android Chrome / Edge**. **Safari trên iPhone không hỗ trợ Web Bluetooth** (trên iOS có thể mở bằng trình duyệt *Bluefy* hoặc dùng bộ contract này để bọc lại thành app Flutter/React Native sau này).
3. **Bảo mật BLE:** Hiện ESP2 mở không PIN theo `LCK-05 v3` để tiện demo. Nếu đưa ra thương mại, nhóm App + Firmware cần bổ sung mã PIN phiên ở tầng ứng dụng.

---

## 3. Bên trong thư mục `APP_IOT/` có gì & Cách chạy/test

| File | Vai trò | Bạn có thể sửa? |
|---|---|---|
| `driver.html` | Giao diện buồng lái tài xế (nút bấm lớn >=68px: `TẮT CÒI 30s`, `SOS`, `TÔI ĐANG TỈNH`, `NGHỈ`, thẻ GPS trực tiếp, ô ghép OTP Server, log BLE) | **CÓ** (tùy biến giao diện, ngôn ngữ, bố cục) |
| `driver.css` | Giao diện tối tương phản cao, 0 phụ thuộc CDN ngoài (chạy offline/LAN 100%) | **CÓ** |
| `driver_ble.js` | Bộ xử lý Web Bluetooth NUS (`BleLineFramingParser` ghép gói 20-byte MTU, `DriverBleClient` tự nối lại sau 2.5s + khóa hàng đợi ghi GATT, đọc giọng nói TTS tiếng Việt + rung, gọi `/api/v1/phone/pair` & `/api/v1/phone/action`) | **CÓ** (giữ nguyên UUID & format lệnh) |
| `manifest.json` | Cấu hình PWA cho phép "Thêm vào màn hình chính" trên Android | **CÓ** |
| `ble_protocol_contract.json` | Hợp đồng máy đọc được (JSON) cho AI Agent bên bạn | **CHỈ ĐỌC** |
| `test_driver_ble.mjs` | Bộ 5 bài test Node.js (kiểm chứng UUID, ghép mảnh 20-byte MTU, JSON lỗi, chống chèn lệnh lạ, chống tràn buffer) | **CÓ** (chạy trước khi gửi trả) |

```bash
# Chạy kiểm thử logic BLE ngay trên PC (yêu cầu Node.js >= 18)
node APP_IOT/test_driver_ble.mjs

# Xem thử giao diện trên Server of AI:
#   - Trên PC: http://127.0.0.1:5000/driver
#   - Trên điện thoại Android (Web Bluetooth thật): https://<cloudflare-tunnel-url>/driver
```

---

## 4. Kiểm tra thay đổi của `server_ai/` so với bản Server trước đây

So với bản Server trước đây (mốc `d50ea70` / `2a97e17` ngày 22/11/2026):
- **Tất cả endpoint App/Phone cũ giữ nguyên 100% không đổi đường dẫn:**
  - `POST /api/v1/phone/pair_otp` (alias `/api/v1/phone/pair/otp` cũng được hỗ trợ — operator tạo OTP 5 phút)
  - `POST /api/v1/phone/pair` (điện thoại đổi OTP lấy `driver_phone_token` — không cần token đăng nhập trước, chống brute-force bằng rate-limit & atomic `BEGIN IMMEDIATE`)
  - `POST /api/v1/phone/telemetry` (điện thoại gửi `phone_gps` dự phòng & `relay_packet`)
  - `POST /api/v1/phone/action` (`MUTE_ALERT`, `SOS`, `ACK_AWAKE`, `REST_BREAK_START`, `REST_BREAK_END`)
  - `GET /api/v1/phone/notifications?vehicle_id=XE-01`
- **Các thay đổi mới trên `server_ai/` phía App cần biết:**
  1. **Mới gắn sẵn `/driver` và `/app_iot/*`** trên `server_ai` để phục vụ trực tiếp thư mục `APP_IOT/` (trả `307 -> /app_iot/driver.html`).
  2. **Chuẩn hóa mã lỗi JSON:** Nếu App gửi JSON sai cú pháp hoặc không phải object `{}` → Server trả `HTTP 400` rõ ràng (không còn `500`); payload > `2 MB` trả `HTTP 413`.
  3. **Sửa hiển thị `bt_phone_connected`:** Server chỉ báo `bt_phone_connected=1` khi có điện thoại thật nối BLE (`phone_connected: true`), không còn báo ảo khi chỉ cắm USB ESP32 #2.
  4. **Miễn phạt điểm cho `SOS` & `CO2`:** Bấm `SOS` từ App/BLE tạo ngay sự cố `CRITICAL` `SOS_PRESSED` nhưng **KHÔNG trừ điểm an toàn của tài xế**; cảnh báo `AUTO_CO2_CRIT/WARN` cũng chỉ cảnh báo, không trừ điểm và không tính vào số vi phạm tài xế.
  5. **Khử trùng lặp MUTE 5 giây:** Bấm `MUTE` từ BLE (đi qua Pi) rồi gọi thêm API không bị đếm đúp 2 lần tắt còi trong vòng 5 giây.
  6. **Tích hợp tùy chọn `pbl4_bridge` (Phương án A2):** Tắt mặc định (`PBL4_BACKEND_URL=""`), sẵn sàng chuyển tiếp sang Backend Node.js `PBL4_HTTM` (:3000) khi nhóm bật cấu hình.
