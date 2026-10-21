2026-10-19 | FEAT-002 | fd_config | Khởi tạo scope/G1.json và bộ nạp cấu hình ConfigLoader chuẩn R4 | MAIN
2026-10-19 | FEAT-010 | fd_connect | Xây dựng CameraSource phát RawPacket và bắt E_CONN_OPEN/LOST/TIMEOUT | MAIN
2026-10-19 | FEAT-011 | fd_signal | Xây dựng FrameNormalizer chuẩn hóa RawPacket sang SignalPacket | MAIN
2026-10-19 | FEAT-013 | fd_process/ai | Xây dựng cổng run_ai và StubAdapter chuẩn R6 | MAIN
2026-10-19 | FEAT-012/014 | fd_flow/diagnose | Kiểm thử công suất 210,000 frames đạt 6,138 FPS, 0 leak; lưu hồ sơ changes/BENCHMARK-G1.md | MAIN
2026-10-21 | FEAT-020 | fd_process/sampler | Mở scope/G2.json; xây dựng FpsThrottle lọc nhịp theo timestamp (25fps->5fps), cấu hình RTSP Camera 192.168.1.210 & COM3 | MAIN
2026-10-21 | FEAT-021/022 | fd_process/baseline | Xây dựng BaselineCollector (5-10s -> LOCKED) và BaselineStorage (lưu/đọc & cách ly theo driver_id) | MAIN
2026-10-21 | GATE-G2 | fd_flow | Nối FpsThrottle và BaselineCollector vào FlowEngine (step_sampled); kiểm định 10 tài xế & 10/25/30/60 FPS đạt GATE G2: PASS | MAIN
2026-10-21 | FEAT-025 (G2.5) | fd_connect/fd_signal/fd_flow | Xây dựng Esp32Source qua cổng COM3 (chưa gắn GPS -> DEGRADED trên cạnh optional, bảo toàn 100% luồng Camera) | MAIN
2026-10-21 | EXP-001 / FEAT-002 | fd_config / changes | Gộp 4 tính năng vượt trội từ test_config_loader_1 vào test_config_loader.py & config_loader.py, xóa test_config_loader_1.py, ghi hồ sơ EXP-001.md & dọn README.md | MAIN
2026-10-21 | EXP-001 (30.5m) | workbench / changes | Hoàn tất kiểm thử bền bỉ 30.5 phút (9.689M frames, 1.937M ESP reqs, 29k lỗi, 0 leak), cập nhật EXP-001.md & BENCHMARK-G2.md, khôi phục và bảo toàn trọn vẹn vị trí G2 | MAIN
