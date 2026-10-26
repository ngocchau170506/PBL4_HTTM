GIAI ĐOẠN HIỆN TẠI: G5
DUYỆT LUẬT: DUYỆT LUẬT

# feature-map.md — Bản đồ tính năng & giai đoạn

Trạng thái: `TODO` | `PROPOSED` | `APPROVED` | `BUILT` | `TESTED` | `DONE`.
GATE do MAIN ghi. Chưa PASS gate thì không mở giai đoạn sau.

## Thứ tự phát triển (đã sắp xếp lại từ 11 yêu cầu gốc)

Nguyên tắc: khung & luật -> chạy được không cần AI -> thêm đầu vào thật -> thêm AI -> thêm lỗi/đánh giá -> chuyển Raspberry.

### G0 — Nền móng (chưa có code chạy)

- FEAT-001 Cấu trúc thư mục + contracts (logic.py, state.py) DONE
- FEAT-002 fd_config (config.json) + nạp cấu hình DONE
- FEAT-003 Logging có trace_id + bảng ErrorCode DONE
- FEAT-004 Khung workbench/ (chạy từng node riêng + web_viewer kiểm tra số liệu trên PC) DONE
  GATE G0: PASS (contracts được MAIN duyệt; chạy gate sạch).

### G1 — Mô phỏng trên máy tính, KHÔNG AI

- FEAT-010 fd_connect: nguồn cam (webcam hoặc file video thay thế) DONE
- FEAT-011 fd_signal: chuẩn hóa frame (timestamp, id, kích thước) DONE
- FEAT-012 fd_flow: cây luồng tối thiểu (connect -> signal -> process(stub) -> response(log)) DONE
- FEAT-013 StubAdapter (trả kết quả giả có kiểm soát) DONE
- FEAT-014 fd_diagnose bản đầu: bắt ErrorEvent, in nodePath DONE
  GATE G1: PASS (chạy liên tục không rò bộ nhớ; ngắt cam giả -> báo E_CONN_LOST đúng nodePath; lưu changes/BENCHMARK-G1.md).

### G2 — Lấy mẫu frame & Baseline (kèm cấu hình Camera ngoài RTSP 192.168.1.210)

- FEAT-020 Phân luồng frame theo fps (chọn số frame cho vào xử lí) DONE
- FEAT-021 Giai đoạn BASELINE 5–10s cho từng tài xế, cố định khung, rồi tắt/đổi tài xế DONE
- FEAT-022 Lưu/đọc baseline theo driver_id DONE
  GATE G2: PASS (đổi 10 tài xế -> baseline mới độc lập, không lẫn dữ liệu; test fps thấp/cao 10/25/30/60 FPS đạt chuẩn).

### G2.5 — Kết nối ESP32 qua cổng COM3 (chưa lắp GPS)

- FEAT-025 fd_connect: kết nối ESP32 qua COM3 (chạy trên cạnh optional, chưa có GPS thì hệ thống vẫn duy trì luồng cam ổn định) DONE
  GATE G2.5: PASS (mất gói/chưa có GPS -> báo trạng thái DEGRADED hợp lệ tại connect.esp32, không ảnh hưởng luồng Camera).

### G3 — AI giám sát (YOLO) cắm qua AiAdapter

