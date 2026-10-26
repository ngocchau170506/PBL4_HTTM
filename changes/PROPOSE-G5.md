# PROPOSE-G5 — Giai đoạn G5: Đánh giá tổng hợp & Mô phỏng lỗi (Fault Simulation)

Vai: **Planner / Builder**. Ngày: 2026-10-26. Trạng thái: **BUILT** (FEAT-050: BUILT, FEAT-051: BUILT, FEAT-052: BUILT — Đã vượt qua `python workbench/gate.py` SẠCH).
Ràng buộc chung:
- **TUYỆT ĐỐI KHÔNG THIẾT KẾ AI / KHÔNG SỬA MODEL AI**: AI đã do MAIN tự quản lý và cung cấp. Hệ thống chỉ coi AI là một hộp đen cắm qua giao diện chuẩn `AiAdapter` (hoặc `StubAdapter` khi kiểm thử hạ tầng).
- Chỉ sử dụng thư viện chuẩn Python (cho tới khi MAIN duyệt dependency).
- Không sửa `src/contracts/` (logic.py, state.py) trừ khi có Change Request riêng.
- Mỗi lượt `Builder` chỉ làm đúng 1 component đã duyệt.
- Mọi module kiểm thử độc lập đặt tại `workbench/`, dọn sạch mã test sau khi đo đạc.
- Mỗi FEAT phải vượt qua `python workbench/gate.py` SẠCH trước khi chuyển giao.

---

## 1. Mục tiêu Giai đoạn G5 (Trọng tâm Hạ tầng & Hệ điều hành luồng)
Kiểm chứng độ tin cậy, thông lượng truyền dẫn, độ trễ từng chặng và khả năng tự phục hồi của "hệ điều hành nhỏ" `fd_flow` và các thành phần ngoại vi (Camera, ESP32, Server Dispatcher) trong điều kiện mô phỏng khắc nghiệt và tiêm lỗi (Fault Injection), hoàn toàn độc lập với thuật toán AI nội bộ:

1. **Không can thiệp AI**: Bộ đo `fd_eval` chỉ ghi nhận thời gian xử lý suy diễn (`infer_time_ms`) như một chặng trong pipeline, không đánh giá hay can thiệp vào mô hình AI.
2. **Đo đạc hạ tầng luồng dữ liệu**: Đo thông lượng (FPS), phân vị độ trễ từng nút (P50, P90, P99), kích thước hàng đợi và độ biến động bộ nhớ (RAM leak = 0.00 MB).
3. **Mô phỏng lỗi & Khả năng phòng vệ (fd_faultsim)**: Chứng minh hệ thống tự cô lập lỗi, chuyển trạng thái an toàn (`DEGRADED`), kích hoạt ngắt mạch (`Circuit Breaker`) khi mạng rớt hoặc phần cứng chập chờn mà không làm sập tiến trình chính.
4. **Tự động báo cáo & chẩn đoán (fd_diagnose)**: Tự động kết xuất số liệu hạ tầng, đối chiếu ngưỡng cấu hình và cảnh báo sự cố.

---

## 2. Thứ tự triển khai đề xuất

```text
FEAT-050 (Bộ đo hạ tầng luồng fd_eval: MetricsCollector, Throughput & Latency)
   └──> FEAT-051 (Mô phỏng lỗi hạ tầng fd_faultsim: Kịch bản mạng yếu & phần cứng)
          └──> FEAT-052 (Hệ thống tự sinh báo cáo chuẩn hóa fd_diagnose)
```

---

### FEAT-050 — Bộ đo tổng hợp hạ tầng luồng: Thông lượng, Độ trễ chặng, Kích thước hàng đợi (`src/fd_eval`)

- **Mục tiêu:**
  - Xây dựng module `MetricsCollector` tại `src/fd_eval/metrics_collector.py` tuân thủ `NodeContract`:
    + Đo thông lượng khung hình thực tế (FPS) qua từng chặng luồng: `connect` $\rightarrow$ `signal` $\rightarrow$ `sampler` $\rightarrow$ `process` $\rightarrow$ `response`.
    + Đo phân vị độ trễ chuyển tiếp gói tin (P50, P90, P99, Max) theo microsecond/millisecond giữa các cạnh cây luồng `FLOW_TREE`.
    + Giám sát chiều sâu hàng đợi (`queue_depth`) và cảnh báo khi hàng đợi vượt ngưỡng an toàn.
    + Đo lường mức chiếm dụng bộ nhớ (RAM consumption) và xác thực giải phóng bộ nhớ (zero leak).
  - Tích hợp cấu hình ngưỡng đánh giá `eval` vào `src/fd_config/config.json` và bổ sung `EvalConfig` trong `src/fd_config/config_loader.py`.
