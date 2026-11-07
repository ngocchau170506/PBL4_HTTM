# CẨM NANG TOÀN DIỆN: TỰ KIỂM TRA, VẬN HÀNH & BẮT LỖI HỆ THỐNG TRÊN RASPBERRY PI 4

- **Dự án:** Hệ thống giám sát tài xế thông minh (Driver Fatigue & Attention Monitoring System)
- **Mục đích:** Tài liệu hướng dẫn độc lập giúp bạn tự tay vận hành, kiểm tra từng module (Service, Mạng/VLAN/nmcli/IP, Camera, ESP32/GPS, Log) và chủ động xử lý 4 điểm tồn ẩn thực tế mà không cần phụ thuộc vào AI.
- **Môi trường:** Raspberry Pi 4 Model B (`raspberrypi@192.168.1.7` / cứu hộ `192.168.137.7`)

---

## 1. Truy cập Pi & Điều khiển chạy hệ thống (Service vs. Chạy tay trực tiếp)

Từ PowerShell hoặc Terminal trên máy tính Windows của bạn:
```powershell
# Cách 1: Khi Wi-Fi bình thường (cùng dải mạng 192.168.1.x)
ssh raspberrypi@192.168.1.7

# Cách 2: Khi mất Wi-Fi (Cắm dây LAN trực tiếp từ Pi sang PC, đặt IP LAN của PC là 192.168.137.1)
ssh raspberrypi@192.168.137.7
```
*(Mật khẩu SSH mặc định: `raspberrypi`)*

---

### A. Quản lý hệ thống chạy ngầm tự động (`systemd`)

Dịch vụ chạy ngầm được quản lý bởi systemd dưới tên `driver-monitor.service`.

```bash
# 1. Xem trạng thái hiện tại (đang chạy active hay dừng dead)
systemctl status driver-monitor.service

# 2. Xem log thời gian thực liên tục (nhấn Ctrl+C để thoát)
journalctl -u driver-monitor -f

# 3. Xem nhanh 50 dòng log gần nhất, lọc bỏ dòng hiệu năng [PERF] để dễ đọc cảnh báo và lỗi
journalctl -u driver-monitor --no-pager | grep -v "\[PERF\]" | tail -n 50

# 4. Khởi động lại dịch vụ sau khi sửa code hoặc nạp lại cấu hình
sudo systemctl restart driver-monitor.service

# 5. Dừng hẳn dịch vụ (Bắt buộc phải chạy lệnh này trước khi muốn test chạy tay)
sudo systemctl stop driver-monitor.service

# 6. Bật lại dịch vụ sau khi test tay xong
sudo systemctl start driver-monitor.service
```

---

### B. Chạy tay trực tiếp trên Terminal (Khi cần gỡ lỗi & theo dõi chi tiết)

> [!WARNING]
> **Quy tắc bất di bất dịch:** Phải chạy `sudo systemctl stop driver-monitor.service` trước khi chạy tay! Nếu không, hai tiến trình sẽ cùng tranh chấp mở cổng phần cứng `/dev/ttyUSB0` và cổng mạng Web `8080`, gây nghẽn và xung đột dữ liệu.

```bash
# Bước 1: Dừng service ngầm
sudo systemctl stop driver-monitor.service

# Bước 2: Di chuyển vào thư mục dự án
cd ~/iot_cam_project

# Bước 3: Nạp toàn bộ biến môi trường từ pi.env vào shell hiện tại
set -a; source AI/deploy/pi.env; set +a

# Bước 4: Khởi chạy ứng dụng trực tiếp bằng môi trường venv
PYTHONPATH=.:./src ./venv/bin/python -m AI.app.main

# Bước 5: Khi kiểm tra xong, nhấn Ctrl+C để dừng chương trình, sau đó khởi động lại service ngầm
sudo systemctl start driver-monitor.service
```

---

## 2. Kiểm tra Mạng, Wi-Fi, LAN (`eth0`), `nmcli`, Định tuyến (`ip route`) & Xung đột Subnet/VLAN

Khi nghi ngờ rớt mạng, kẹt luồng camera, hoặc khi mang Pi sang kết nối vào mạng Wi-Fi / VLAN khác (ví dụ: phát 4G từ iPhone `172.20.10.x` hoặc mạng trường học `ITF_Da_Nang`):

```bash
# 1. Xem tổng quan nhanh các card mạng và địa chỉ IP hiện tại
ip -br -4 addr

# 2. Xem danh sách các profile mạng trong NetworkManager (profile màu XANH là đang kích hoạt)
nmcli connection show
nmcli device status

# 3. Kiểm tra thông số kỹ thuật của cổng LAN eth0 (Camera 192.168.1.209/32 + Cứu hộ 192.168.137.7/24)
nmcli -f ipv4.method,ipv4.addresses,ipv4.routes,ipv4.never-default,connection.autoconnect connection show "netplan-eth0"

# 4. Kiểm tra bảng định tuyến hệ thống (Route Table) - CỰC KỲ QUAN TRỌNG ĐỂ PHÁT HIỆN XUNG ĐỘT
ip route
```

