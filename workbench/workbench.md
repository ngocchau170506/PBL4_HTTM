# [WORKBENCH] PHÒNG THÍ NGHIỆM ĐỘC LẬP & WEB KIỂM TRA THUẬT TOÁN (CHỈ CHẠY TRÊN PC)

> **Nguyên tắc sống còn khi triển khai Raspberry Pi:**  
> Toàn bộ thư mục `workbench/` (bao gồm cả `web_viewer/`) chỉ phục vụ kiểm thử, đo đạc và soi lỗi thuật toán trên máy tính (`mode="sim"`).  
> Khi đóng gói nạp vào **Raspberry Pi (`mode="rasp"`)**, toàn bộ thư mục `workbench/` sẽ được **LOẠI BỎ HOÀN TOÀN** để Pi chạy nhẹ, ổn định, không gánh bất kỳ tiến trình Web HTML/Flask nào.

---

## 1. Cổng Kiểm Duyệt Tự Động (Gatekeeper)
- `gate.py`: Cổng kiểm duy nhất trước mọi REPORT và MERGE (`scope` -> `check_comments` -> `unittest`).
- `check_scope.py`: Kiểm tra phạm vi file theo `scope/<Gx>.json` [S1] và chặn import chéo giữa các `fd_*` [S2].
- `check_comments.py`: [F7/F8] Quét cấm `TODO`, `pass` rỗng, comment tắt kiểm tra, `except` nuốt lỗi.
- `comment_allowlist.txt`: Danh sách ngoại lệ chỉ do MAIN duyệt.

## 2. Giao Diện Web Kiểm Tra Trực Quan (`workbench/web_viewer/`)
Thiết kế ngay từ nền móng để khi test bất kỳ node hay thuật toán nào trên PC đều quan sát được số liệu thực tế và tìm ra sơ hở:
- `web_viewer/lab_server.py`: Server Web nhẹ (chỉ chạy ở phòng thí nghiệm PC) nhận gói tin từ các node (`SignalPacket`, `DetectionResult`, `ViolationEvent`, `ErrorEvent`).
- `web_viewer/templates/lab_dashboard.html`: Giao diện trực quan hiển thị:
  - Luồng frame thực tế + khung chuẩn `BASELINE` (5-10s) của từng `driver_id`.
  - Thông số thuật toán thời gian thực (EAR mắt, MAR ngáp, góc nghiêng đầu, điện thoại, điểm rủi ro).
  - Trạng thái bộ đệm vòng `-2s -> 0s -> kết thúc` (`WATCHING` / `SUSPECT` / `ACTIVE` / `CLOSED`).
  - Sơ đồ sức khỏe các node (`OK` / `DEGRADED` / `DOWN`) và bảng báo lỗi đúng `nodePath` + `trace_id`.
- `web_viewer/static/`: Tài nguyên CSS/JS vẽ biểu đồ và khung nhận diện trên trình duyệt PC.

## 3. Các Trạm Thử Nghiệm Độc Lập Từng Bộ Phận (`workbench/probes/`)
- `connect_probe.py`: Mở thử nguồn Camera / ESP32 giả lập, đếm gói/giây, thử ngắt kết nối.
- `signal_check.py`: Bơm ảnh đen, ảnh sai kích thước, lệch timestamp để kiểm tra `fd_signal`.
- `flow_trace.py`: Bơm gói sai kiểu giữa 2 node để xác minh `fd_flow` chỉ đúng cạnh bị lỗi (`E_FLOW_EDGE_TYPE`).
- `algo_runner.py`: Chạy thử 1 thuật toán đơn lẻ trên video/ảnh mẫu và đẩy kết quả lên `web_viewer/` để soi sơ hở số liệu.
- `response_sim.py`: Kiểm tra buffer vòng `-2s..0s` và giả lập Máy chủ chậm/mất mạng.

## 4. Kịch Bản Mô Phỏng Sự Cố (`workbench/faults/`) & Unit Tests (`workbench/tests/`)
- `faults/camera_disconnect.yaml`: Kịch bản rớt khung / mất kết nối camera.
- `faults/esp32_timeout.yaml`: Kịch bản ESP32 mất gói / trễ gói (hệ thống phải chuyển sang `DEGRADED`, không sập cam).
- `tests/`: Chứa các bài kiểm thử tự động chạy qua `unittest`.

---

## 5. Danh Mục Lọc Bỏ Khi Nạp Sang Raspberry Pi (Deployment Checklist)

| Giữ lại nạp vào Raspberry Pi (`mode="rasp"`) | Vứt bỏ lại trên PC (Không nạp vào Pi) |
| :--- | :--- |
| `src/contracts/` (Hợp đồng dữ liệu chuẩn) | Toàn bộ `workbench/` (kể cả `web_viewer/`, `tests/`, `faults/`) |
| `src/fd_config/` (`config.json` với `mode="rasp"`) | `src/fd_faultsim/`, `src/fd_diagnose/`, `src/fd_eval/` |
| `src/fd_connect/`, `src/fd_signal/`, `src/fd_flow/` | `training/`, `DRAFT/`, `features/`, `scope/`, `changes/` |
| `src/fd_process/` (Sampler, Baseline, AI Adapter, Rules) | Thư viện Web (`flask`, `flask-socketio`) |
| `src/fd_response/` (Còi tại chỗ + Hàng đợi gửi Máy chủ) | Các file ghi chú thiết kế `*.md` |
| `models/` (Chỉ giữ file model đã tối ưu cho Pi) | Các model thử nghiệm nặng không dùng tới |
