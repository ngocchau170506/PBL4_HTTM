# [C-RESPONSE] PHẢN ỨNG CỤC BỘ & GIAO TIẾP MÁY CHỦ (CHỐNG NGHẼN RASPBERRY PI)

## 1. Cảnh báo cục bộ & Bộ nhớ vòng Timeline (`-2s -> 0s -> kết thúc`)
- `local_alert.py`: Kích hoạt còi báo, buzzer qua GPIO/ESP32 ngay tức thì khi xác nhận vi phạm.
- `ring_buffer.py`: Bộ nhớ vòng trên RAM (giới hạn cố định số frame theo `buffer_frames` trong `config.json` để chống tràn RAM trên Raspberry Pi). Luôn giữ liên tục `-2s` (`pre_event_ms: 2000`) trước thời điểm `t0_ms` (0s bắt đầu vi phạm) cho đến khi kết thúc sự kiện.

## 2. Giao tiếp Máy chủ bất đồng bộ (`server_dispatcher.py`)
Thiết kế đặc biệt để **Raspberry Pi chạy ổn định tuyệt đối khi kết nối với Máy chủ**:
- **Không bao giờ upload trực tiếp trên luồng xử lý ảnh (Non-blocking Outbox Queue):** Luồng Camera và AI chỉ đẩy sự kiện `ViolationEvent` đã đóng gói vào hàng đợi nền rồi chạy tiếp ngay, không bao giờ đứng chờ mạng.
- **Không cần Web cục bộ trên Raspberry Pi:** Ở chế độ `mode="rasp"`, thiết bị chỉ đóng gói dữ liệu JSON Manifest + cụm frame vi phạm gửi về Máy chủ (do bên Server tiếp nhận và hiển thị).
- **Chịu lỗi đứt mạng / Máy chủ quá tải (Offline-Spool & Circuit Breaker):** Nếu mạng yếu hoặc Máy chủ không phản hồi (`E_RESP_SEND`, `E_RESP_NO_ACK`), gói sự kiện được lưu tạm có giới hạn dung lượng và tự động gửi bù khi đường truyền ổn định, tuyệt đối không làm treo hay sập tiến trình giám sát trên xe.
