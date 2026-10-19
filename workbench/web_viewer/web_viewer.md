# [WEB-VIEWER] GIAO DIỆN WEB KIỂM TRA TRONG PHÒNG THÍ NGHIỆM (`workbench/web_viewer/`)

## Mục đích
- Cung cấp giao diện trực quan trên trình duyệt máy tính (PC) trong suốt quá trình phát triển và chạy mô phỏng (`mode="sim"`).
- Giúp quan sát trực tiếp:
  1. Khung hình Camera, vùng nhận diện khuôn mặt và quá trình học `BASELINE` (5–10s đầu cho mỗi tài xế).
  2. Các con số trung gian của thuật toán (EAR, MAR, góc quay đầu, nhãn `DetectionResult` và độ tự tin `conf`).
  3. Trạng thái của từng node trên cây luồng `FLOW_TREE` (`OK` / `DEGRADED` / `DOWN`) và độ sâu hàng đợi.
  4. Hồ sơ lỗi `ErrorEvent` thời gian thực kèm `trace_id` và `node_path`.

## Quy tắc đóng gói
- Nằm hoàn toàn trong `workbench/web_viewer/`, không được import ngược vào `src/`.
- Khi chuyển giao mã nguồn sang **Raspberry Pi (`mode="rasp"`)**, thư mục này bị loại bỏ hoàn toàn cùng với `workbench/`, giúp Raspberry Pi không tốn tài nguyên cho giao diện Web mà chỉ tập trung xử lý và truyền gói dữ liệu về Máy chủ.
