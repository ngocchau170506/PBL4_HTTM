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
2026-10-22 | G3-OPEN | scope/feature-map | Mở G3: tạo scope/G3.json, GIAI ĐOẠN HIỆN TẠI: G3, viết changes/PROPOSE-G3.md | MAIN
2026-10-22 | FEAT-032 | fd_response | Thêm FrameRingBuffer (timeline -2s/0s/kết thúc, chống tràn RAM), 9 test; Reviewer PASS | MAIN
2026-10-22 | FEAT-031 | fd_process/rules, fd_config | Thêm luật eye_drowsiness, mouth_yawn, rules_engine + util, mục rules trong config.json (ngưỡng CHUA_XAC_DINH), requirements.txt về stdlib; 20 test; MAIN duyệt merge, bỏ qua bước Reviewer riêng cho FEAT-031 | MAIN
2026-10-22 | FEAT-034 | fd_flow, fd_response | Nối rules_engine + ring_buffer vào FlowEngine (I-003/I-004 phương án b, tương thích ngược), thêm ts_of cho FrameRingBuffer; 6 test mới (98/98 PASS); MAIN duyệt merge | MAIN
2026-10-22 | FEAT-033 | fd_response | Thêm ServerDispatcher (outbox non-blocking, offline spool, circuit breaker, phát E_RESP_SEND/E_RESP_NO_ACK/E_RESP_BUFFER_OVERRUN); 6 test mới (104/104 PASS); MAIN duyệt merge | MAIN
2026-10-22 | FEAT-035 (I-008) | fd_config, fd_flow | Nạp & kiểm tra mục rules trong ConfigLoader; bổ sung build_rules_from_config, enable_rules_from_config và server_dispatcher vào FlowEngine (106/106 PASS); MAIN duyệt merge | MAIN
2026-10-22 | FEAT-036 (I-007) | fd_config, .gitignore | Tách mật khẩu RTSP khỏi config.json, thêm FD_CAMERA_URI/FD_CAMERA_PASSWORD, .env trong .gitignore, chặn mật khẩu thường (108/108 PASS); MAIN duyệt merge | MAIN
2026-10-22 | EXP-002 (30.5m) | workbench / changes | Hoàn tất kiểm thử bền bỉ pipeline G3 liên tục 30.5 phút (11.379M frames, 6,218 FPS, 2.276M ESP reqs, 56.8k lỗi, 0 leak, RTSP 100%), ghi hồ sơ changes/EXP-002.md & BENCHMARK-G3-30M-DATA.json | MAIN
2026-10-25 | FEAT-040/041/042 (G4) | fd_connect | Mở G4 (scope/G4.json): xây dựng SensorReplay, nâng cấp Esp32Source (giải mã cú pháp kép + phát hiện mất gói/trôi nhịp), xây dựng Esp32Commander (phát lệnh còi/loa hai chiều có Mutex và kiểm tra ACK chuẩn PBL4_HTTM-AI); 16 test mới (124/124 tests SẠCH) | MAIN
2026-10-26 | FEAT-050 (G5) | fd_eval, fd_config | Mở G5 (scope/G5.json): xây dựng MetricsCollector (Welford streaming stats, reservoir sampling, đo FPS/Latency P50-P99/Queue/RAM zero-leak) và nạp EvalConfig trong ConfigLoader; 6 test mới (136/136 tests SẠCH) | MAIN
2026-10-26 | FEAT-051 (G5) | fd_faultsim, workbench/faults | Xây dựng FaultInjector và 4 kịch bản YAML (network_drop, uart_degraded, camera_fps_drop, edge_contract_violation), tích hợp kiểm thử cùng ServerDispatcher, FlowEngine & MetricsCollector; 6 test mới (142/142 tests SẠCH) | MAIN
2026-10-26 | FEAT-052 (G5) | fd_diagnose | Xây dựng AutoReporter, EvaluationReport, DiagnosisFinding tự động tổng hợp báo cáo BENCHMARK-G5 (Markdown/JSON) và sinh hồ sơ nháp BUG-xxx chuẩn mẫu; 4 test mới (146/146 tests SẠCH) | MAIN
2026-10-26 | GATE-G5 | fd_eval, fd_faultsim, fd_diagnose | Nghiệm thu bền bỉ >20 phút luồng G5 (80k frames, 15.3k FPS, P95 0.08ms, 0 leak RAM, 4/4 kịch bản lỗi bắt 100%), lưu changes/BENCHMARK-G5.md; MAIN duyệt đóng GATE G5: PASS | MAIN
