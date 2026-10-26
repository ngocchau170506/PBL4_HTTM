# PROPOSE-WEB — Web kiểm tra số liệu và ảnh nhận diện trực tiếp (workbench/web_viewer)

Vai: Planner. Ngày: 2026-10-23. Trạng thái: PROPOSED, chờ MAIN duyệt (chưa viết code, chưa gắn FEAT-ID; MAIN gắn ID).
Giai đoạn: G3 (phục vụ gate G3 "có số liệu đánh giá cơ bản"). Không mở giai đoạn mới.

## Mục tiêu (theo yêu cầu MAIN)
Có một trang web chạy trên PC để MAIN tự đứng trước camera, nhìn trực tiếp: ảnh có khung nhận diện, số liệu từng phần (camera, ESP32, baseline, luật, lỗi), rồi tự đánh giá hệ thống có hoạt động không. Hiện chỉ có test chạy Terminal, không có chỗ quan sát. Mô tả sẵn ở `workbench/web_viewer/web_viewer.md`, chưa có code.

## Hiện trạng đã kiểm chứng (đọc code, không đoán)
1. `CameraSource` (src/fd_connect/camera_source.py) **không tự mở webcam/RTSP**: nó chỉ gọi `frame_provider(seq)` do bên ngoài đưa vào, nếu không có thì tự sinh khung giả (`encode_raw_frame`). Vì vậy EXP-001/EXP-002 (11 triệu frame) đo trên khung giả/mock, không phải ảnh thật từ camera; chưa có số liệu nhận diện người thật.
2. `StubAdapter` trả kết quả giả. `YoloAdapter` (FEAT-030) chưa duyệt, chưa code. `models/best.pt` chỉ có lớp mắt/miệng (I-009), chưa có điện thoại và góc đầu.
3. `DetectionResult.items[].box` có sẵn (x1,y1,x2,y2) nên vẽ khung được mà không đổi contracts.
4. `FlowEngine` đã có các thuộc tính đọc trạng thái: `health()`, `queues`, `diagnose`, `response`, `rules`, `ring_buffer`, `dispatcher`, `last_clip`, `baseline`, `sampler`, `camera.measured_fps`.
5. `requirements.txt` hiện chỉ có comment. `opencv, flask, numpy, ultralytics, torch, mediapipe, pyserial` ĐÃ CÀI trên máy PC nhưng chưa được duyệt (features/dependencies.md ghi flask+opencv ở G1 workbench). Câu tôi nói trước đó "đã có trong requirements.txt" là sai.
6. Ngưỡng luật trong `config.json` còn ghi `CHUA_XAC_DINH` (giá trị tạm). Web hiển thị phải ghi rõ điều này, không dùng làm số liệu đánh giá chính thức.

## Thành phần đề xuất (mỗi lượt Builder đúng 1 thành phần, đều nằm trong `workbench/web_viewer/`, nằm trong scope G3 `workbench/**`)

Thứ tự: A -> B -> C -> D -> E. Mỗi thành phần có test riêng và `gate.py` SẠCH trước REPORT.

### A. `state_hub.py` — kho số liệu dùng chung (chỉ thư viện chuẩn)
- Việc: giữ ảnh JPEG mới nhất, danh sách DetectionResult gần nhất, bộ đếm FPS/độ trễ (trung bình, P95), ErrorEvent gần nhất (tối đa N), trạng thái node. Có khóa luồng, kích thước giới hạn cứng (không phình RAM).
- Vào/Ra: nhận dữ liệu từ B/C; trả `snapshot()` dạng dict JSON.
- Test: nhiều luồng ghi/đọc, đủ giới hạn, không rò RAM.

### B. `live_runner.py` — chạy FlowEngine với camera thật cho web
- Việc: mở webcam hoặc RTSP bằng OpenCV, cấp ảnh qua `frame_provider` vào `CameraSource` (không sửa `src/`), chạy vòng `step_sampled`, đẩy kết quả vào A. RTSP đọc mật khẩu từ biến môi trường `FD_CAMERA_PASSWORD` (FEAT-036), không in ra. Vẽ khung `Detection.box` + nhãn + conf lên ảnh bằng OpenCV.
- Chế độ `--mock` (không cần camera) để Tester chạy tự động; chế độ camera thật chỉ chạy khi MAIN tự chạy tay.
- Rủi ro: cần xác nhận `FlowEngine` nhận được adapter AI tuỳ chọn mà không sửa `src/`. Nếu không nhận được, Builder báo BLOCKED và xin PROPOSE/CR riêng (không sửa kèm).
- Test: `--mock` 100 vòng không lỗi; ngắt provider -> thấy `E_CONN_LOST` với `node_path=connect.camera` trên snapshot.