- **Phạm vi file dự kiến:**
  - `src/fd_eval/metrics_collector.py`
  - `src/fd_eval/__init__.py`
  - `src/fd_eval/fd_eval.md`
  - `src/fd_config/config.json` (bổ sung mục cấu hình `eval`)
  - `src/fd_config/config_loader.py` (nạp `EvalConfig`)
  - `workbench/tests/test_metrics_collector.py`
- **Vào / Ra:**
  - Vào: Dòng gói tin `PacketBase` (timestamps, queue metrics, packet hops) qua các hook quan sát không xâm lấn.
  - Ra: Bản ghi thống kê `MetricsSummary` (fps, p50/p95/p99 latency từng node, queue_depth, ram_delta_mb).
- **Phòng vệ rò rỉ bộ nhớ:**
  - Sử dụng giải thuật cập nhật trực tuyến (Welford) và lấy mẫu hồ chứa (reservoir sampling 1,000 mẫu) để cố định bộ nhớ đệm, không tích lũy dữ liệu thô vô hạn.
- **Cách test độc lập:**
  - Bơm 50,000 gói tin liên tục -> RAM đỉnh tăng thêm $< 1.0\text{ MB}$;
  - Kiểm tra độ sai lệch phân vị P50/P99 $< 0.1\%$;
  - Kiểm tra `self_test()` và `health()` đạt `OK`.

---

### FEAT-051 — Kịch bản mạng yếu & Tiêm lỗi hạ tầng (`src/fd_faultsim`)

- **Mục tiêu:**
  - Xây dựng module `FaultInjector` tại `src/fd_faultsim/fault_injector.py` thực thi các kịch bản lỗi cấu hình từ `workbench/faults/*.yaml`:
    + Kịch bản 1: **Mất mạng / Mạng chập chờn Server Dispatcher** (`network_drop.yaml`): Gây nghẽn hoặc rớt kết nối; kiểm tra Circuit Breaker chuyển `OPEN`, lưu tạm gói vào offline spool, báo `E_RESP_BUFFER_OVERRUN` khi đầy đệm mà không làm sập luồng chính.
    + Kịch bản 2: **Mất gói / Trễ nhịp UART ESP32** (`uart_degraded.yaml`): Làm rơi gói telemetry hoặc quá hạn ACK; kiểm tra node `connect.esp32` chuyển `DEGRADED`, **bảo toàn 100% luồng Camera**.
    + Kịch bản 3: **Sụt giảm FPS Camera** (`camera_fps_drop.yaml`): Tụt tốc độ cấp khung hình; kiểm tra phát hiện trôi nhịp `E_SIG_TS_DRIFT`.
    + Kịch bản 4: **Gói tin sai kiểu ở cạnh luồng** (`edge_contract_violation.yaml`): Bơm sai loại packet giữa hai node; kiểm tra `FlowEngine` cô lập và báo đúng mã `E_FLOW_EDGE_TYPE` kèm cạnh lỗi `(A -> B)`.
- **Phạm vi file dự kiến:**
  - `src/fd_faultsim/fault_injector.py`
  - `src/fd_faultsim/__init__.py`
  - `src/fd_faultsim/fd_faultsim.md`
  - `workbench/faults/network_drop.yaml`
  - `workbench/faults/uart_degraded.yaml`
  - `workbench/faults/camera_fps_drop.yaml`
  - `workbench/faults/edge_contract_violation.yaml`
  - `workbench/tests/test_faultsim.py`
- **Vào / Ra:**
  - Vào: Kịch bản tiêm lỗi YAML/Dict.
  - Ra: Can thiệp trễ nhịp, rớt gói, sai định dạng; kiểm tra và đếm các `ErrorEvent` phát ra.
- **Phòng vệ:**
  - Mã lỗi bắt buộc thuộc `ErrorCode` trong `contracts/logic.py`, `node_path` chỉ đích danh, không gây unhandled exception.
- **Cách test độc lập:**
  - Kích hoạt 4 kịch bản lỗi; xác nhận 100% `ErrorEvent` chuẩn xác, hệ thống duy trì hoạt động an toàn.

---

### FEAT-052 — Báo cáo hạ tầng tự sinh & Chẩn đoán (`src/fd_diagnose` & `src/fd_eval`)

- **Mục tiêu:**
  - Xây dựng module `AutoReporter` tại `src/fd_diagnose/auto_reporter.py` tuân thủ `NodeContract`:
    + Thu thập số liệu vận hành hạ tầng từ `MetricsCollector` (FEAT-050) và nhật ký lỗi từ `fd_diagnose`.
    + Tự động đối chiếu với ngưỡng cấu hình trong `src/fd_config/config.json`.
    + Tự động xuất báo cáo hạ tầng chuẩn hóa dạng JSON và Markdown (`changes/BENCHMARK-G5.md`).
    + Nếu có lỗi nghiêm trọng: tự động trích xuất nguyên nhân gốc và gợi ý tạo hồ sơ `BUG-xxx.md`.
