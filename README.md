# IOTv3 — Hệ Thống Giám Sát Tài Xế Thực Chiến Trên Xe (Raspberry Pi 4 ARM64)

Phiên bản nâng cấp tối ưu hóa thực chiến dành riêng cho bo mạch nhúng **Raspberry Pi 4 Model B (Quad-core ARM Cortex-A72 @ 1.5GHz / 1.8GHz, 4GB/8GB RAM)** vận hành trực tiếp trên xe ô tô.

---

## 1. Kiến Trúc Tối Ưu Cốt Lõi

Hệ thống được thiết kế theo phân tầng chuyên biệt, loại bỏ hoàn toàn các module mô phỏng thừa để đạt tốc độ cao và độ trễ thấp nhất:

1. **`src/` — XƯƠNG SỐNG (Code Chính)**:
   - Quản lý luồng thu hình từ Camera Vantech IP Camera LAN RTSP TCP / USB webcam với hàng đợi đệm siêu nhẹ (`max_depth = 1`), không bị trễ khung hình tích lũy.
   - Giao tiếp UART Serial trực tiếp với ESP32, tự nhận diện cổng (`auto_detect_serial_port`), hỗ trợ phát giọng nói tiếng Việt `VOICE:1..5`, `STOP`, `PING` với độ trễ phát lệnh $< 5\text{ms}$.
   - Quản lý toàn vẹn dữ liệu hợp đồng chuẩn (`src/contracts/`), bộ đệm sự cố vòng giới hạn 8 sự kiện trên RAM để không bao giờ làm tràn bộ nhớ nhúng.
   - Tích hợp giám sát phần cứng SoC (`hardware_monitor.py`): bắt cờ sụt áp nguồn tẩu sạc xe hơi ($< 4.63\text{V}$) và cảnh báo quá nhiệt ($> 80^\circ\text{C}$).

2. **`AI/` — BỘ NÃO (Inference Engine)**:
   - Suy diễn nhận diện mắt/miệng bằng YOLOv8 (`AI/models/best.pt` hoặc thư mục NCNN).
   - Trích xuất 468 điểm mốc khuôn mặt, EAR, MAR bằng MediaPipe FaceLandmarker tối ưu đa luồng **XNNPACK**.
   - Cơ chế probe an toàn tự động chuyển sang chế độ YOLO-only nếu MediaPipe gặp sự cố thư viện C++.

3. **`AI/app/` — VẬN HÀNH THỰC CHIẾN (Web Dashboard & Systemd Service)**:
   - Web Dashboard MJPEG thời gian thực trên cổng `8080`, chạy thuần Python HTTP Server, **100% offline không cần Internet**.
   - Hiển thị trực tiếp: FPS suy diễn, trạng thái tài xế, thời lượng nhắm mắt/ngáp, % CPU, RAM RSS, nhiệt độ SoC và cờ cảnh báo sụt áp nguồn.
   - Cho phép người vận hành ấn nút kích hoạt loa ESP32 và tinh chỉnh ngưỡng buồn ngủ ngay từ điện thoại/máy tính bảng qua WiFi.

---

## 2. Cài Đặt Tự Động 1 Chạm Trên Raspberry Pi 4

Chạy script cấu hình tự động từ thư mục gốc của repository:

```bash
chmod +x AI/deploy/setup_pi.sh
./AI/deploy/setup_pi.sh
```

Script sẽ tự động:
1. Cài đặt các gói hệ thống APT: `python3-venv`, `python3-pip`, `v4l-utils`, `libgl1`, `libglib2.0-0`, `udev`, `raspi-utils`.
2. Phân quyền phần cứng nhúng cho user hiện tại: `sudo usermod -aG dialout,video,input $USER`.
3. Khởi tạo môi trường ảo `.venv` và cài đặt các phụ thuộc ARM64 tối giản từ `AI/requirements-pi.txt`.
4. Tạo template cấu hình `AI/deploy/pi.env`.
5. Cài đặt và kích hoạt dịch vụ tự khởi động `driver-monitor.service`.

---

## 3. Kiểm Tra Nhanh Phần Cứng Tại Xe (1 Chạm)

Trước khi cho xe lăn bánh, chạy lệnh kiểm tra tự động 5 thành phần:

```bash
python AI/scripts/check_pi_env.py
```

Kết quả trả về:
- `[1/5]` Nhiệt độ chip SoC & Kiểm tra sụt áp nguồn tẩu sạc xe (Under-voltage).
- `[2/5]` Quyền truy cập Serial UART và Camera của User.
- `[3/5]` Kết nối và thu hình thử nghiệm từ Camera.
- `[4/5]` Tự dò cổng Serial và gửi thử bản tin bắt tay tới ESP32.
- `[5/5]` Nạp và kiểm tra khả năng suy diễn của YOLOv8 và MediaPipe.

---

## 4. Quản Lý Dịch Vụ Systemd Khi Xe Vận Hành

Dịch vụ tự động chạy khi xe bật khóa điện và tự khởi động lại sau 3s nếu có sự cố:

```bash
# Khởi động dịch vụ thủ công
sudo systemctl start driver-monitor

# Xem trạng thái vận hành
sudo systemctl status driver-monitor

# Theo dõi nhật ký thời gian thực
journalctl -u driver-monitor -f

# Dừng dịch vụ khi bảo trì
sudo systemctl stop driver-monitor
```

---

## 5. Truy Cập Web Dashboard Giám Sát Không Dây

Kết nối điện thoại hoặc máy tính bảng vào cùng mạng WiFi / Hotspot với Raspberry Pi, mở trình duyệt:

```text
http://<dia_chi_ip_pi>:8080
hoặc
http://raspberrypi.local:8080
```
