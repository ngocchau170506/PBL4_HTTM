# BÁO CÁO CHI TIẾT CÁC BẢN VÁ HỆ THỐNG ĐÃ ÁP DỤNG TRÊN MÁY TÍNH VÀ RASPBERRY PI

- **Dự án:** Hệ thống giám sát tài xế thông minh (Driver Fatigue & Attention Monitoring System)
- **Thời gian áp dụng & xác thực:** Ngày 05 tháng 11 năm 2026
- **Phạm vi đồng bộ:** 
  - Máy tính phát triển (PC): `e:\AI\IOTv3`
  - Thiết bị nhúng trên xe (Raspberry Pi 4): `/home/raspberrypi/iot_cam_project`
- **Thư mục sao lưu an toàn trước khi vá:**
  - Trên Raspberry Pi: `~/iot_cam_project_backup_20261105/`
  - Trên Máy tính: `scratch/backup_local/`

---

## 1. Bản vá 1: Sửa lỗi sập gửi cảnh báo vi phạm (`TypeError: datetime * int` & `None confidence`)

### 1.1. Vị trí & Triệu chứng lỗi
- **File & Vị trí:** `AI/scripts/cloud_sync.py` (hàm `dispatch_violation`, dòng 535–598)
- **Triệu chứng:** Khi luồng AI phát hiện tài xế nhắm mắt hoặc ngáp thực tế, hàm `dispatch_violation` bị văng ngoại lệ `TypeError: can't multiply sequence by non-int of type 'float'` hoặc lỗi tương tự về datetime, in ra log:
  ```text
  [CLOUD SYNC] dispatch_violation error: unsupported operand type(s) for *: 'datetime.datetime' and 'int'
  ```
  Hậu quả là sự kiện vi phạm và ảnh bằng chứng JPEG bị hủy bỏ hoàn toàn, không thể gửi lên server hoặc spool vào SQLite.

### 1.2. Nguyên nhân kỹ thuật
- Trong luồng xử lý `AI/scripts/drowsiness_pipeline.py` (dòng 907–914), các biến mốc thời gian `started_at` và `confirmed_at` được sinh ra dưới dạng đối tượng `datetime.datetime` (thông qua hàm `local_now()`), và `confidence` có trường hợp là `None`.
- Trong khi đó, hàm `dispatch_violation()` trong `cloud_sync.py` lại giả định mốc thời gian luôn là số thực `float` epoch timestamp, thực hiện trực tiếp:
  ```python
  event_id = f"EVT_{int(conf_t * 1000)}"  # conf_t là datetime -> crash TypeError!
  "confidence": round(confidence, 2)       # confidence là None -> crash TypeError!
  ```

### 1.3. Mã nguồn Trước và Sau khi sửa
- **Trước khi sửa:**
  ```python
  now = time.time()
  start_t = started_at if started_at else (now - duration_seconds)
  conf_t = confirmed_at if confirmed_at else now
  event_id = f"EVT_{int(conf_t * 1000)}"
  ...
  "confidence": round(confidence, 2),
  ```
- **Sau khi sửa:**
  Bổ sung phương thức tĩnh `_to_epoch_seconds` để chuẩn hóa đa năng mọi kiểu dữ liệu thời gian (`float`, `int`, `datetime.datetime`, chuỗi ISO) về số thực epoch timestamp:
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

### 1.4. Kết quả kiểm chứng thực tế
Đã kiểm thử trực tiếp trên Python 3.13 của Pi bằng lệnh test giả lập vi phạm thực tế với `datetime.now()` và `confidence=None`:
```text
VIOLATION_PAYLOAD_OK: EVT_1793886952961 DROWSY_ALERT 2026-11-05T20:55:52+0700 | confidence: 1.0
```
Toàn bộ gói tin và dữ liệu vi phạm được đóng gói chuẩn JSON, ghi nhận vào hàng đợi lưu trữ an toàn.

---

## 2. Bản vá 2: Thiết kế cổng LAN `eth0` đa năng & Triệt tiêu hoàn toàn lệnh `sudo` ngầm trong Python