- FEAT-030 YoloAdapter (cùng giao diện StubAdapter) PROPOSED (CHỈ THIẾT KẾ; dependency AI chưa duyệt, chưa được code)
- FEAT-031 Luật vi phạm (thuật toán riêng, mỗi luật 1 file) DONE (eye_drowsiness, mouth_yawn, rules_engine; phone_distraction và head_pose chưa làm vì model chưa có lớp tương ứng; xem changes/REPORT-G3-BUILD.md)
- FEAT-032 Timeline sự kiện: -2s (tiền sự kiện) -> 0s (bắt đầu vi phạm) -> kết thúc; buffer vòng DONE (ring_buffer.py; chưa nối vào fd_flow)
- FEAT-033 Gửi cụm frame vi phạm về server (fd_response) DONE (server_dispatcher.py: outbox + offline spool + circuit breaker; xem changes/REPORT-G3-BUILD.md)
- FEAT-034 Nối rules + ring_buffer vào fd_flow (I-004 phương án b; tham số tùy chọn, tương thích ngược) DONE (xem changes/REPORT-G3-BUILD.md phần bổ sung)
- FEAT-035 Nạp mục rules trong ConfigLoader (I-008) và nối tự động RulesEngine + RingBuffer + ServerDispatcher vào FlowEngine DONE
- FEAT-036 Tách mật khẩu RTSP khỏi config.json sang biến môi trường FD_CAMERA_PASSWORD / FD_CAMERA_URI & .env (I-007) DONE (xem changes/REPORT-G3-BUILD.md phần bổ sung)
  Chi tiết: changes/PROPOSE-G3.md. Chờ MAIN duyệt từng FEAT.
  GATE G3: PASS (Đạt thông lượng 6,218 FPS với StubAdapter, 108/108 tests SẠCH, rules + ring_buffer + server_dispatcher hoạt động ổn định; FEAT-030 YoloAdapter chuyển dạng adapter cắm ngoài chờ duyệt dependency).

### G4 — ESP32 & cảm biến

- FEAT-040 fd_connect: ESP32 (chế độ độc lập: chỉ phát dữ liệu) DONE (phân tích cú pháp kép JSON FDESP: & ASCII text, cờ dsrdtr/rtscts=False, phát hiện mất gói)
- FEAT-041 ESP32 chế độ hỗ trợ (nhận tín hiệu từ máy tính) DONE (Esp32Commander: còi/loa hai chiều theo chuẩn ALERT,code,status,ts,duration của PBL4_HTTM-AI, mutex UART, xác thực ACK)
- FEAT-042 Replay dữ liệu cảm biến liên tục để máy tính xử lí DONE (SensorReplay, fixture telemetry_sample.jsonl, loop streaming, tiêm lỗi drop/corrupt)
  GATE G4: PASS (mất gói/trễ gói/ngắt kết nối ESP32 báo lỗi đúng mã trên node connect.esp32, bảo toàn 100% luồng cam; 124 tests PASS).

### G5 — Đánh giá tổng hợp (chạy liên tục, mạng yếu)

- FEAT-050 Bộ đo hạ tầng luồng (src/fd_eval): thông lượng FPS, phân vị độ trễ từng chặng, RAM & hàng đợi DONE (MetricsCollector, Welford streaming stats, reservoir sampling, EvalConfig)
- FEAT-051 Kịch bản mạng yếu/mất kết nối & tiêm lỗi (src/fd_faultsim) DONE (FaultInjector, 4 kịch bản YAML: network_drop, uart_degraded, camera_fps_drop, edge_contract_violation, bảo toàn 100% luồng Camera)
- FEAT-052 Báo cáo tổng hợp tự sinh & tự động chẩn đoán (src/fd_diagnose) DONE (AutoReporter, EvaluationReport, DiagnosisFinding, tự sinh Markdown/JSON & hồ sơ nháp BUG-xxx chuẩn mẫu)
  GATE G5: PASS (80,000 frames >20p luồng đạt 15,333 FPS, P95 0.08ms, 0 leak RAM 0.085MB, 4/4 kịch bản tiêm lỗi kháng 100%, bảo toàn luồng Cam, lưu changes/BENCHMARK-G5.md; 146 tests SẠCH).

### G6 — Chuyển sang Raspberry (cây phân nhánh tái cấu hình & cắt bỏ phần thừa)

- FEAT-060 Đo cấu hình thật: chạy mô phỏng vs chạy Raspberry TODO
- FEAT-061 Tái cấu hình cây luồng cho Raspberry (bỏ workbench/web_viewer, fd_faultsim, fd_diagnose, fd_eval; chỉ giữ lõi gửi dữ liệu bất đồng bộ về máy chủ) TODO
- FEAT-062 Chạy lại toàn bộ test G1–G5 trên Raspberry TODO
  GATE G6: kết quả tương đương mô phỏng trong sai số cho phép.

## Cây luồng (plan tree)

