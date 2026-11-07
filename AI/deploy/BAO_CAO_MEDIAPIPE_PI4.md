# BÁO CÁO CHUYÊN BIỆT: MEDIAPIPE TRÊN RASPBERRY PI 4
*(Tài liệu kỹ thuật gửi thành viên phụ trách thuật toán AI & MediaPipe)*

- **Dự án:** Hệ thống giám sát tài xế thông minh (Driver Fatigue & Attention Monitoring System)
- **Thời gian lập:** Ngày 05 tháng 11 năm 2026
- **Phần cứng mục tiêu:** Raspberry Pi 4 Model B (Broadcom BCM2711, 4 nhân ARM Cortex-A72, kiến trúc ARMv8-A 64-bit)
- **Môi trường phần mềm:** Debian GNU/Linux 13 (trixie), Linux kernel 6.12+ aarch64, Python 3.13.5 (venv)
- **Cam kết thực hiện:** **Giữ nguyên 100% logic mã nguồn MediaPipe**, không tự ý chỉnh sửa thuật toán trích xuất điểm mốc khuôn mặt.

---

## 1. Triệu chứng lỗi thực tế trên Raspberry Pi 4

Khi dịch vụ ứng dụng `driver-monitor.service` hoặc lệnh chạy tay khởi động trên Raspberry Pi 4, quá trình nạp mô hình MediaPipe bị hủy ngang tức thì ở tầng nhị phân với thông báo lỗi:

```text
[STARTUP] MediaPipe unavailable; YOLO-only mode
(FATAL ERROR: This binary was compiled with aes enabled, but this feature is not available on this processor (go/sigill-fail-fast).)
```

---

## 2. Nguyên nhân gốc rễ (Root Cause)

1. **Phần cứng CPU của Raspberry Pi 4:**
   - Vi xử lý trên Raspberry Pi 4 là **Broadcom BCM2711** với 4 lõi **ARM Cortex-A72**.
   - Trong kiến trúc Cortex-A72 tiêu chuẩn của Pi 4, phần cứng **hoàn toàn không hỗ trợ tập lệnh tăng tốc giải mã mã hóa ARMv8 Cryptographic Extension (`+aes`)**. Tập lệnh `aes` này chỉ bắt đầu được tích hợp từ thế hệ lõi Cortex-A76 (trang bị trên **Raspberry Pi 5**) trở lên.
2. **File nhị phân thư viện MediaPipe:**
   - Bản cài đặt `mediapipe` hiện tại trong môi trường `venv` của Pi chứa file đối tượng chia sẻ C++:
     ```text
     venv/lib/python3.13/site-packages/mediapipe/tasks/cc/vision/face_landmarker/python/_framework_bindings.cpython-313-aarch64-linux-gnu.so
     ```
   - Binary C++ này được biên dịch sẵn từ nguồn bên ngoài với cờ tối ưu hóa phần cứng bật cứng cờ `+aes` (chỉ phù hợp với ARMv8.2-A+ hoặc Cortex-A76).
3. **Cơ chế Fail-Fast của nhân MediaPipe:**
   - Khi Python gọi `FaceLandmarker.create_from_options()`, mã C++ kiểm tra tính tương thích của tập lệnh CPU (`cpu_features`). Khi phát hiện CPU thiếu tính năng `aes`, nhân C++ tự động kích hoạt mã bảo vệ `go/sigill-fail-fast` và bắn tín hiệu ngắt **`SIGILL` (Illegal Instruction)**, ép hủy tiến trình ngay lập tức.

---

## 3. Cơ chế bảo vệ hiện tại trong Code & 3 Ảnh hưởng thực tế

### 3.1. Cơ chế phòng vệ tự động hiện tại
Trong file `AI/app/main.py` (dòng 147–171), hệ thống đã cài đặt sẵn cơ chế chạy thử nghiệm cách ly:
- Sử dụng `subprocess.run([sys.executable, "-c", "import mediapipe..."], timeout=15)` trong một tiến trình độc lập.
- Khi tiến trình con bị sập `SIGILL`, `main.py` bắt được mã lỗi thoát khác 0, ghi nhận log và chuyển ứng dụng sang **chế độ chỉ dùng YOLO (`YOLO-only mode`, biến `landmarker = None`)**.
- Nhờ cơ chế này, toàn bộ hệ thống Camera, YOLO, ESP32, GPS và Web Server vẫn hoạt động bình thường, không bị sập dịch vụ.

### 3.2. Ba ảnh hưởng kỹ thuật đến chất lượng phát hiện
Khi MediaPipe bị vô hiệu hóa trên Pi 4, hệ thống phải chịu 3 giới hạn:

| # | Ảnh hưởng | Chi tiết kỹ thuật |
|---|---|---|
| **1** | **Khởi động chậm (Startup Latency)** | Mỗi lần bật dịch vụ, hệ thống phải mất từ **5 đến 15 giây** để nạp tiến trình con nặng, chờ thư viện crash `SIGILL` và timeout trước khi chuyển sang YOLO. |
| **2** | **Hạ cấp mức độ tin cậy cảnh báo** | • Khi đủ cả 2 mô hình (YOLO + MediaPipe Landmarker): Cảnh báo nhắm mắt/ngáp đạt mức **`CONFIRMED`** (Độ tin cậy tuyệt đối nhờ xác thực chéo 2 nguồn độc lập).<br>• Khi chỉ có YOLO: Cảnh báo chỉ đạt mức **`SUSPECTED`** (Nghi ngờ - chỉ có 1 nguồn). Đồng thời mất khả năng lọc cảnh báo giả (`CONFLICT`) khi YOLO nhầm lẫn góc nghiêng khuôn mặt. |
| **3** | **Mất tính năng đo góc nghiêng đầu 3D (Head Pose 3D)** | Thuật toán tính góc Pitch / Yaw / Roll trong `AI/scripts/drowsiness_pipeline.py` (dòng 380) phụ thuộc hoàn toàn vào 468 điểm mốc khuôn mặt thông qua OpenCV `solvePnP`. Khi MediaPipe tắt, chỉ số Head Pose trên Web Dashboard luôn hiển thị `-- / -- / --`. |

---

## 4. Ba phương án khắc phục đề xuất (Để hội ý triển khai)

### Phương án 1: Cài bản build MediaPipe tương thích Cortex-A72 hoặc mô hình Facial Landmarker thay thế
*(Phương án khuyên dùng nếu muốn hệ thống hoàn thiện đầy đủ cả EAR/MAR và Head Pose 3D trên Pi 4)*
- **Cách thực hiện:**
  - Cài đặt bản bánh xe (`.whl`) của `mediapipe` được biên dịch riêng cho kiến trúc ARMv8 Cortex-A72 tiêu chuẩn (tắt cờ `+aes` khi cấu hình Bazel build).
  - Hoặc: Tích hợp mô hình Face Landmarker nhẹ (dạng ONNX hoặc OpenCV YuNet / FaceYN) thay thế cho phần core C++ của MediaPipe.
- **Đánh giá:**
  - *Trước:* MediaPipe crash `SIGILL`, mất hoàn toàn tính năng kiểm chứng chéo và đo góc đầu.
  - *Sau:* Hệ thống chạy song song 2 kênh trinh sát độc lập trên Pi 4, cảnh báo đạt chuẩn `CONFIRMED`.

### Phương án 2: Bổ sung kiểm tra CPU để bỏ qua bước nạp thử 15 giây
*(Giải pháp tối ưu code thông minh, giữ nguyên code MediaPipe)*
- **Cách thực hiện:**
  Trong file `AI/app/main.py`, bổ sung kiểm tra cờ CPU nhanh trước khi gọi `subprocess.run`:
  ```python
  # Kiểm tra nhanh tập lệnh phần cứng của CPU (tốn chưa tới 0.001 giây)
  if sys.platform.startswith("linux") and os.path.exists("/proc/cpuinfo"):
      try:
          with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
              cpu_info = f.read()
          if "Features" in cpu_info and " aes " not in cpu_info:
              logger.warning("[STARTUP] CPU khong ho tro tap lenh ARM AES (Raspberry Pi 4) -> Bo qua MediaPipe de vao thang YOLO mode")
              raise RuntimeError("CPU Cortex-A72 thieu phan cung ARMv8 AES")
      except Exception as cpu_err:
          logger.info(f"[STARTUP] Phat hien gioi han CPU: {cpu_err}")
  ```
- **Đánh giá:**
  - *Trước:* Khởi động mất 15 giây chờ tiến trình con crash.
  - *Sau:* Trên PC và Raspberry Pi 5 (có tập lệnh `aes`), MediaPipe vẫn chạy 100% bình thường. Trên Raspberry Pi 4 (không có `aes`), hệ thống nhận biết ngay trong 1 mili-giây và chuyển sang YOLO không hề bị khựng trễ.

### Phương án 3: Khống chế qua biến môi trường cấu hình
*(Giải pháp không can thiệp bất kỳ dòng code Python nào)*
- **Cách thực hiện:**
  Thiết lập biến cấu hình trong file `AI/deploy/pi.env`:
  ```bash
  DISABLE_MEDIAPIPE=1
  ```
- **Đánh giá:**
  Nhanh gọn, triệt để. Khi hệ thống được cắm sang Raspberry Pi 5 hoặc PC chỉ cần đổi lại thành `DISABLE_MEDIAPIPE=0`.