### 2.1. Vị trí & Triệu chứng lỗi
- **File & Vị trí:**
  1. Cấu hình NetworkManager hệ thống: `/etc/NetworkManager/system-connections/netplan-eth0.nmconnection`
  2. Mã nguồn Python: `AI/scripts/camera_config.py` (hàm `ensure_pi_eth_camera_route()`)
- **Triệu chứng:**
  - Khi Pi khởi động lại thực tế (Cold Reboot), phiên của user `raspberrypi` không có sẵn vé xác thực `sudo`. Hàm `ensure_pi_eth_camera_route()` gọi ngầm các lệnh `sudo -n ip ...` và `sudo -n nmcli ...`, dẫn đến bị hệ điều hành từ chối quyền hạn (`sudo: a password is required`), làm mất route tới camera và ứng dụng không kết nối được camera IP.
  - Cấu hình cũ để `eth0` ở chế độ DHCP (`method=auto`). Khi cắm cáp LAN trực tiếp vào Camera hoặc Máy tính (không có DHCP server), cổng `eth0` không có địa chỉ IP nào.

### 2.2. Giải pháp kỹ thuật đã áp dụng

#### A. Cấu hình NetworkManager cấp Hệ điều hành (`netplan-eth0`):
Thiết lập profile mạng tĩnh bền vững qua mọi lần khởi động lại máy:
- **Nhánh Camera:** Gán địa chỉ IP tĩnh `192.168.1.209/32` đi kèm Host Route duy nhất trỏ tới IP Camera `192.168.1.210/32` trên cổng `eth0` với độ ưu tiên cao (`metric 10`). Việc sử dụng mặt nạ mạng `/32` **triệt tiêu hoàn toàn nguy cơ xung đột bảng định tuyến với card Wi-Fi `wlan0` cùng dải `192.168.1.x`**.
- **Nhánh Cứu hộ Máy tính:** Gán thêm địa chỉ IP tĩnh thứ hai **`192.168.137.7/24`** trên cùng cổng `eth0`. Khi mất sóng Wi-Fi ngoài thực địa, chỉ cần cắm cáp LAN nối thẳng Pi với Laptop (đặt IP Laptop là `192.168.137.1`), người vận hành lập tức truy cập được SSH và Web UI mà không cần router hay switch trung gian.

#### B. Loại bỏ 100% lệnh gọi `sudo` ngầm trong `AI/scripts/camera_config.py`:
- **Trước khi sửa:** Gọi ngầm các lệnh quản trị: `sudo -n ip link set eth0 up`, `sudo -n nmcli dev set eth0 managed no`, `sudo -n ip addr replace...`
- **Sau khi sửa:** Chuyển đổi toàn bộ hàm sang cơ chế **Chỉ đọc (Read-Only)**:
  - Kiểm tra trạng thái cáp vật lý trực tiếp qua sysfs: `/sys/class/net/eth0/carrier`.
  - Kiểm tra tuyến đường mạng bằng lệnh người dùng tiêu chuẩn: `ip route show 192.168.1.210/32`.
  - Không bao giờ can thiệp cấu hình hệ thống bằng subprocess từ Python.

### 2.3. Kết quả kiểm chứng thực tế sau khi thu hồi vé sudo (`sudo -k`)
```text
inet 192.168.137.7/24 brd 192.168.137.255 scope global noprefixroute eth0
inet 192.168.1.209/32 scope global noprefixroute eth0
192.168.1.210 proto static scope link src 192.168.1.209 metric 10
ROUTE_OK_NO_SUDO: True
CAMERA_RTSP_554_ONLINE: True
```
Dịch vụ `driver-monitor.service` khởi động hoàn toàn trong sạch, không còn bất kỳ dòng log nào cảnh báo về quyền hạn sudo.

---

## 3. Bản vá 3: Truy xuất nguyên nhân định lượng, theo dõi phản hồi phần cứng ESP32 & Chống tràn log