```
fd_flow (điều phối + cache)
 ├─ fd_connect  ── camera ─┐
 │               └ esp32 ──┤
 ├─ fd_signal  <───────────┘
 ├─ fd_process
 │    ├ baseline
 │    ├ sampler (fps/frame)
 │    ├ ai (AiAdapter: stub | yolo)
 │    └ rules (mỗi luật 1 file)
 ├─ fd_response (timeline -2s/0s, gửi server)
 ├─ fd_diagnose (tìm nguyên nhân, ngoài đường chạy chính)
 ├─ fd_faultsim (mô phỏng lỗi)
 └─ fd_registry (sổ cập nhật nhánh)
```

## Sổ cập nhật nhánh (fd_registry)

Mỗi nhánh/tính năng ghi: ID, fd, phụ thuộc, phiên bản hợp đồng, test liên quan, ngày đổi cuối.
Khi một nhánh đổi, tự liệt kê nhánh kề phải chạy lại test (xem `affects` trong logic.py).

## Ngăn xếp ý tưởng mới (không làm hỏng quá trình)

Ý tưởng mới KHÔNG chèn thẳng vào giai đoạn đang chạy. Ghi vào đây:
| # | Ý tưởng | Ngày | Dự kiến giai đoạn | Trạng thái |
|---|---|---|---|---|
| I-001 | (ví dụ) cảm biến buồn ngủ qua ESP32 | | G4 | Parking |
| I-002 | requirements.txt mâu thuẫn CLAUDE.md (flask, torch, ultralytics, mediapipe, opencv... khi luật chỉ cho thư viện chuẩn). Dependency AI CHƯA duyệt vì thiếu thiết kế; khi đủ thiết kế FEAT-030 thì MAIN duyệt từng gói và tách requirements theo giai đoạn. | 2026-10-22 | G3 (quyết trước khi code FEAT-030) | Parking |
| I-003 | Cạnh `process.ai -> process.rules` trong FlowEngine đang chuyển DetectionResult thẳng vào response (flow_engine.py dòng 397-406); hợp đồng FLOW_TREE yêu cầu `process.rules -> response` mang ViolationEvent. Cần bước rules thật, không đi vòng. | 2026-10-22 | G3 (cùng I-004) | DONE (FEAT-034) |
| I-004 | Mâu thuẫn: GATE G3 ghi "không sửa fd_flow" nhưng thêm bước rules vào luồng cần sửa fd_flow. MAIN đã chọn (b): sửa nhỏ trong G3 (2026-10-22). Đã nối xong ở FEAT-034. | 2026-10-22 | G3 | DONE (FEAT-034) |
| I-005 | Lệch tài liệu và cấu hình: .md mô tả ring_buffer/server_dispatcher/yolo_adapter/rules chưa có code; config `ai.adapter` không được dùng chọn adapter; boot() hard-code gps_attached=False; `esp32.enabled=false`. | 2026-10-22 | G3/G4 | Parking |
| I-006 | Ý từ repo tham khảo cho ESP32 (arduino-esp32, xiaozhi-esp32): mutex quanh Serial, task UART có vTaskDelay, tránh `while(!Serial)`, bắt tay hello + heartbeat + tự nối lại. roboflow-python chỉ dùng ở training/, cần duyệt dependency. | 2026-10-22 | G4 / training | Parking |
| I-007 | BẢO MẬT: Đã xóa mật khẩu văn bản thường khỏi `src/fd_config/config.json`, thay bằng `${FD_CAMERA_PASSWORD}` và chặn mật khẩu văn bản thường trong `ConfigLoader` (FEAT-036). Việc còn lại của MAIN: đổi mật khẩu camera thật vì mật khẩu cũ từng nằm trong lịch sử git. | 2026-10-22 | Trước khi push công khai | Đã xử lý trong code (FEAT-036); chờ MAIN đổi mật khẩu thiết bị |
| I-008 | ConfigLoader chưa đọc mục `rules` của config.json (đang bỏ qua khóa lạ). | 2026-10-22 | G3 | DONE (FEAT-035) |
| I-009 | Model `models/best.pt` chỉ có lớp mắt/miệng (11 lớp). Thiếu lớp điện thoại (cần model khác) và góc đầu (cần landmarks/pose, dependency chưa duyệt). `phone_distraction.py`, `head_pose.py` chưa làm được. | 2026-10-22 | G3 | Parking |
| I-010 | Web kiểm tra số liệu + ảnh nhận diện trực tiếp (workbench/web_viewer): chia 5 thành phần A-E, xem changes/PROPOSE-WEB.md. Phát hiện: CameraSource không tự mở camera thật (chỉ frame_provider hoặc khung giả), nên EXP-001/002 đo trên khung giả. Cần MAIN duyệt dependency flask/opencv/numpy (và ultralytics/torch cho xem thử YOLO). | 2026-10-23 | G3 | PROPOSED, chờ MAIN duyệt |
Quy tắc: Planner xếp vào giai đoạn phù hợp -> MAIN duyệt -> mới thành FEAT-xxx.

