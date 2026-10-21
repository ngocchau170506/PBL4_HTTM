# Sổ thí nghiệm hiệu năng / chất lượng

Mỗi thí nghiệm một dòng, cập nhật khi trạng thái đổi. Chi tiết nằm ở `EXP-xxx.md`. Mã trạng thái xem CLAUDE.md mục 10.

| EXP | Ngày | Giai đoạn | Mục tiêu (MAIN) | Trạng thái | Kết luận ngắn |
|---|---|---|---|---|---|
| EXP-001 | 2026-10-21 | G2 / G2.5 | Chạy kiểm tra bền bỉ trên 30 phút (>1800s): max Throughput, độ trễ, FPS jitter, dồn tải ESP32 COM3, độ trễ loa, AI compatibility, kháng 29k lỗi, test phần cứng thật | DA_GHI_BAO_CAO, CAN_PHAT_HIEN_LOI | Chạy 30.5 phút xử lý 9.689M frames (5,294 FPS, trễ 0.107ms), 1.937M ESP32 reqs (0.190ms), 484k alerts (0.081ms), bắt 29,067 lỗi (100%), RAM 12.25MB (0 leak); Cam RTSP 1080p cần resize 640x480 ở G3; ESP32 115200 baud cần tắt DTR/RTS và thêm GPS ở G4. |