### 3.1. Đọc tốc độ GPS dạng JSON trong `AI/scripts/drowsiness_pipeline.py` (dòng 399–423)
- **Vấn đề cũ:** Hàm `_get_current_speed()` chỉ tìm kiếm chuỗi định dạng CSV cũ `"SPD:"`. Trong khi firmware ESP32 hiện tại gửi bản tin telemetry dạng JSON chuẩn `FDESP:{"speed_kmh":...}`. Điều này khiến biến tốc độ trong pipeline luôn bị gán bằng `0.0 km/h`, làm sai lệch các ngưỡng kích hoạt theo tốc độ xe.
- **Sau khi sửa:** Hỗ trợ giải mã đồng thời cả trường `"speed_kmh"` từ bản tin JSON `FDESP:` lẫn định dạng legacy `"SPD:"`.

### 3.2. Chuỗi nguyên nhân định lượng (`alert_reason`) tại `AI/scripts/drowsiness_pipeline.py` (dòng 719–742)
- **Vấn đề cũ:** Bảng sự kiện chỉ hiển thị chung chung `SUSPECTED` hoặc lệnh còi `VOICE:`, không biết được cụ thể vì sao hệ thống lại cảnh báo; đồng thời bị tràn ngập hàng chục dòng thông báo `"TRACKING_LOST"` khi tài xế rời vị trí.
- **Sau khi sửa:** Mỗi cảnh báo sinh chuỗi nguyên nhân định lượng rõ ràng:
  - **Ngủ gật:** `Nhắm mắt 2.0s/2.0s (nguồn=yolo_only)` hoặc kèm `EAR=...`
  - **Ngáp:** `Ngáp 1.2s/1.0s (nguồn=...)`
  - **Mất tập trung:** `Lệch góc đầu ... (Pitch=..., Yaw=...)` hoặc `Mất dấu mặt ... ở tốc độ ... km/h`
  - **Lọc xung đột:** `Xung đột: YOLO=... nhưng MediaPipe báo bình thường -> Chặn cảnh báo giả`

### 3.3. Bắt phản hồi phần cứng thực tế từ ESP32 tại `AI/scripts/esp_communication.py` & `AI/app/web_server.py`
- **Vấn đề cũ:** Khi ESP32 khởi động, nó gửi 2 gói tin `EVT,BOOT_GPS_READY` và `EVT,RESET_REASON,POWERON`. Thuật toán cũ đếm nhầm thành `reset_count = 2` và phát cảnh báo giả là loa bị sụt nguồn. Ngoài ra, các cảnh báo tự động từ cảm biến khí CO₂ (`EVT,AUTO_ALERT,CO2_...`) hoặc sụt áp thật (`BROWNOUT`) không được hiển thị lên Dashboard Web.
- **Sau khi sửa:**
  - Phân định chuẩn xác giữa khởi động cấp nguồn bình thường (`POWERON`) với lỗi sụt áp loa/nguồn thật sự (`ESP_BROWNOUT`).
  - Đồng bộ các sự kiện phần cứng độc lập từ ESP32 (`AUTO_CO2_WARN`, `AUTO_CO2_CRIT`, `ESP_BROWNOUT`, `MUTE_ACTIVE`) trực tiếp lên bảng **Nhật Ký Cảnh Báo Gần Nhất** trên giao diện Web.
  - Hiển thị tường minh trạng thái phát loa: `VOICE:1 [ACK: EVT,ACK,VOICE,1,i2s=1]` (đã phát loa thành công), `COOLDOWN (còn 3.2s)` (chặn phát âm thanh đè nhau), hoặc `VOICE:1 [NO_ACK_TIMEOUT]` (cảnh báo lỗi kết nối UART vật lý).

### 3.4. Chống tràn log hệ thống & Tối ưu hiệu năng
- Giảm tần suất in dòng log `[DETECT]`: chỉ in khi có sự kiện chuyển đổi trạng thái hoặc tối đa 2 giây/lần trong lúc đang tích lũy thời gian vi phạm.
- Thiết lập cấu hình `PERF_LOG_INTERVAL=30.0` trong `pi.env` (ghi log hiệu năng mỗi 30 giây thay vì 5 giây).
- Bổ sung `Environment="MPLCONFIGDIR=/tmp/matplotlib"` vào `driver-monitor.service` để triệt tiêu hoàn toàn cảnh báo quyền ghi thư mục ẩn `.config/matplotlib` lúc khởi động. File log CSV chi tiết `logs/performance.csv` vẫn ghi nhận đầy đủ theo từng khung hình như thiết kế ban đầu.