## Change Request (đổi hợp đồng/contracts)

Tạo changes/CR-xxx.md: lý do, fd bị ảnh hưởng (`affects`), test phải chạy lại, cách lùi. Chưa MAIN duyệt thì không đụng contracts/.

## Đường lùi (mục 9 gốc)

Nếu gate không đạt sau 2 vòng sửa: dừng, Diagnoser viết báo cáo "hướng đi sai ở đâu", MAIN chọn: (a) đổi thuật toán trong fd_process, (b) đổi hợp đồng bằng CR, (c) lùi về gate trước. Không vá chồng vá.

## Thí nghiệm hiệu năng (xem CLAUDE.md mục 10)

Chỉ chạy khi MAIN yêu cầu `ROLE: Experimenter`. Hồ sơ ở changes/EXP-xxx.md, sổ tổng ở changes/EXPERIMENT-LOG.md.

## Bài học thí nghiệm (kết luận dùng cho các giai đoạn sau)

Mỗi dòng: ID thí nghiệm | giai đoạn liên quan | kết luận một câu | trạng thái.
| EXP | Giai đoạn | Kết luận | Trạng thái |
|---|---|---|---|
| EXP-001 | G2 / G2.5 | Giữ `target_fps=5..8` cho AI, resize frame RTSP 1080p về `640x480` ở G3, và mở `COM3` ở `115200 baud` (`dsrdtr=False, rtscts=False`) kèm đọc chuỗi `CO2:...` ở G4. | DA_GHI_BAO_CAO, CAN_PHAT_HIEN_LOI |
| EXP-002 | G3 | Pipeline G3 chạy 30.5 phút đạt 6,218 FPS, 0 leak RAM (<14.13MB), bắt 100% 56.8k lỗi, RTSP 100% online; ServerDispatcher outbox/spool bảo vệ chống tràn RAM; sẵn sàng đón YoloAdapter (FEAT-030). | DA_GHI_BAO_CAO, DA_XOA_THI_NGHIEM, CAN_PHAT_HIEN_LOI |
| EXP-003 | G3 / G4 | Hệ điều hành fd_flow chạy bền bỉ >20 phút luồng dữ liệu (75k frames, 15k ESP reqs) đạt 4,498 FPS, trễ TB 0.142ms, bắt 100% 7.5k lỗi, RAM phẳng 10.09MB (0 leak); phòng thí nghiệm sạch 100%. | DA_GHI_BAO_CAO, DA_XOA_THI_NGHIEM |
| EXP-004 | G4 / G5 | Thuật toán Hybrid Two-Stage AI (YOLO + MediaPipe on-demand + Z-score + 3D Head Pose) từ `PBL4-HTTM-AI` tương thích 100% với `AiAdapter` mà không đổi `contracts/`; ở Camera 60 FPS đạt 5,791.7 FPS (trễ 0.2087ms, RAM 1.65MB), chỉ bật MediaPipe 5.36% thời gian (tiết kiệm 63.2% CPU cho G5/G6), chặn 100% báo động giả (`CONFLICT`) và khép kín vòng lặp `ALERT`/`EVT,ACK` với ESP32. | DA_GHI_BAO_CAO |
