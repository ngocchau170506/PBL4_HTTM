# BENCHMARK-G5 — Báo cáo Đánh giá Tổng hợp Hạ tầng & Mô phỏng lỗi

- **Thời điểm kết xuất**: `2026-10-26T15:53:00+07:00`
- **Giai đoạn**: `G5`
- **Kết luận tổng thể**: **PASS**
- **Đạt ngưỡng Metrics (`fd_eval`)**: `PASS`
- **Đạt kịch bản Phòng vệ lỗi (`fd_faultsim`)**: `PASS`

## 1. Số liệu Tổng hợp Hạ tầng Luồng (`fd_eval`)

| Chỉ số | Giá trị |
|---|---|
| Tổng số gói xử lý | `276400` |
| Thông lượng tổng thể (FPS) | `52976.05` |
| Độ trễ P50 (ms) | `0.0350` |
| Độ trễ P90 (ms) | `0.0450` |
| Độ trễ P95 (ms) | `0.0800` |
| Độ trễ P99 (ms) | `0.1200` |
| Độ trễ cực đại Max (ms) | `0.1500` |
| Chiều sâu hàng đợi đỉnh | `4` |
| Tăng trưởng bộ nhớ RAM (MB) | `0.1142` |

### Phân rã theo từng chặng luồng

| Chặng (`stage`) | Số gói | FPS | P50 (ms) | P95 (ms) | P99 (ms) | Max (ms) |
|---|---|---|---|---|---|---|
| `connect.camera` | 80000 | 15333.15 | 0.0350 | 0.0350 | 0.0350 | 0.0350 |
| `connect.esp32` | 16000 | 3066.63 | 0.0800 | 0.0800 | 0.0800 | 0.0800 |
| `process.ai` | 10000 | 1916.64 | 0.1200 | 0.1200 | 0.1200 | 0.1200 |
| `process.rules` | 10000 | 1916.64 | 0.0300 | 0.0300 | 0.0300 | 0.0300 |
| `process.sampler` | 80000 | 15333.15 | 0.0200 | 0.0200 | 0.0200 | 0.0200 |
| `response` | 400 | 76.67 | 0.1500 | 0.1500 | 0.1500 | 0.1500 |
| `signal` | 80000 | 15333.15 | 0.0450 | 0.0450 | 0.0450 | 0.0450 |

## 2. Kết quả Kịch bản Tiêm lỗi Hạ tầng (`fd_faultsim`)

| Kịch bản | Loại | Số lỗi bắt được | Khớp mã & nodePath | Luồng Cam sống sót | Tự hồi phục | Kết quả |
|---|---|---|---|---|---|---|
| `FI-051-NET` | `network_drop` | 14 | `True` | `100.0%` | `True` | **PASS** |
| `FI-051-UART` | `uart_degraded` | 17 | `True` | `100.0%` | `True` | **PASS** |
| `FI-051-FPS` | `camera_fps_drop` | 10 | `True` | `44.4%` | `True` | **PASS** |
| `FI-051-EDGE` | `edge_contract_violation` | 7 | `True` | `100.0%` | `True` | **PASS** |

## 3. Chẩn đoán Sự cố Tự động (`fd_diagnose`)

- **BUG-G5-001** (`E_CONN_LOST` tại `connect.esp32`, phân loại: *nguồn*, số lần: 6, tự hồi phục: `True`): Mất kết nối thiết bị ngoại vi giữa chừng; nhánh optional cần chuyển sang DEGRADED.
- **BUG-G5-002** (`E_CONN_TIMEOUT` tại `connect.esp32`, phân loại: *nguồn*, số lần: 6, tự hồi phục: `True`): Quá thời gian chờ gói tin hoặc ACK từ phần cứng ngoại vi.
- **BUG-G5-003** (`E_FLOW_EDGE_TYPE` tại `process.ai->process.rules`, phân loại: *hợp đồng*, số lần: 7, tự hồi phục: `True`): Gói dữ liệu đi qua cạnh cây luồng không khớp kiểu khai báo trong FLOW_TREE.
- **BUG-G5-004** (`E_RESP_BUFFER_OVERRUN` tại `response.dispatcher`, phân loại: *hiệu năng*, số lần: 6, tự hồi phục: `True`): Bộ đệm vòng hoặc offline spool vượt trần dung lượng cho phép.
- **BUG-G5-005** (`E_RESP_NO_ACK` tại `response.dispatcher`, phân loại: *nguồn*, số lần: 6, tự hồi phục: `True`): Máy chủ không phản hồi ACK đúng hạn; kiểm tra Circuit Breaker và spool.
- **BUG-G5-006** (`E_RESP_SEND` tại `response.dispatcher`, phân loại: *nguồn*, số lần: 2, tự hồi phục: `True`): Lỗi kết nối mạng khi gửi gói vi phạm về máy chủ; kích hoạt offline spool.
- **BUG-G5-007** (`E_SIG_BAD_FORMAT` tại `connect.esp32`, phân loại: *dữ liệu*, số lần: 5, tự hồi phục: `True`): Gói tin thô sai cấu trúc hoặc thiếu trường định danh trace_id.
- **BUG-G5-008** (`E_SIG_EMPTY` tại `signal`, phân loại: *dữ liệu*, số lần: 5, tự hồi phục: `True`): Khung hình hoặc payload rỗng (0 byte) từ nguồn thu.
- **BUG-G5-009** (`E_SIG_TS_DRIFT` tại `signal`, phân loại: *hiệu năng*, số lần: 5, tự hồi phục: `True`): Độ lệch timestamp giữa các khung vượt ngưỡng; tốc độ cấp khung hình bị sụt giảm.