### Cách đối chiếu bảng định tuyến (`ip route`) chuẩn:
- Tuyến Camera: Bắt buộc phải có dòng `192.168.1.210 proto static scope link src 192.168.1.209 metric 10` đi qua `dev eth0` -> Đảm bảo duy nhất gói tin tới Camera `.210` mới ra dây LAN.
- Tuyến Gateway & Mạng chính: Dòng `default via ...` và dải `192.168.1.0/24` phải đi qua `dev wlan0` -> Đảm bảo kết nối Internet và đường truyền tới PC qua Wi-Fi không bị cổng LAN chiếm quyền.
- Tuyến Cứu hộ: Phải có dòng `192.168.137.0/24 dev eth0 src 192.168.137.7` -> Sẵn sàng cho việc cắm thẳng dây mạng vào Laptop để cứu hộ.

```bash
# 5. Kiểm tra trạng thái vật lý của cáp LAN (1: đã cắm cáp và có điện; 0: chưa cắm hoặc đứt/lỏng dây)
cat /sys/class/net/eth0/carrier
```

---

## 3. Kiểm tra đường truyền Camera RTSP (`192.168.1.210:554`)

```bash
# 1. Kiểm tra Pi nhận diện đường đi tới IP Camera qua cổng mạng nào (phải ra: dev eth0 src 192.168.1.209)
ip route get 192.168.1.210

# 2. Ping thử Camera qua cáp LAN (giới hạn 3 gói tin)
ping -c 3 -W 2 192.168.1.210

# 3. Kiểm tra cổng dịch vụ RTSP 554 trên Camera có đang mở không
nc -zv -w 3 192.168.1.210 554

# 4. Kiểm tra đọc thử 1 khung hình thật từ Camera bằng OpenCV FFmpeg (không ảnh hưởng đến service đang chạy)
~/iot_cam_project/venv/bin/python -c "
import cv2
url = 'rtsp://192.168.1.210:554/user=admin_password=tlJwpbo6_channel=1_stream=0&protocol=unicast&onvif=ture.sdp?real_stream'
cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
ok, frame = cap.read()
print('CAM_OPEN:', cap.isOpened(), '| READ_OK:', ok, '| SHAPE:', frame.shape if ok else None)
cap.release()
"
```
*(Nếu trả về `CAM_OPEN: True | READ_OK: True | SHAPE: (1080, 1920, 3)` là camera hoạt động hoàn hảo 100%)*

---

## 4. Kiểm tra ESP32, Loa I2S & Cảm biến GPS trên `/dev/ttyUSB0`

```bash
# 1. Kiểm tra mạch nạp USB UART của ESP32 có được nhân Linux nhận diện không
ls -l /dev/ttyUSB* /dev/ttyACM*
lsusb | grep -i -E "cp210|ch340|silicon|serial"

# 2. Kiểm tra tiến trình nào đang chiếm giữ cổng /dev/ttyUSB0
fuser -v /dev/ttyUSB0
```
> [!NOTE]
> Khi `driver-monitor.service` đang chạy, lệnh `fuser` phải trả về **duy nhất 1 PID của python**. Tuyệt đối không mở thêm `minicom`, `screen` hay chạy script đọc cổng nối tiếp độc lập cùng lúc, vì sẽ làm gãy các gói tin UART tốc độ cao `921600 baud`.

```bash
# 3. Xem trực tiếp gói tin ESP32 + GPS + Sự kiện cảnh báo qua API nội bộ (An toàn tuyệt đối lúc service đang chạy)
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool

# 4. Bắn lệnh test Loa trực tiếp xuống ESP32 qua API và xem phản hồi ACK từ mạch I2S
curl -s -X POST http://127.0.0.1:8080/api/control -H "Content-Type: application/json" -d '{"cmd":"VOICE:1"}'
echo ""
curl -s -X POST http://127.0.0.1:8080/api/control -H "Content-Type: application/json" -d '{"cmd":"PING"}'
echo ""
```

---

## 5. Bốn điểm "Tồn ẩn" thực tế khi tự test (Nguyên nhân & Vị trí dòng Code)

Dựa trên chuỗi nguyên nhân định lượng (`alert_reason`), dưới đây là 4 tình huống thực tế bạn sẽ gặp khi chạy thử nghiệm và vị trí mã nguồn cụ thể để xử lý:

