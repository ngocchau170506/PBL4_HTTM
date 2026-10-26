# [C-FAULTSIM] MÔ PHỎNG LỖI HẠ TẦNG (fd_faultsim)

- **Vai trò**: Bơm lỗi hạ tầng có kiểm soát theo kịch bản `workbench/faults/*.yaml` (FEAT-051).
- **Kịch bản hỗ trợ**:
  1. `network_drop.yaml`: Mạng yếu / mất kết nối máy chủ (`E_RESP_SEND`, `E_RESP_NO_ACK`, `E_RESP_BUFFER_OVERRUN`).
  2. `uart_degraded.yaml`: Mất gói / trễ nhịp UART ESP32 (`E_CONN_LOST`, `E_CONN_TIMEOUT`, `E_SIG_BAD_FORMAT`) với cô lập an toàn 100% luồng Camera.
  3. `camera_fps_drop.yaml`: Sụt giảm FPS Camera / trôi nhịp (`E_SIG_TS_DRIFT`, `E_SIG_EMPTY`).
  4. `edge_contract_violation.yaml`: Gói tin sai kiểu ở cạnh luồng (`E_FLOW_EDGE_TYPE`).
  5. `get.yaml`: Kiểm tra chặn vi phạm phạm vi (`E_SCOPE_VIOLATION`).
- **Thành phần**:
  - `fault_injector.py`: `FaultInjector`, `FaultScenario`, `FaultExecutionReport`, `parse_simple_yaml`.
