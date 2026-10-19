# [PROBES] KỊCH BẢN CHẠY THỬ ĐỘC LẬP TỪNG BỘ PHẬN (`workbench/probes/`)

Mỗi bộ phận (`fd_*`) phải chạy thử độc lập tại đây và gửi số liệu sang `workbench/web_viewer/` trước khi được nối vào cây luồng chính:
- `connect_probe.py`: Kiểm tra mở nguồn Camera / ESP32, đếm gói/giây, thử ngắt kết nối giả.
- `signal_check.py`: Kiểm tra chuẩn hóa frame, phát hiện ảnh đen, sai kích thước, lệch timestamp.
- `flow_trace.py`: Kiểm tra cây luồng, bơm gói sai kiểu để xác minh `E_FLOW_EDGE_TYPE`.
- `algo_runner.py`: Chạy 1 thuật toán đơn lẻ trên video/ảnh mẫu và hiển thị thông số lên `web_viewer`.
- `response_sim.py`: Kiểm tra bộ đệm vòng `-2s..0s` và hàng đợi gửi Máy chủ khi mạng chập chờn.
