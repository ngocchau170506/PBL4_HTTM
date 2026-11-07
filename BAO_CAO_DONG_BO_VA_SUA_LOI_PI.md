# BÁO CÁO TỔNG HỢP: ĐỒNG BỘ BẢN VÁ HỆ THỐNG VÀ XỬ LÝ MEDIAPIPE TRÊN RASPBERRY PI 4

- **Dự án:** Hệ thống giám sát tài xế thông minh (Driver Fatigue & Attention Monitoring System)
- **Thời gian thực hiện:** Ngày 05 tháng 11 năm 2026
- **Môi trường triển khai:**
  - **Môi trường máy tính phát triển:** Windows PC (`e:\AI\IOTv3`)
  - **Môi trường nhúng trên xe:** Raspberry Pi 4 Model B (Debian 13 trixie aarch64, thư mục `~/iot_cam_project`)
  - **Phần cứng ngoại vi:** ESP32 (COM3 / CP210x, 921600 baud), Camera IP RTSP Vantech (`192.168.1.210:554`, luồng 1080p), Cảm biến CO₂ MQ-135, Mạch khuếch đại âm thanh I2S MAX98357A.
- **Thư mục sao lưu an toàn trước khi vá:**
  - Trên Raspberry Pi: `~/iot_cam_project_backup_20261105/`
  - Trên Máy tính: `scratch/backup_local/`

---

## MỤC LỤC

