# [C-FD-CONNECT] NGUỒN DỮ LIỆU VIDEO & GỬI RAW PACKET

- `camera_source.py`: Đọc frame từ Video/Webcam/RTSP -> phát RawPacket (sensor_kind='camera').
- `esp32_source.py`: Đọc dữ liệu cảm biến CO2, rung lắc, GPS từ UART/Serial/Replay -> phát RawPacket (sensor_kind='esp32'). Phân tích cú pháp kép FDESP: & ASCII text, bảo vệ CP210x bằng cờ dsrdtr/rtscts=False, theo dõi mất gói.
- `esp32_commander.py`: Kênh lệnh cảnh báo hai chiều PC -> ESP32 theo chuẩn ALERT,code,status,ts,duration của PBL4_HTTM-AI, khóa Mutex chống xung đột UART, kiểm tra bắt tay xác nhận ACK.
- `sensor_replay.py`: Bộ phát lại dữ liệu cảm biến từ fixture JSONL, hỗ trợ loop streaming và tiêm lỗi mô phỏng (drop, corrupt, delay).