- **Phạm vi file dự kiến:**
  - `src/fd_diagnose/auto_reporter.py`
  - `src/fd_diagnose/diagnose.md`
  - `workbench/tests/test_auto_reporter.py`
- **Vào / Ra:**
  - Vào: `MetricsSummary`, sự kiện lỗi `ErrorEvent`, ngưỡng cấu hình `EvalConfig`.
  - Ra: File báo cáo chuẩn hóa `changes/BENCHMARK-G5.md`.
- **Phòng vệ:**
  - Không hard-code đường dẫn; đọc đường dẫn xuất báo cáo từ cấu hình hoặc tham số gọi.
- **Cách test độc lập:**
  - Nạp dữ liệu mô phỏng đạt chuẩn -> Báo cáo kết luận `PASS`;
  - Nạp dữ liệu có sự cố -> Báo cáo kết luận `FAIL` kèm phân tích nguyên nhân.

---

## 3. Dự thảo Cấu hình Ngưỡng G5 (`src/fd_config/config.json`)

Mục cấu hình `eval` thuần túy đo hạ tầng đường ống (chờ MAIN duyệt thông số):
```json
"eval": {
  "min_throughput_fps": 3000,
  "max_p95_latency_ms": 1.0,
  "max_p99_latency_ms": 5.0,
  "max_queue_depth": 100,
  "max_ram_growth_mb": 5.0,
  "sustained_test_duration_s": 30
}
```

---

## 4. Dự thảo Phạm vi Giai đoạn G5 (`scope/G5.json`)

```json
{
  "gate": "G5",
  "status": "APPROVED",
  "note": "Phạm vi G5: Đánh giá tổng hợp hạ tầng & Mô phỏng lỗi (FEAT-050 MetricsCollector, FEAT-051 FaultInjector, FEAT-052 AutoReporter). Không sửa/can thiệp AI. Chỉ dùng thư viện chuẩn.",
  "allowed": [
    ".gitignore",
    ".vscode/**",
    "CLAUDE.md",
    "agents.md",
    "GEMINI.md",
    "feature-map.md",
    "README.md",
    "requirements.txt",
    "features/**",
    "scope/**",
    "DRAFT/**",
    "changes/**",
    "models/**",
    "training/**",
    "logs/**",
    "workbench/**",
    "src/contracts/**",
    "src/fd_config/**",
    "src/fd_connect/**",
    "src/fd_signal/**",
    "src/fd_process/**",
    "src/fd_flow/**",
    "src/fd_diagnose/**",
    "src/fd_response/**",
    "src/fd_eval/**",
    "src/fd_faultsim/**",
    "src/*.md",
    "src/**/.gitkeep"
  ]
}
```

---

## 5. Tiêu chí nghiệm thu GATE G5

1. **Thông lượng & Độ trễ hạ tầng**: Đạt $\ge 3,000\text{ FPS}$ trên luồng chuyển tiếp; phân vị trễ chuyển tiếp nút $P95 \le 1.0\text{ ms}$.
2. **Khả năng phòng vệ & Tiêm lỗi**: Bắt đúng $100\%$ lỗi hạ tầng được tiêm trong `FEAT-051`, phát sinh đúng `ErrorCode` và `node_path`.
3. **Cách ly sự cố**: Khi nhánh phụ (ESP32 / Mạng Server) gặp sự cố, luồng camera chính được bảo toàn $100\%$.
4. **Không rò rỉ bộ nhớ**: Độ tăng trưởng RAM qua phiên chạy liên tục đạt $0.00\text{ MB}$ (sau GC).
5. **Cổng kiểm tra**: Lệnh `python workbench/gate.py` đạt **SẠCH** tuyệt đối.

---

## 6. Điểm cần MAIN duyệt trước khi chuyển sang vai Builder

1. **Duyệt hồ sơ PROPOSE-G5** (đã loại bỏ hoàn toàn việc thiết kế hay can thiệp AI).
2. **Duyệt thông số ngưỡng hạ tầng G5** trong `config.json`.
3. **Kích hoạt Giai đoạn G5**: Phê duyệt tạo `scope/G5.json` và cập nhật dòng `GIAI ĐOẠN HIỆN TẠI: G5` tại `feature-map.md`.
4. **Giao lệnh cho Builder**: MAIN ra lệnh `ROLE: Builder. FEAT-050. Phạm vi: src/fd_eval, src/fd_config, workbench/tests`.