1. [PHẦN I: BÁO CÁO RIÊNG VỀ MEDIAPIPE TRÊN RASPBERRY PI 4](#phần-i-báo-cáo-riêng-về-mediapipe-trên-raspberry-pi-4)
   - [1. Triệu chứng lỗi thực tế trên Pi](#1-triệu-chứng-lỗi-thực-tế-trên-pi)
   - [2. Nguyên nhân gốc rễ (Root Cause)](#2-nguyên-nhân-gốc-rễ-root-cause)
   - [3. Cơ chế hiện tại trong code & Ảnh hưởng tới hệ thống](#3-cơ-chế-hiện-tại-trong-code--ảnh-hưởng-tới-hệ-thống)
   - [4. Ba phương án khắc phục đề xuất](#4-ba-phương-án-khắc-phục-đề-xuất)
2. [PHẦN II: CHI TIẾT CÁC BẢN VÁ ĐÃ ÁP DỤNG TRÊN CẢ HAI HỆ THỐNG](#phần-ii-chi-tiết-các-bản-vá-đã-áp-dụng-trên-cả-hai-hệ-thống)
   - [1. Sửa lỗi sập gửi cảnh báo vi phạm (TypeError: datetime * int & None confidence)](#1-sửa-lỗi-sập-gửi-cảnh-báo-vi-phạm-typeerror-datetime--int)
   - [2. Thiết kế cổng LAN eth0 đa năng & Triệt tiêu hoàn toàn lệnh sudo ngầm trong Python](#2-thiết-kế-cổng-lan-eth0-đa-năng-bắt-camera--cứu-hộ-nối-thẳng-máy-tính--xóa-bỏ-sudo-ngầm-trong-code-python)
   - [3. Truy xuất nguyên nhân cảnh báo bên trong, theo dõi phản hồi phần cứng ESP32 & Chống tràn log](#3-thiết-kế-truy-xuất-rõ-nguyên-nhân-cảnh-báo-bên-trong-theo-dõi-phản-hồi-phần-cứng-esp32--chống-tràn-log)
3. [PHẦN III: HƯỚNG DẪN VẬN HÀNH & KẾT NỐI CỨU HỘ](#phần-iii-hướng-dẫn-vận-hành--kết-nối-cứu-hộ)

---

# PHẦN I: BÁO CÁO RIÊNG VỀ MEDIAPIPE TRÊN RASPBERRY PI 4
*(Tài liệu chuyên biệt để trao đổi với thành viên phụ trách mảng AI / MediaPipe)*

> [!IMPORTANT]
> **Trạng thái mã nguồn:** Đã tuân thủ nghiêm ngặt chỉ đạo **giữ nguyên hoàn toàn logic và mã nguồn MediaPipe**, không tự ý can thiệp vào các thuật toán trích xuất điểm mốc khuôn mặt.

### 1. Triệu chứng lỗi thực tế trên Pi
Khi dịch vụ `driver-monitor.service` khởi động trên Raspberry Pi 4, quá trình nạp MediaPipe bị sập ngầm ở mức tầng nhị phân với thông báo:
```text
[STARTUP] MediaPipe unavailable; YOLO-only mode
(FATAL ERROR: This binary was compiled with aes enabled, but this feature is not available on this processor (go/sigill-fail-fast).)
```

### 2. Nguyên nhân gốc rễ (Root Cause)
- Thư viện `mediapipe` đang cài trong môi trường Python `venv` của Pi (cụ thể là file nhị phân C++ `_framework_bindings.cpython-313-aarch64-linux-gnu.so`) được biên dịch sẵn kèm theo cờ phần cứng **ARMv8 Cryptographic Extension (`+aes`)**.
- Bộ vi xử lý **Broadcom BCM2711 (ARM Cortex-A72)** của **Raspberry Pi 4 không hỗ trợ phần cứng tập lệnh tăng tốc mã hóa AES** (tập lệnh này chỉ bắt đầu có từ vi xử lý Cortex-A76 trên Raspberry Pi 5 trở lên).
- Ngay khi hàm Python gọi `FaceLandmarker.create_from_options()`, nhân C++ bên dưới của MediaPipe thực hiện tự kiểm tra CPU features. Thấy thiếu cờ `aes`, nhân lập tức phát tín hiệu ngắt **`SIGILL` (Illegal Instruction)** làm tiến trình con bị hủy ngay lập tức (`Fail-Fast`).

### 3. Cơ chế hiện tại trong code & Ảnh hưởng tới hệ thống
- **Cơ chế tự bảo vệ đã có trong code (`AI/app/main.py` dòng 147–171):**
  Hệ thống sử dụng một tiến trình con `subprocess.run(..., timeout=15)` để chạy kiểm tra thử MediaPipe trước. Khi tiến trình con sập bởi `SIGILL`, file `main.py` bắt được ngoại lệ, log cảnh báo và tự động chuyển sang chế độ **chỉ dùng YOLO (`YOLO-only mode`, biến `landmarker = None`)** giúp ứng dụng không bị chết toàn bộ.
- **3 ảnh hưởng kỹ thuật thực tế khi chạy trên Pi 4:**
  1. **Khởi động dịch vụ bị chậm trễ:** Mỗi lần Pi hoặc dịch vụ bật lên, hệ thống phải mất từ 5 đến 15 giây để tạo tiến trình con nạp thư viện nặng và chờ sập `SIGILL`.
  2. **Hạ cấp mức độ cảnh báo (Mất kênh kiểm chứng chéo EAR/MAR):**
     - Khi có đủ 2 mô hình (YOLO + MediaPipe Landmarker), cảnh báo nhắm mắt/ngáp đạt mức **`CONFIRMED`** (Độ tin cậy cao, 2 nguồn đồng thuận).
     - Khi mất MediaPipe, mọi cảnh báo chỉ đạt mức **`SUSPECTED`** (Nghi ngờ - chỉ dựa vào 1 nguồn duy nhất là YOLO) và không thực hiện được bước lọc xung đột cảnh báo giả (`CONFLICT`).
  3. **Mất tính năng đo góc nghiêng đầu 3D (Head Pose Pitch/Yaw/Roll):**
     Vì góc nghiêng đầu 3D được tính toán từ 468 điểm mốc khuôn mặt thông qua thuật toán `solvePnP` trong `AI/scripts/drowsiness_pipeline.py`, khi MediaPipe tắt thì thông số `Head Pose` trên Dashboard Web luôn hiển thị `-- / -- / --`.

### 4. Ba phương án khắc phục đề xuất (Để hội ý với phụ trách MediaPipe)

| Phương án | Cách thực hiện chi tiết | Đánh giá Trước & Sau |
| :--- | :--- | :--- |
| **Phương án 1 (Khuyên dùng nếu muốn giữ đủ EAR/MAR & Góc đầu 3D trên Pi 4)** | Cài bản build `mediapipe` bánh xe (`.whl`) được biên dịch cho kiến trúc ARMv8 Cortex-A72 tiêu chuẩn (không bật `+aes`), hoặc sử dụng mô hình mốc khuôn mặt nhẹ qua `onnxruntime` / OpenCV FaceYN thay thế nhân C++ MediaPipe. | • **Trước:** MediaPipe chết `SIGILL`, mất hoàn toàn EAR/MAR và Head Pose 3D.<br>• **Sau:** Chạy song song cả YOLO + Facial Landmarker mượt mà trên Pi 4, cảnh báo đạt chuẩn xác thực chéo `CONFIRMED`. |
| **Phương án 2 (Bổ sung kiểm tra CPU để bỏ qua bước chờ crash 15s)** | Trong `AI/app/main.py`, kiểm tra `/proc/cpuinfo` trước khi khởi chạy subprocess:<br>```python<br>if sys.platform.startswith("linux") and os.path.exists("/proc/cpuinfo"):<br>    cpu_features = open("/proc/cpuinfo").read()<br>    if "Features" in cpu_features and " aes " not in cpu_features:<br>        raise RuntimeError("CPU Pi 4 khong ho tro tap lenh AES cho MediaPipe")<br>``` | • **Trước:** Mỗi lần khởi động tốn 15 giây tạo process và chờ crash `SIGILL`.<br>• **Sau:** Trên PC hoặc Pi 5 (có `aes`), MediaPipe vẫn hoạt động bình thường; trên Pi 4 (không có `aes`), code nhận diện chỉ mất 0.001 giây và vào thẳng chế độ YOLO ngay tức thì. |
| **Phương án 3 (Cấu hình qua biến môi trường)** | Đặt `DISABLE_MEDIAPIPE=1` trong file `AI/deploy/pi.env`. | • **Đặc điểm:** Không cần can thiệp code Python. Khi chạy Pi 4 thì bật `=1`, khi nâng cấp lên Pi 5 thì đổi lại thành `=0`. |

---

# PHẦN II: CHI TIẾT CÁC BẢN VÁ ĐÃ ÁP DỤNG TRÊN CẢ HAI HỆ THỐNG
*(Đã áp dụng và đồng bộ trên PC `e:\AI\IOTv3` và Pi `~/iot_cam_project`)*

---

### 1. Sửa lỗi sập gửi cảnh báo vi phạm (`TypeError: datetime * int`)
- **File & Vị trí:** `AI/scripts/cloud_sync.py` (hàm `dispatch_violation`, dòng 535–598)
- **Nguyên nhân:**
  Trong luồng xử lý ngủ gật thực tế (`AI/scripts/drowsiness_pipeline.py`), tham số `started_at` và `confirmed_at` được truyền vào dưới dạng đối tượng `datetime.datetime` (tạo từ `local_now()`), đồng thời `confidence` có trường hợp là `None`. Trong khi đó, `dispatch_violation()` lại thực hiện phép toán `int(conf_t * 1000)` và `round(confidence, 2)` gây văng lỗi `TypeError`.
- **Đoạn mã trước khi sửa:**
  ```python
  now = time.time()
  start_t = started_at if started_at else (now - duration_seconds)
  conf_t = confirmed_at if confirmed_at else now
  event_id = f"EVT_{int(conf_t * 1000)}"
  ...
  "confidence": round(confidence, 2),
  ```
- **Đoạn mã sau khi sửa:**
  ```python
  @staticmethod
  def _to_epoch_seconds(val: Any, fallback: float) -> float:
      """Chuẩn hóa mốc thời gian (float/int epoch, datetime.datetime, hoặc chuỗi ISO) về epoch seconds."""
      if val is None:
          return fallback
      if isinstance(val, (int, float)) and not isinstance(val, bool):
          return float(val)
      if hasattr(val, "timestamp"):
          return float(val.timestamp())
      if isinstance(val, str):
          try:
              return float(datetime.fromisoformat(val).timestamp())
          except Exception:
              pass
      return fallback

  now = time.time()
  dur_s = float(duration_seconds or 0.0)
  conf_t = self._to_epoch_seconds(confirmed_at, now)
  start_t = self._to_epoch_seconds(started_at, conf_t - dur_s)
  conf_score = round(float(confidence), 2) if confidence is not None else 1.0
  event_id = f"EVT_{int(conf_t * 1000)}"
  ```
- **Kết quả kiểm chứng:**
  - **Trước khi sửa:** Mỗi khi AI phát hiện vi phạm thực tế sẽ văng lỗi `[CLOUD SYNC] dispatch_violation error`, toàn bộ gói tin cảnh báo và ảnh chụp bằng chứng bị hủy bỏ, không gửi được lên server.
  - **Sau khi sửa:** Đã kiểm thử trực tiếp trên Pi với đối tượng `datetime.now()` -> Đóng gói payload thành công tuyệt đối:
    ```text
    VIOLATION_PAYLOAD_OK: EVT_1793886952961 DROWSY_ALERT 2026-11-05T20:55:52+0700
    ```

---

### 2. Thiết kế cổng LAN `eth0` đa năng (Bắt Camera + Cứu hộ nối thẳng Máy tính) & Xóa bỏ `sudo` ngầm trong code Python
- **File & Vị trí:**
  1. Cấu hình hệ thống NetworkManager profile `netplan-eth0` trên Pi (`/etc/NetworkManager/system-connections/netplan-eth0.nmconnection`).
  2. Hàm `ensure_pi_eth_camera_route()` trong `AI/scripts/camera_config.py`.
- **Nguyên nhân sự cố trước đây:**
  - `camera_config.py` cũ gọi ngầm tới 5 lệnh `sudo -n nmcli / ip ...` mỗi khi khởi tạo kết nối camera. Khi Pi khởi động lại (Cold Reboot), phiên làm việc của user `raspberrypi` không có sẵn vé `sudo`, dẫn đến toàn bộ lệnh `sudo -n` bị từ chối quyền (`password is required`), làm mất kết nối camera.
  - Profile mạng `netplan-eth0` để chế độ `ipv4.method=auto` (chờ DHCP), nên khi cắm trực tiếp cáp mạng vào Camera hoặc máy tính (không có DHCP server), cổng `eth0` bị treo ở trạng thái không có địa chỉ IP.
- **Giải pháp toàn diện đã áp dụng:**
  1. **Cấu hình mạng hệ thống mức OS (`netplan-eth0`):**
     - **Địa chỉ nhánh Camera:** Gán IP tĩnh `192.168.1.209/32` đi kèm Host Route đơn lẻ trỏ tới Camera `192.168.1.210/32` trên `eth0` (độ ưu tiên `metric 10`). Vì dùng mặt nạ `/32` nên **hoàn toàn không tạo xung đột dải mạng với Wi-Fi `192.168.1.x`**.
     - **Địa chỉ nhánh Cứu hộ Máy tính:** Gán thêm IP tĩnh **`192.168.137.7/24`**. Khi gặp sự cố mất Wi-Fi ngoài thực địa, chỉ cần rút đầu cáp LAN từ Camera cắm thẳng vào cổng LAN của Laptop (đặt IP Laptop là `192.168.137.1`), người vận hành có thể SSH thẳng vào `raspberrypi@192.168.137.7` hoặc mở trình duyệt vào Web Dashboard `http://192.168.137.7:8080`.
  2. **Tối ưu mã nguồn Python trong `camera_config.py`:**
     - **Trước:** Chạy các lệnh xâm phạm quyền hạn hệ điều hành: `sudo -n ip link set eth0 up`, `sudo -n nmcli dev set eth0 managed no`, `sudo -n ip addr replace...`
     - **Sau:** Loại bỏ 100% các lệnh gọi `sudo`. Hàm chuyển sang cơ chế **Read-Only**: đọc trạng thái cáp từ `/sys/class/net/eth0/carrier` và kiểm tra bảng định tuyến bằng lệnh người dùng thường `ip route show 192.168.1.210/32`.
- **Kết quả kiểm chứng thực tế trên Pi (sau khi thu hồi vé sudo bằng `sudo -k`):**
  ```text
  inet 192.168.137.7/24 brd 192.168.137.255 scope global noprefixroute eth0
  inet 192.168.1.209/32 scope global noprefixroute eth0
  192.168.1.210 proto static scope link src 192.168.1.209 metric 10
  ROUTE_OK_NO_SUDO: True
  CAMERA_RTSP_554_ONLINE: True
  ```
  Trong log dịch vụ `journalctl` hoàn toàn sạch bóng các lỗi liên quan đến sudo.

---

### 3. Thiết kế truy xuất rõ nguyên nhân cảnh báo bên trong, theo dõi phản hồi phần cứng ESP32 & Chống tràn log
- **Các file & Vị trí:**
  1. `AI/scripts/esp_communication.py` (dòng 110–118, 248–305, 431–485)
  2. `AI/scripts/drowsiness_pipeline.py` (dòng 329, 399–423, 716–845)
  3. `AI/app/web_server.py` (dòng 879, 917–993)
  4. `AI/deploy/pi.env` & `AI/deploy/driver-monitor.service`

- **Chi tiết từng cải tiến:**
  1. **Đọc tốc độ GPS dạng JSON trong `drowsiness_pipeline.py`:**
     - *Trước:* Hàm `_get_current_speed()` chỉ tìm kiếm chuỗi định dạng CSV cũ `"SPD:"`. Trong khi firmware ESP32 hiện tại gửi bản tin JSON `FDESP:{"speed_kmh":...}`, dẫn đến biến tốc độ trong pipeline luôn bị gán bằng `0.0`.
     - *Sau:* Hỗ trợ giải mã đồng thời cả trường `"speed_kmh"` từ JSON `FDESP:` lẫn định dạng legacy `"SPD:"`.
  2. **Chuỗi nguyên nhân định lượng (`alert_reason`) phục vụ giám sát và gỡ lỗi:**
     - *Trước:* Bảng sự kiện chỉ hiển thị chung chung `SUSPECTED` hoặc lệnh còi `VOICE:`, không biết được cụ thể vì sao hệ thống lại cảnh báo; đồng thời bị tràn ngập hàng chục dòng thông báo `"TRACKING_LOST"` khi tài xế rời vị trí.
     - *Sau:* Mỗi cảnh báo sinh chuỗi nguyên nhân định lượng rõ ràng:
       - **Ngủ gật:** `Nhắm mắt 2.0s/2.0s (nguồn=yolo_only)` hoặc kèm `EAR=...`
       - **Ngáp:** `Ngáp 1.2s/1.0s (nguồn=...)`
       - **Mất tập trung:** `Lệch góc đầu ... (Pitch=..., Yaw=...)` hoặc `Mất dấu mặt ... ở tốc độ ... km/h`
       - **Lọc xung đột:** `Xung đột: YOLO=... nhưng MediaPipe báo bình thường -> Chặn cảnh báo giả`
  3. **Theo dõi phản hồi phần cứng thực tế từ ESP32:**
     - *Trước:* Khi ESP32 khởi động, nó gửi 2 gói tin `EVT,BOOT_GPS_READY` và `EVT,RESET_REASON,POWERON`. Thuật toán cũ đếm nhầm thành `reset_count = 2` và phát cảnh báo giả là loa bị sụt nguồn. Ngoài ra, các cảnh báo tự động từ cảm biến khí CO₂ (`EVT,AUTO_ALERT,CO2_...`) hoặc sụt áp thật (`BROWNOUT`) không được hiển thị lên Dashboard Web.
     - *Sau:*
       - Phân định chuẩn xác giữa khởi động cấp nguồn bình thường (`POWERON`) với lỗi sụt áp loa/nguồn thật sự (`ESP_BROWNOUT`).
       - Đồng bộ các sự kiện phần cứng độc lập từ ESP32 (`AUTO_CO2_WARN`, `AUTO_CO2_CRIT`, `ESP_BROWNOUT`, `MUTE_ACTIVE`) trực tiếp lên bảng **Nhật Ký Cảnh Báo Gần Nhất** trên giao diện Web.
       - Hiển thị tường minh trạng thái phát loa: `VOICE:1 [ACK: EVT,ACK,VOICE,1,i2s=1]` (đã phát loa thành công), `COOLDOWN (còn 3.2s)` (chặn phát âm thanh đè nhau), hoặc `VOICE:1 [NO_ACK_TIMEOUT]` (cảnh báo lỗi kết nối UART vật lý).
  4. **Tối ưu hóa và chống tràn log hệ điều hành:**
     - Giảm tần suất in dòng log `[DETECT]`: chỉ in khi có sự kiện chuyển đổi trạng thái hoặc tối đa 2 giây/lần trong lúc đang tích lũy thời gian vi phạm.
     - Thiết lập cấu hình `PERF_LOG_INTERVAL=30.0` trong `pi.env` (ghi log hiệu năng mỗi 30 giây thay vì 5 giây).
     - Bổ sung `Environment="MPLCONFIGDIR=/tmp/matplotlib"` vào `driver-monitor.service` để triệt tiêu hoàn toàn cảnh báo quyền ghi thư mục ẩn `.config/matplotlib` lúc khởi động. File log CSV chi tiết `logs/performance.csv` vẫn ghi nhận đầy đủ theo từng khung hình như thiết kế ban đầu.

---

# PHẦN III: HƯỚNG DẪN VẬN HÀNH & KẾT NỐI CỨU HỘ

### 1. Khởi động và kiểm tra dịch vụ trên Raspberry Pi
```bash
# Khởi động lại dịch vụ sau khi cập nhật
sudo systemctl restart driver-monitor.service

# Xem log thời gian thực của hệ thống
journalctl -u driver-monitor -f

# Kiểm tra trạng thái dịch vụ
sudo systemctl status driver-monitor.service
```

### 2. Hướng dẫn kết nối Cứu hộ ngoài thực địa (Khi mất Wi-Fi)
1. Rút cáp mạng LAN từ Camera cắm trực tiếp vào cổng mạng trên Máy tính/Laptop.
2. Trên máy tính, cấu hình IP tĩnh cho cổng Ethernet:
   - **IP Address:** `192.168.137.1`
   - **Subnet Mask:** `255.255.255.0`
3. Truy cập hệ thống:
   - **SSH điều khiển:** `ssh raspberrypi@192.168.137.7`
   - **Dashboard Web:** Mở trình duyệt truy cập `http://192.168.137.7:8080`
