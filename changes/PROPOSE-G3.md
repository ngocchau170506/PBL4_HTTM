# PROPOSE-G3 — Giai đoạn G3 (AI giám sát)

Vai: Planner. Ngày: 2026-10-22. Trạng thái: PROPOSED, chờ MAIN duyệt từng FEAT (chưa viết code).
Ràng buộc chung: chỉ thư viện chuẩn; không sửa `src/contracts/`; không sửa `requirements.txt`; mỗi lượt Builder 1 component; mỗi FEAT phải qua `python workbench/gate.py` SẠCH trước REPORT.

## Thứ tự đề xuất

FEAT-032 -> FEAT-031 -> FEAT-033 -> (FEAT-030 chỉ thiết kế). Lý do: ring buffer là nền cho luật và bằng chứng, luật dùng DetectionResult của StubAdapter nên không cần model thật, dispatcher dùng ViolationEvent + cụm frame từ hai bước trước.

## FEAT-032 — Timeline & buffer vòng (`src/fd_response/ring_buffer.py`)

- Việc: `FrameRingBuffer` (deque maxlen theo `response.buffer_frames`), giữ khung theo ts; `extract(t0_ms, pre_ms)` trả frame_id trong [t0-pre_ms, t0]; theo dõi pha WATCHING/SUSPECT/ACTIVE/CLOSED đúng `VIOLATION_TRANSITIONS`.
- File đụng: `src/fd_response/ring_buffer.py`, `workbench/tests/test_ring_buffer.py`, `src/fd_response/response.md`.
- Vào/Ra: SignalPacket (chỉ lưu frame_id + ts, không lưu ảnh thô) / tuple frame_id.
- Lỗi: `E_RESP_BUFFER_OVERRUN` khi vượt giới hạn mà frame cần giữ bị đẩy mất.
- Rủi ro: không có timestamp đồng nhất giữa frame; xử lý bằng `ts_ms` từ signal. Chuyển pha sai bảng -> lỗi, không bỏ qua.
- Test: 100.000 nhịp không tăng bộ nhớ; pre-event đúng 2000ms ở 5fps (10 frame); pha sai bị từ chối.

## FEAT-031 — Luật vi phạm (`src/fd_process/rules/`, mỗi luật 1 file)

- Việc: `eye_drowsiness.py`, `mouth_yawn.py`, `head_pose.py`, `phone_distraction.py`, cộng `rules_engine.py` (điều phối: nhận DetectionResult, gọi từng luật, phát ViolationEvent). Ngưỡng thời gian đọc từ `fd_config` (R4), không hard-code; cần thêm khóa cấu hình -> xin MAIN ghi đè `config.json`.
- Mỗi luật là hàm/lớp thuần: vào chuỗi Detection theo thời gian (+BaselineProfile cho head_pose), ra True/False + thời điểm. Không đọc camera, không gọi server.
- Nhãn Detection (`eye_closed`, `yawn`, `phone`...) CHƯA xác định vì chưa rõ lớp của `models/best.pt`; Builder phải dùng nhãn do StubAdapter bơm, bảng nhãn thật do MAIN cung cấp ở FEAT-030.
- Phụ thuộc quyết định I-004: nối `rules_engine` vào luồng cần sửa `fd_flow`. Cho tới khi MAIN chọn, Builder chỉ làm luật + test độc lập trong workbench, KHÔNG sửa fd_flow.
- File đụng: `src/fd_process/rules/*.py`, `workbench/tests/test_rules_*.py`, `src/fd_process/rules/rule.md`.
- Test: chớp mắt <0.4s không báo; nhắm >ngưỡng báo đúng t0; mất frame giữa chừng không báo giả; mỗi luật độc lập.

## FEAT-033 — Gửi cụm frame về server (`src/fd_response/server_dispatcher.py`)

- Việc: hàng đợi gửi nền có giới hạn (non-blocking), spool tạm có giới hạn, circuit breaker; phát `E_RESP_SEND`/`E_RESP_NO_ACK`. Kênh gửi qua giao diện trừu tượng (stdlib `urllib`/socket), kiểm thử bằng server giả trong workbench.
- Mở mạng thật cần MAIN cho phép riêng (CLAUDE.md mục 6). Mặc định test chỉ dùng server giả cục bộ.
- File đụng: `src/fd_response/server_dispatcher.py`, `workbench/tests/test_server_dispatcher.py`, `src/fd_response/response.md`.
- Test: server chậm/rớt, spool đầy, gửi bù khi mạng hồi, luồng xử lý không bao giờ chờ mạng.

## FEAT-030 — YoloAdapter: CHỈ THIẾT KẾ (chưa code)

- Hiện thực `AiAdapter` (`name="yolo"`, `load/infer/health/self_test`), đi qua `run_ai`; lỗi nạp -> `E_PROC_MODEL_LOAD`, thiếu thư viện -> không crash, health DOWN.
- Trước khi code, MAIN cần cung cấp: (1) danh sách lớp của `models/best.pt` và ánh xạ sang nhãn luật; (2) quyết định dependency (ultralytics/torch/opencv/mediapipe, từng gói); (3) kích thước đầu vào 640x480 (resize ở `fd_signal`, theo EXP-001); (4) ngưỡng FPS 5-8 và timeout 500ms cho thiết bị đích.
- Giữ `StubAdapter` làm mặc định; `config.ai.adapter` chọn adapter phải là việc riêng (I-005).

## Điểm cần MAIN quyết trước khi Builder bắt đầu

1. Duyệt thứ tự FEAT-032 -> 031 -> 033.
2. I-004: nối rules vào fd_flow bằng CR hay sửa nhỏ trong G3.
3. Cho phép thêm khóa ngưỡng luật vào `src/fd_config/config.json`.
4. Bảng lớp của `models/best.pt`.

## Rủi ro chung

Gate G3 yêu cầu "số liệu đánh giá cơ bản" nhưng FEAT-030 chưa code nên chưa đo được suy luận YOLO thật; phần số liệu của G3 chỉ hoàn tất khi dependency được duyệt.
