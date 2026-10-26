# [C-DIAGNOSE] CHẨN ĐOÁN & BÁO CÁO TỰ SINH (fd_diagnose)

- **Vai trò**: Nhận `ErrorEvent` ngoài đường chạy chính, kết xuất hồ sơ nháp `BUG-xxx.md` và tự động tổng hợp báo cáo đánh giá hạ tầng Markdown/JSON (FEAT-014, FEAT-052).
- **Thành phần**:
  - `error_recorder.py`: `ErrorRecorder` (deque giới hạn dung lượng, không rò rỉ bộ nhớ).
  - `auto_reporter.py`: `AutoReporter`, `DiagnosisFinding`, `EvaluationReport` (tổng hợp số liệu từ `MetricsCollector` và `FaultInjector`, đối chiếu ngưỡng cấu hình, tự sinh báo cáo `BENCHMARK-G5` và bản nháp `BUG-xxx`).
