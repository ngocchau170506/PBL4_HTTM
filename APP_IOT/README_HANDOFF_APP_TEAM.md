# BÀN GIAO BẢN APP HỢP NHẤT (`APP_IOT/` — DRIVER + ADMIN + ĐIỂM & KHIẾU NẠI 48H + IOT REAL-TIME)

> **Dành cho thành viên App (`fleetguard-driver-app` / `PBL4_HTTM`) & AI Coding Agent:**
> Nhánh này (`feat/app-iot-unified`) **CHỈ chứa bản thiết kế chuẩn thư mục `APP_IOT/`** đã hợp nhất toàn bộ 2 thế mạnh:
> 1. **Điều khiển IoT thời gian thực `< 5ms`** (ESP32 #2 BLE NUS, Raspberry Pi 4 cục bộ `:8080`, Server `:5000`): Cảnh báo buồn ngủ 3 lần & nút **Tắt loa lần 3** (kiểm tra lại 2–4p), **3 chế độ giám sát hiện diện** (`AUTO` / `Tạm tắt 30p` / `Nghỉ ngủ trên xe tự bật lại ≥ 15 km/h`), Cân chỉnh MediaPipe 10s thật, Điều khiển loa ESP32 #1 (`VOL:50..100`, `VOICE:1..7`, `ALARM:ON/OFF`).
> 2. **Nghiệp vụ Tài xế chuẩn `PBL4_HTTM`** (góp ý của giảng viên): **Xem Điểm Uy Tín (`/100` theo 3 ngưỡng `> 65` / `≤ 65` / `≤ 20`)**, lịch sử chuyến đi, **Xem Video/Ảnh Vi phạm AI**, **Gửi Đơn Khiếu nại trong 48h** (4 lý do chuẩn: `FALSE_DETECTION`, `SENSOR_ERROR`, `NOT_ME`, `OTHER`), và **Admin duyệt Khiếu nại theo nguyên tắc Four-Eyes Check** (tự động hoàn điểm khi chấp nhận).

---

## 1. Kiến trúc 2 Phân hệ trong 1 Ứng dụng (`APP_IOT/driver.html`)

### A. Phân hệ `🚗 Tài xế (Driver)` (2 tab con gọn gàng)
1. **Tab con `🎛️ Buồng lái IoT`:**
   - **Cảnh báo Buồn ngủ 3 lần (cửa sổ 2–4 phút):**
     - Thanh 3 nấc (`0/3`, `1/3`, `2/3`, `3/3 BÁO ĐỘNG!`, `KIỂM TRA LẠI 2:59`, `TÁI PHẠM — BÁO ĐỘNG LIÊN TỤC!`).
     - Nút nổi bật **`🔇 TẮT LOA CẢNH BÁO BUỒN NGỦ (LẦN 3)`** (`DISMISS_ALARM` / `DISMISS_DROWSY_ALARM`): dừng ngay còi ESP32 #1 và mở cửa sổ kiểm tra lại 2–4 phút trên Pi 4; nếu tài xế ngủ gục lại trong cửa sổ này → ESP32 #1 phát báo động liên tục mức khẩn cấp (`ALARM:CONT`).
   - **Bộ chọn 3 Chế độ Giám sát Camera (`Cam thấy người` / `Cam không thấy người`):**
     - **Chế độ 1 — `🟢 Tự động (AUTO)`** *(mặc định mỗi khi Pi bật/khởi động lại)*: Tự xác định thấy/không thấy người; mất mặt quá 15–30s tự báo App/Server (`DRIVER_ABSENT`) rồi tự chuyển sang `Tạm tắt 30p`.
     - **Chế độ 2 — `🟡 Tạm tắt 30p` (`MODE_PAUSE`)**: Tắt cảnh báo vắng mặt trong 30 phút (có đếm ngược), **nhưng nếu Pi vẫn thấy người trước camera thì mọi cảnh báo vi phạm vẫn kêu bình thường**.
     - **Chế độ 3 — `🌙 Nghỉ / Ngủ trên xe` (`MODE_SLEEP`)**: Camera vẫn giám sát & báo hiện diện, tắt toàn bộ loa cảnh báo để tài xế ngủ nghỉ/đi ăn; **tự động bật lại `AUTO` khi vận tốc GPS `≥ 15 km/h` liên tục ≥ 3s** (gửi báo cáo lên App/Server) hoặc khi Pi tắt nguồn/khởi động lại.
   - **Cân chỉnh MediaPipe 478 (10 giây thật trên Pi 4)** + Thao tác an toàn (`Tắt còi 30s`, `SOS`, `Tôi đang tỉnh`, `Nghỉ giải lao`) + GPS/Nhiệt Pi + Kết nối 3 nguồn độc lập (`BLE ESP32 #2` → `Pi Cục Bộ :8080` → `Server PC :5000`).
2. **Tab con `🏆 Điểm & Khiếu nại (48h)`:**
   - **Điểm Uy Tín (`/100`)** đồng bộ theo 3 ngưỡng nghiệp vụ của `fleetguard-driver-app` (`ScoreScreen.js`): `> 65` (Hoạt động tốt), `≤ 65` (Cảnh cáo vàng), `≤ 20` (Báo động đỏ).
   - **Lịch sử Chuyến đi & Vi phạm AI**: Xem video/ảnh bằng chứng trực tiếp trong modal, bấm **`⚖️ Khiếu nại (48h)`** chọn 1 trong 4 mã lý do chuẩn + nhập giải trình.

### B. Phân hệ `🛡️ Quản trị (Admin / Operator)`
- Đăng nhập bằng Token `admin` / `operator` (hỗ trợ cả **Pi Cục Bộ `:8080`** và **Server PC `:5000`**).
- **Cấp mã OTP 8 ký tự (5 phút)** cho tài xế ghép điện thoại.
- **Điều khiển Loa ESP32 #1 (`MAX98357A`)**: chọn âm lượng (`50%`, `65%`, `80% mặc định mới`, `90%`, `100%`), thử 7 giọng nói (`VOICE:1..7` gồm cả `VOICE:6` Điện thoại & `VOICE:7` Hút thuốc), bật/tắt báo động liên tục (`ALARM:ON` / `ALARM:OFF`).
- **Cấu hình Tham số AI & Chính sách từ xa**: Bật/tắt Hút thuốc & Điện thoại, cửa sổ buồn ngủ (`120–240s`), số lần kích hoạt (`2–5`), ngưỡng vắng mặt (`15–30s`), thời gian tạm tắt (`5–60p`), vận tốc tự bật lại (`5–40 km/h`).
- **⚖️ Duyệt Đơn Khiếu nại từ Tài xế (Four-Eyes Check)**: Xem giải trình + video bằng chứng, bấm **Hoàn điểm (`APPROVED`)** hoặc **Từ chối (`REJECTED`)** kèm phản hồi bắt buộc.
- Ghi đè chế độ xe từ xa (`Ép AUTO` / `Tạm tắt 30p` / `Nghỉ/Ngủ`), lối tắt sang Fleet Dashboard (`/`) và Nhật ký kỹ thuật.

---

## 2. Hợp đồng Giao thức BLE & HTTP (Chi tiết tại [ble_protocol_contract.json](./ble_protocol_contract.json))

| Kênh | Lệnh / Endpoint | Mục đích |
|---|---|---|
| **BLE NUS (ESP32 #2)** | `MUTE`, `DISMISS_ALARM`, `MODE_AUTO`, `MODE_PAUSE`, `MODE_SLEEP`, `SOS`, `ACK`, `REST_START`, `REST_END`, `CALIBRATE`, `STATUS` | Giao tiếp trực tiếp không cần Internet (UUID `6e400001-...`, kết thúc `\n`, ghép mảnh MTU 20 byte) |
| **HTTP Phone Actions** | `POST /api/v1/phone/action` | `MUTE_ALERT`, `DISMISS_DROWSY_ALARM`, `MODE_AUTO`, `MODE_PAUSE_30M`, `MODE_REST_OFF`, `SOS`, `ACK_AWAKE`, `REST_BREAK_START`, `REST_BREAK_END`, `CALIBRATE_LOCK` |
| **HTTP Live Status** | `GET /api/v1/phone/status?vehicle_id=XE-01` | Trạng thái AI, `drowsy_escalation`, `monitoring`, `calibration`, `esp`, `gps`, `pi` |
| **HTTP Điểm & Vi phạm** | `GET /api/v1/phone/score_summary?vehicle_id=XE-01` | Điểm uy tín `/100`, 3 ngưỡng màu, danh sách chuyến, danh sách vi phạm + cờ `can_dispute` |
| **HTTP Khiếu nại 48h** | `POST /api/v1/phone/disputes` | Tài xế gửi khiếu nại (`FALSE_DETECTION \| SENSOR_ERROR \| NOT_ME \| OTHER`) |
| **HTTP Admin Duyệt** | `GET /api/v1/disputes` · `POST /api/v1/disputes/{id}/resolve` | Admin duyệt Four-Eyes Check (`APPROVED` tự động hoàn điểm / `REJECTED`) |

---

## 3. Cách chạy kiểm thử nhanh thư mục `APP_IOT/`

```bash
# Chạy 18 bài kiểm thử tự động cho toàn bộ logic BLE, 3 chế độ, buồn ngủ 3 lần, điểm & khiếu nại (Node.js >= 18)
node APP_IOT/test_driver_ble.mjs
```

> **Gợi ý ghép với `fleetguard-driver-app` (React Native):** Team App có thể dùng trực tiếp giao diện web PWA `APP_IOT/driver.html` (mở ở `/driver` hoặc nhúng WebView), **hoặc** dùng đúng hợp đồng trong [ble_protocol_contract.json](./ble_protocol_contract.json) và các hàm thuần trong [driver_ble.js](./driver_ble.js) để mang 2 khối **Tắt loa buồn ngủ lần 3** + **3 Chế độ Giám sát Hiện diện** sang màn hình `TripScreen.js` của `fleetguard-driver-app`.
