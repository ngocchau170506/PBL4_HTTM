# HƯỚNG DẪN TÍCH HỢP & GỘP SERVER: `PI_AI` (Edge IoT) ↔ `PBL4_HTTM` (Node.js / Prisma / React / Mobile App)

> **Dành cho:** Thành viên quản lý Server (`PBL4_HTTM`: Backend Node.js + SQL Prisma + Frontend React + Mobile App)  
> **Người gửi:** Thành viên phụ trách IoT & AI Edge (`PI_AI`: Raspberry Pi 4 + Camera + ESP32 #1/#2)  
> **Cơ sở đối chiếu:** Đã kiểm tra trực tiếp nhánh `main` của `github.com/ngocchau170506/PBL4_HTTM` (commit `07895ac`) và `e:\AI\PI_AI` (`source_hash 720e03f71191f03c`).

---

## 1. Kiến trúc gộp 2 Server thành 1 trên cùng máy Server (Khuyên dùng)

Do **Raspberry Pi 4 không tự nén video `.mp4`** (tránh quá nhiệt & giảm FPS suy diễn AI) mà gửi chuỗi ảnh JPEG nhẹ (10–40 ảnh/10 giây), trong khi **`PBL4_HTTM` Backend (`:3000`)** đã hoàn thiện SQL Prisma, Socket.IO, Web React, Mobile App và nhận file video `.mp4` tại `POST /api/v1/edge/violations`, cách gộp **nhanh nhất và không phải sửa Database/Frontend** là chạy `server_ai` làm **AI Media Gateway** ngay cạnh Backend Node.js trên cùng máy Server:

```text
[Raspberry Pi 4 + Camera + ESP32]
        │  4G / Hotspot / Wi-Fi phòng máy
        │  HTTPS (Cloudflare Tunnel — không cần mở port router, không cài VPN lên Pi)
        ▼
[server_ai — Python FastAPI :5000]  (Chạy trên chính máy Server của nhóm)
        │  1. Nhận Telemetry JSON + chuỗi ảnh JPEG 10s (multipart/form-data) từ Pi 4
        │  2. Hậu kiểm Tầng 2 (lọc báo giả ảnh tối / xung nhiễu)
        │  3. Ghép chuỗi ảnh JPEG thành file video .mp4 chuẩn H.264 (chạy trên PC khỏe)
        │  4. Chuyển tiếp (Bridge) sang Backend Node.js nội bộ (http://127.0.0.1:3000)
        ▼
[PBL4_HTTM Backend — Node.js Express :3000 + Prisma SQL + Socket.IO]
        │
        ├──► Web Dashboard (React :5173 — Bản đồ Leaflet, Video vi phạm, Điểm uy tín)
        └──► Mobile App Tài xế (fleetguard-driver-app — Socket.IO real-time)
```

> **Lợi ích:**
> - **0 sửa đổi** vào schema Prisma, Frontend React hay Mobile App.
> - Thay thế luôn link video mẫu `ForBiggerBlazes.mp4` trong `iot.service.js` bằng **video `.mp4` thật quay từ Camera + Pi 4**.
> - Nếu bạn quản lý server muốn **gộp trực tiếp thành 1 tiến trình Node.js duy nhất** (không chạy Python `:5000`), xem mục 4 bên dưới.

---

## 2. Bảng đối chiếu hợp đồng API giữa 2 hệ thống (Bắt buộc khớp)

### 2.1. Endpoints & Xác thực

| Chức năng | Phía `PI_AI` gửi ra (`pi_edge`) | Phía `PBL4_HTTM` đang nhận (`backend :3000`) | Cách ánh xạ khi gộp |
|---|---|---|---|
| **Xác thực thiết bị** | Header `Authorization: Bearer <TOKEN>` và `X-Auth-Token: <TOKEN>` | `requireDeviceAuth` nhận `x-device-token` hoặc `Authorization: Bearer <IOT_DEVICE_SECRET>` | **Khớp sẵn!** Đặt `IOT_DEVICE_SECRET` trong `backend/.env` trùng với `REMOTE_AUTH_TOKEN` (role `device`) của Pi. |
| **Telemetry GPS / CO2** | `POST /api/v1/telemetry` (mỗi 1.5s) | `POST /api/v1/edge/telemetry` (hoặc `/api/v1/iot/telemetry`) | Bridge chuyển tiếp mỗi 3–5s sang `/api/v1/edge/telemetry`. |
| **Vi phạm + Bằng chứng** | `POST /api/v1/violation_burst` (`multipart/form-data`: `meta` JSON + `frames` ảnh `.jpg` + `Idempotency-Key`) | `POST /api/v1/edge/violations` (`multipart/form-data`: file `video` `.mp4/.webm` + `payload` JSON + `Idempotency-Key`) | `server_ai` ghép `frames` → file `.mp4` rồi POST dạng `multipart` field `video` + `payload` sang `/api/v1/edge/violations`. |
| **Sức khỏe Pi / Camera** | Gộp trong `/api/v1/telemetry` (`pi.cpu_temp_c`, `camera.status`) | `POST /api/v1/edge/heartbeat` (`device_id`, `cpu_temp_c`, `free_storage_mb`, `trang_thai_ket_noi`) | Bridge trích từ telemetry và gọi `/api/v1/edge/heartbeat` mỗi 30–60s. Khi `camera.status == "OFFLINE"` gửi `trang_thai_ket_noi: "ERROR_CAMERA"`. |
| **Nút MUTE / Tắt còi** | `POST /api/v1/events/alert_dismissed` | Chưa có endpoint riêng trong `iot.router.js` | Lưu ở `server_ai` (hoặc thêm bảng/route log sự kiện trên Node.js nếu cần chấm điểm phản xạ). |

### 2.2. Ánh xạ tên trường JSON (Telemetry)

| Trường của `PI_AI` (`pi_edge`) | Trường của `PBL4_HTTM` (`iot.service.js`) | Ghi chú |
|---|---|---|
| `vehicle_id` (vd `"XE-01"`) | `device_id` / `maThietBi` (vd `"IOT-DN-01"` hoặc ID số `1`) | **Cần chốt mã thiết bị chung** trong bảng `iot_devices` (khuyến nghị tạo bản ghi `maThietBi = "XE-01"` hoặc đổi `VEHICLE_ID` của Pi khớp với `maThietBi` trong `seed.js`). |
| `gps.lat` (khi `gps.is_valid == true`) | `vi_do_gps` (hoặc `viDo`) | Không gửi `0.0` nếu `is_valid == false` để tránh bản đồ nhảy ra giữa biển. |
| `gps.lon` (khi `gps.is_valid == true`) | `kinh_do_gps` (hoặc `kinhDo`) | Như trên. |
| `gps.speed_kmh` | `toc_do_kmh` (hoặc `tocDo`) | Đơn vị km/h. |
| `mq135.co2_ppm` | `co2_ppm` (hoặc `co2Level`) | Đơn vị ppm. |
| `pi.cpu_temp_c` | `cpu_temp_c` (tại `/api/v1/edge/heartbeat`) | Độ C. |

### 2.3. Ánh xạ mã loại vi phạm (`alert_type` → `ma_loai`) — **RẤT QUAN TRỌNG**

> ⚠️ Trong `backend/src/common/constants/violationTypes.js`, hàm `getViolationTypeByCode()` hiện **chưa nhận diện** các chuỗi `"DROWSY"`, `"DISTRACTED"`, `"SMOKING"` của `PI_AI`. Cần ánh xạ như bảng dưới (hoặc bổ sung thêm từ khóa vào `violationTypes.js`):

| Mã từ `PI_AI` (`pi_edge`) | Mã chuẩn `PBL4_HTTM` (`ma_loai`) | Ghi chú |
|---|---|---|
| `DROWSY` | `MICROSLEEP` | Có clip 10s (`-10đ`) |
| `YAWNING` | `YAWNING_FATIGUE` | Có clip 10s (`-5đ`) |
| `PHONE` | `PHONE_DISTRACTION` | Có clip 10s (`-5đ`) |
| `DISTRACTED` | `PHONE_DISTRACTION` *(hoặc thêm mã mới)* | `violationTypes.js` dòng 63 có ghi `looking_away` thuộc nhóm này |
| `SMOKING` | *(Cần thống nhất: thêm loại `SMOKING` vào bảng `violation_types` hay gộp vào `PHONE_DISTRACTION`)* | Nếu chưa có trong DB Prisma thì cần seed thêm hoặc ánh xạ |
| `AUTO_CO2_WARN` / `AUTO_CO2_CRIT` | `HIGH_CO2` | Ngưỡng > 1200 ppm |
| Camera offline khi xe chạy | `DEVICE_TAMPERING` | Gửi qua `/api/v1/edge/heartbeat` với `trang_thai_ket_noi: "ERROR_CAMERA"` |

### 2.4. Lưu ý nhỏ cần chỉnh ở `PBL4_HTTM` Backend (để ảnh/video hiển thị đúng qua mạng 4G / Cloudflare Tunnel)
1. **`BACKEND_URL` trong `backend/.env`:**
   - Trong `iot.controller.js:61`, khi lưu file video upload lên, Node.js ghép đường dẫn là `${process.env.BACKEND_URL}/uploads/videos/${req.file.filename}`.
   - Khi chạy qua Cloudflare Tunnel, hãy đặt `BACKEND_URL=https://<domain-tunnel-cua-ban>` (không để `http://localhost:3000`) thì Web Dashboard và Mobile App ở xa mới xem được video!
2. **`CORS_ORIGINS` trong `backend/.env`:**
   - Thêm URL `https://<domain-tunnel-cua-ban>` vào `CORS_ORIGINS` để trình duyệt không bị chặn CORS.
3. **`IOT_DEVICE_SECRET` trong `backend/.env`:**
   - Đặt 1 chuỗi token mạnh (hoặc dùng đúng token role `device` trong `.vault` của `PI_AI`) để Pi và Bridge xác thực qua `Authorization: Bearer <IOT_DEVICE_SECRET>`.
4. **Hỗ trợ thêm từ khóa trong `backend/src/common/constants/violationTypes.js`:**
   - Dòng 101: thêm `upper.includes('DROWSY')` → trả về `VIOLATION_TYPES.MICROSLEEP`.
   - Dòng 109: thêm `upper.includes('DISTRACT')` → trả về `VIOLATION_TYPES.PHONE_DISTRACTION`.
5. **Chế độ Media (Đã thống nhất):**
   - Pi 4 **KHÔNG mở Live Camera Stream** khi chạy qua 4G (`ENABLE_LIVE_STREAM=0`) để tránh nặng CPU Pi; quản lý chỉ xem **Clip vi phạm 10s**, trạng thái thiết bị, CO2, GPS và bảng sự kiện.

---

## 3. Cách bật Cloudflare Tunnel HTTPS trên máy Server (Phương án B)

Máy tính của bạn quản lý server không cần IP tĩnh và không cần mở cổng Router.

### Bước 1: Cài `cloudflared` (1 lần duy nhất trên Windows)
```powershell
winget install --id Cloudflare.cloudflared
```

### Bước 2: Chạy Backend & Tunnel
* **Chạy thử nhanh (Quick Tunnel — miễn phí, không cần tên miền, URL đổi mỗi lần bật lại):**
  ```powershell
  # Mở cổng cho server_ai (nhận từ Pi 4)
  .\server_ai\run_tunnel.ps1 -LocalUrl http://127.0.0.1:5000

  # Hoặc nếu gộp thẳng vào Node.js :3000:
  .\server_ai\run_tunnel.ps1 -LocalUrl http://127.0.0.1:3000
  ```
  → Copy link `https://xxxx.trycloudflare.com` in ra màn hình gửi sang máy thiết kế Pi 4 để điền vào `REMOTE_SERVER_URL` trong `pi.env` (và cập nhật `mobileConfig.js` của App nếu test từ xa).

* **Chạy cố định (Named Tunnel — có tên miền riêng, URL không đổi khi tắt/mở máy):**
  Tạo 1 Tunnel trên Cloudflare Zero Trust Dashboard, cấu hình 2 Public Hostname cùng 1 tunnel:
  - `api.tenmien.com` → `http://127.0.0.1:3000` (Backend Node.js + Web/Mobile)
  - `iot.tenmien.com` → `http://127.0.0.1:5000` (AI Media Gateway nhận burst từ Pi 4)
  Rồi chạy:
  ```powershell
  .\server_ai\run_tunnel.ps1 -TunnelToken "<CLOUDFLARE_TUNNEL_TOKEN>"
  ```

---

## 4. Hai lựa chọn gộp Server (Bạn quản lý Server chọn 1 trong 2)

| Lựa chọn | Cách làm | Công việc cần làm |
|---|---|---|
| **Cách 1 — Chạy song song + Bridge nội bộ (Đã code sẵn trong `server_ai/server_core/pbl4_bridge.py`, khuyên dùng!)** | Máy Server chạy cả `backend` Node.js (`:3000`) và `server_ai` Python (`:5000`). Khi `server_ai` nhận telemetry / ghép xong `.mp4` từ Pi 4, `pbl4_bridge.py` tự động gọi HTTP nội bộ sang `http://127.0.0.1:3000/api/v1/edge/...`. | Chỉ cần đặt biến môi trường trước khi chạy `server_ai/run_server.py`:<br>`$env:PBL4_BACKEND_URL="http://127.0.0.1:3000"`<br>`$env:PBL4_DEVICE_TOKEN="<IOT_DEVICE_SECRET>"`<br>(tùy chọn `$env:PBL4_DEVICE_ID="XE-01"`). Bên Node.js chỉ chỉnh `.env` + 2 dòng trong `violationTypes.js`. |
| **Cách 2 — Gộp hẳn vào 1 Server Node.js duy nhất** | Không chạy Python `:5000`; Pi 4 gọi thẳng lên Node.js `:3000`. | Bên Node.js cần viết thêm route `POST /api/v1/violation_burst` (dùng `multer.array('frames', 60)` + `ffmpeg` ghép ảnh thành `.mp4`), hoặc viết adapter ở `pi_edge` (nhưng Pi 4 sẽ phải tốn CPU ghép video hoặc gửi từng ảnh lẻ). |

---

## 5. Checklist kiểm tra nối thông (5 phút tại phòng máy)
1. [ ] Khởi động Backend Node.js (`:3000`) + kiểm tra `GET /api/health` trả `status: "ok"`.
2. [ ] Khởi động `server_ai` (`:5000`) (nếu chọn Cách 1) + bật Cloudflare Tunnel.
3. [ ] Đảm bảo trong bảng `iot_devices` (SQL) có thiết bị trùng `VEHICLE_ID` của Pi (vd `"XE-01"`), và có 1 chuyến `Trip` đang ở trạng thái `IN_PROGRESS` (tài xế bấm *Bắt đầu hành trình* trên App hoặc tạo sẵn bằng `seed.js`).
4. [ ] Từ Pi 4 (qua Wi-Fi phòng máy hoặc Hotspot 4G), gửi 1 gói telemetry + 1 vi phạm thử:
   - Bản đồ Leaflet trên Web React thấy xe cập nhật tọa độ & CO2.
   - Tab Vi phạm hiện bản ghi mới kèm **video `.mp4` phát được trực tiếp**.
   - App `fleetguard-driver-app` nhận thông báo Socket.IO trừ điểm uy tín.
