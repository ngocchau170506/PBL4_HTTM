# [C-EVAL] ĐÁNH GIÁ TỔNG HỢP HẠ TẦNG LUỒNG (fd_eval)

- **Vai trò**: Đo thông lượng thực tế (FPS), phân vị độ trễ từng chặng (P50, P90, P95, P99, Max), chiều sâu hàng đợi (`queue_depth`) và mức tăng trưởng RAM khi chạy liên tục (FEAT-050).
- **Nguyên tắc**: Không can thiệp hay thiết kế thuật toán AI; chỉ quan sát hạ tầng truyền dẫn luồng dữ liệu.
- **Thành phần**:
  - `metrics_collector.py`: `MetricsCollector`, `EvalThresholds`, `StageMetrics`, `MetricsSummary` (sử dụng thuật toán Welford trực tuyến và lấy mẫu hồ chứa cố định bộ nhớ).