### C. `yolo_preview.py` — bộ nhận diện xem thử, chỉ dùng trong workbench
- Việc: lớp thực hiện giao diện `AiAdapter` (name=`yolo_preview`) nạp `models/best.pt` bằng ultralytics, chạy ở 5-8 FPS, ảnh 640x480 (theo EXP-001). Dùng để MAIN thấy khung nhận diện thật trên mặt mình.
- Phân biệt rõ: đây KHÔNG phải FEAT-030, không nằm trong `src/`, không thay StubAdapter mặc định. Thiếu thư viện hoặc thiếu model -> health DOWN + `E_PROC_MODEL_LOAD`, web vẫn chạy với StubAdapter.
- Nhãn hiển thị: chỉ các lớp có thật trong `best.pt` (mắt, miệng). Không có ô điện thoại/góc đầu cho tới khi MAIN cung cấp model (I-009).
- Test: nạp model giả -> lỗi đúng mã, không crash; có ảnh mẫu thì ra khung.

### D. `server.py` + `static/index.html` — máy chủ web và trang xem
- Việc: Flask nghe `127.0.0.1` (không mở ra mạng), các đường: `/video` (MJPEG), `/api/snapshot` (JSON), `/api/errors`. Trang tự cập nhật (polling 500ms, không cần socketio).
- Hiển thị: ảnh + khung; FPS thật so với `target_fps`; độ trễ khung; trạng thái từng node (OK/DEGRADED/DOWN) trên cây luồng; độ sâu hàng đợi từng cạnh; trạng thái baseline và tài xế hiện tại; trạng thái luật (đang đếm ms nhắm mắt/ngáp so với `trigger_ms`); `last_clip` (ring buffer); thống kê dispatcher (outbox/spool/breaker); danh sách `ErrorEvent` (code, node_path, trace_id); khung ghi chú "ngưỡng luật = tạm, CHUA_XAC_DINH".
- Nút "Ghi CSV": lưu số liệu phiên vào `logs/web_session_<ts>.csv` để MAIN đối chiếu sau (ghi vào thư mục `logs/` đã nằm trong scope).
- Test: gọi các API bằng client thử của Flask; `/video` trả đúng loại nội dung MJPEG; snapshot đủ khóa; chạy ở chế độ `--mock`.

### E. Bảng ESP32 và nút thử loa (tuỳ chọn, làm sau cùng)
- Việc: hiển thị CO2/ADC/GPS (`FDESP:` hoặc `CO2:`), trạng thái DEGRADED khi chưa có GPS; nút gửi `ALERT:1`, `ALERT:2`, `SLEEP`, `STOP` và hiện ACK (`EVT,ACK,...,i2s=`) để kiểm BUG-001 ngay trên web.
- Ràng buộc: COM3 chỉ một chương trình dùng được, nên web giữ cổng; phải đóng Arduino Serial Monitor và `test_esp32_speaker.py`. Mở cổng serial cần MAIN cho phép lúc chạy.
- Test: dùng Esp32Source với `packet_provider` giả; không mở COM thật trong test tự động.

## Cách MAIN chạy thử (dự kiến, ghi vào REPORT khi xong)
`python workbench/web_viewer/server.py --source webcam` hoặc `--source rtsp` hoặc `--mock`, sau đó mở `http://127.0.0.1:<port>`. Cổng và nguồn cấu hình qua tham số/`config.json`, không hard-code (R4).

## Điểm MAIN cần quyết trước khi Builder bắt đầu
1. **Dependency (bắt buộc duyệt, CLAUDE.md mục 4):** đề xuất duyệt từng gói chỉ cho workbench: `flask`, `opencv-python`, `numpy` (A-D); `ultralytics`+`torch` chỉ cho C; `pyserial` cho E. Mọi gói đã cài trên máy nhưng chưa được duyệt trong `requirements.txt`. Tôi đề xuất không sửa `requirements.txt` của lõi mà tạo `workbench/web_viewer/requirements-web.txt` (không nạp vào Raspberry). Cần MAIN xác nhận cách ghi này.
2. Có đồng ý C (`yolo_preview`) làm riêng trong workbench, không tính là FEAT-030, và dùng `best.pt` hiện có (chỉ mắt/miệng) không?
3. Thứ tự: làm A->D trước (đã dùng được với StubAdapter và mock), hay muốn C sớm hơn để thấy khung nhận diện thật?
4. Cho phép MAIN tự mở camera/COM3 khi chạy web (mục 6 CLAUDE.md), còn test tự động của AI chỉ dùng mock.

## Rủi ro
- Nhận diện thật chạy nặng trên PC; giữ 5-8 FPS cho AI, luồng hiển thị tách riêng để web không giật.
- Số liệu web phản ánh StubAdapter nếu chưa bật C; ghi nhãn rõ "STUB/YOLO_PREVIEW" trên trang để không nhầm là kết quả YOLO chính thức.
- `check_comments.py` chỉ quét `src/`, nhưng luật F7 (không code giả, không comment né) vẫn áp dụng cho mã workbench.
- Web cho thấy thuật toán dựa trên ngưỡng tạm; không dùng để kết luận độ chính xác cuối.

## Không làm trong đề xuất này
Sửa `src/` hoặc `src/contracts/`, sửa `requirements.txt` lõi, YoloAdapter chính thức (FEAT-030), đổi `config.json`, thêm ErrorCode.