### Tồn ẩn 1: Cảnh báo `DISTRACTED (VOICE:3)` tự kích hoạt khi để Pi trong nhà và không có người ngồi trước Cam
- **Hiện tượng:** Bạn không ngồi trước camera (`lost_face_duration >= 3.0s`), xe và Pi đặt cố định trên bàn trong phòng nhưng loa tự động hú lệnh `VOICE:3` kèm lý do `"Mất dấu mặt ... ở tốc độ 10.5km/h"`.
- **Nguyên nhân kỹ thuật:**
  Khi đặt ăng-ten GPS trong phòng kín (chỉ bắt được 4–6 vệ tinh xuyên qua kính cửa sổ), hiện tượng sóng phản xạ tường (Multipath) làm giá trị vận tốc thô trên module GPS dao động tức thời từ `0 km/h` vọt lên `5 – 12 km/h` trong 1–2 chu kỳ. Giá trị này vượt qua ngưỡng lọc đứng yên `1.8 km/h` trong `esp32_firmware.ino` (dòng 228) và chạm ngưỡng kích hoạt mất tập trung khi xe chạy `current_spd >= 10.0` tại `AI/scripts/drowsiness_pipeline.py` (dòng 558).
- **Cách nhận biết & xử lý:**
  - Nhìn bảng sự kiện Web hoặc `/api/status`, cột nhãn ghi rõ `"Mất dấu mặt ... ở tốc độ ... km/h"`.
  - Nếu muốn tắt cảnh báo mất dấu mặt khi ngồi test trong nhà, chỉ cần tăng cấu hình trong `AI/deploy/pi.env`:
    ```bash
    TRACKING_LOST_ALERT_S=999
    ```
  - Hoặc khi đem ra xe chạy thực tế ngoài trời, GPS bắt trên 10 vệ tinh thì hiện tượng vận tốc ảo này sẽ tự biến mất.

### Tồn ẩn 2: Đổi mạng Wi-Fi khác dải `192.168.1.x` (Ví dụ 4G iPhone `172.20.10.x` hoặc VLAN trường học)
- **Tác động đến Camera:**
  Camera vẫn chạy bình thường 100% trên cáp LAN `eth0` ở địa chỉ tĩnh `192.168.1.210`, do profile `netplan-eth0` đã ghim cứng route Host `/32` độc lập.
- **Tác động đến kết nối Server PC (`REMOTE_SERVER_URL`):**
  - Trong `AI/deploy/pi.env` (dòng 63), địa chỉ mặc định trỏ về `http://192.168.1.6:5000`.
  - Nếu PC đổi IP hoặc tắt, luồng chạy nền `auto_discover_pc_server()` trong `AI/scripts/cloud_sync.py` (dòng 73–145) sẽ tự động mở 48 worker quét cổng `5000` trên toàn dải mạng mới mỗi 8 giây.
  - Trong thời gian Server PC chưa sẵn sàng, các gói tin vi phạm và ảnh bằng chứng JPEG được tự động lưu trữ tạm thời an toàn vào SQLite cục bộ (`AI/data/remote_sync.db`, hiển thị tại chỉ số `pending_spool` trên Dashboard Web).

### Tồn ẩn 3: Nhiệt độ CPU Pi 4 & Hiện tượng sụt áp nguồn loa I2S (MAX98357A)
- **Nhiệt độ CPU:**
  Khi chạy liên tục, nhiệt độ Pi 4 nằm trong khoảng `68°C - 74°C` (`throttled: 0x0`). Nếu nhiệt độ vượt quá `80°C`, CPU sẽ tự giảm xung nhịp bảo vệ phần cứng, dẫn đến FPS suy diễn YOLO giảm nhẹ.
  - Lệnh kiểm tra nhiệt độ và hạ xung:
    ```bash
    vcgencmd measure_temp && vcgencmd get_throttled
    ```
- **Sụt áp nguồn Loa (Brownout):**
  Mạch loa I2S MAX98357A có công suất đỉnh khá lớn. Nếu cấp nguồn cho ESP32 qua cổng USB lỏng hoặc dây cáp mỏng, khi phát âm lượng lớn ESP32 có thể bị sụt áp tức thời.
  - Code trong `AI/scripts/esp_communication.py` (dòng 254) sẽ bắt ngay sự kiện phần cứng `EVT,WARN,BROWNOUT_SUT_AP_NGUON_LOA` và hiển thị cảnh báo `ESP_BROWNOUT` lên Dashboard Web để bạn kịp thời thay cáp nguồn.

### Tồn ẩn 4: Nhãn cảnh báo mang trạng thái `SUSPECTED` thay vì `CONFIRMED`
- **Nguyên nhân:**
  Như đã trình bày ở phần MediaPipe, khi chỉ có mô hình YOLO hoạt động (chưa có kênh MediaPipe Landmarker xác thực chéo), logic trong `AI/scripts/sensor_fusion.py` và `AI/scripts/drowsiness_pipeline.py` (dòng 663–680) sẽ gắn nhãn cảnh báo ở mức `SUSPECTED` (Nghi ngờ - 1 nguồn).
- **Đánh giá an toàn:**
  Dù ở mức `SUSPECTED`, hệ thống vẫn kích hoạt đầy đủ còi loa cảnh báo (`VOICE:1` nhắm mắt, `VOICE:2` ngáp) và ghi nhận vi phạm như bình thường, đảm bảo an toàn tuyệt đối khi thử nghiệm thực tế.
