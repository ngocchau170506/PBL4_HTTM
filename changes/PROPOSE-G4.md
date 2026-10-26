# PROPOSE-G4 — Giai đoạn G4 (ESP32 & Cảm biến)

Vai: Planner / Builder. Ngày: 2026-10-25. Trạng thái: DONE (FEAT-042: DONE, FEAT-040: DONE, FEAT-041: DONE — Đã vượt qua `python workbench/gate.py` SẠCH).
Ràng buộc chung: chỉ thư viện chuẩn; không sửa `src/contracts/`; không sửa `requirements.txt`; mỗi lượt Builder 1 component; mỗi FEAT phải qua `python workbench/gate.py` SẠCH trước REPORT.

## Thứ tự đề xuất

FEAT-042 (Replay / Fixture) -> FEAT-040 (ESP32 Telemetry Độc lập) -> FEAT-041 (ESP32 Kênh lệnh Hỗ trợ).
Lý do: Bộ Replay (FEAT-042) cung cấp công cụ kiểm thử tự động, chuẩn hóa dữ liệu mô phỏng trong CI/workbench trước, giúp FEAT-040 và FEAT-041 có thể kiểm thử toàn diện ngay cả khi không có phần cứng ESP32 cắm trực tiếp.

---

## FEAT-042 — Replay dữ liệu cảm biến liên tục (`src/fd_connect/sensor_replay.py`)

- Việc:
  - Xây dựng module `SensorReplay` đọc dữ liệu bản tin cảm biến từ file fixture (`workbench/fixtures/telemetry_sample.jsonl`) hoặc sinh chuỗi telemetry liên tục (CO2, rung động, GPS fix, tốc độ, gia tốc).
  - Hỗ trợ 2 chế độ nhịp: (1) thời gian thực (giữ đúng delta ts) và (2) tăng tốc (phục vụ dồn tải công suất cao).
  - Tích hợp cơ chế Fault Injection có thể cấu hình: cố ý bỏ sequence (mất gói), trễ nhịp (timeout), sinh chuỗi sai cú pháp (bad format/corrupted payload) để phục vụ kiểm thử phòng vệ.
- File đụng:
  - `src/fd_connect/sensor_replay.py`
  - `workbench/fixtures/telemetry_sample.jsonl`
  - `workbench/tests/test_sensor_replay.py`
  - `src/fd_connect/connect.md`
- Vào/Ra:
  - Vào: Cấu hình nhịp replay, file fixture, bộ lọc tiêm lỗi.
  - Ra: Chuỗi `bytes` (`FDESP:{...}` hoặc chuỗi text format) cho `Esp32Source` nạp vào `RawPacket`.
- Lỗi phát sinh: `E_SIG_BAD_FORMAT` (khi fixture lỗi), `E_CONN_TIMEOUT` (khi tiêm trễ nhịp).
- Rủi ro: Tràn bộ nhớ khi đọc file fixture lớn; xử lý bằng đọc streaming từng dòng (generator), không đọc toàn bộ file vào RAM.
- Test:
  - Replay 50,000 gói không rò rỉ RAM;
  - Tái hiện đúng độ trễ nhịp giữa các gói;
  - Tiêm lỗi mất gói được nhận diện chuẩn xác.

---

## FEAT-040 — ESP32 Source Chế độ độc lập (`src/fd_connect/esp32_source.py`)

- Việc:
  - Hoàn thiện `Esp32Source` cho chế độ độc lập (chỉ phát telemetry).
  - Hỗ trợ phân tích cả 2 định dạng bản tin thực tế từ firmware:
    1. Định dạng JSON chuẩn: `FDESP:{"seq":..,"co2_ppm":..,"vibration_g":..,"gps_ready":..,"gps_lat":..,"gps_lon":..}`
    2. Định dạng chuỗi văn bản ASCII firmware: `CO2:420,ADC:1200,GPS_FIX:1,SPD:45.2,LAT:16.074,LON:108.223`
  - Cấu hình an toàn phần cứng: Ghim cờ `dsrdtr=False, rtscts=False` khi kết nối cổng Serial/COM để chống khởi động lại (reset) chip nạp CP2102/CH340 ngoài ý muốn (kinh nghiệm rút ra từ EXP-001).
  - Cơ chế cô lập lỗi: Phát hiện mất gói qua sequence gap, đứt kết nối cáp vật lý (`E_CONN_LOST`), quá thời gian chờ (`E_CONN_TIMEOUT`). Khi xảy ra lỗi ESP32, node `connect.esp32` chuyển sang `DEGRADED`, **bảo toàn tuyệt đối 100% luồng xử lý Camera** (không crash, không chặn luồng).
- File đụng:
  - `src/fd_connect/esp32_source.py`
  - `src/fd_config/config.json` (tham số cấu hình baudrate 115200, port, timeout)
  - `workbench/tests/test_esp32_source.py`
  - `src/fd_connect/connect.md`
- Vào/Ra:
  - Vào: Cổng Serial / Provider / Replay stream.
  - Ra: `RawPacket(sensor_kind="esp32")` hoặc `ErrorEvent(node_path="connect.esp32")`.
- Lỗi: `E_CONN_OPEN`, `E_CONN_LOST`, `E_CONN_TIMEOUT`, `E_SIG_BAD_FORMAT`.
- Rủi ro: ESP32 gửi dữ liệu rác khi mới cắm điện (boot noise); xử lý bằng lọc bỏ ký tự lạ trước tiền tố hợp lệ.
- Test:
  - Chạy liên tục khi ngắt nguồn ESP32 giả lập -> luồng Camera vẫn chạy 100% không gián đoạn;
  - Phân tích chính xác 1,000 bản tin telemetry cả 2 định dạng;
  - Tự động phát `E_CONN_LOST` và phục hồi khi kết nối lại.

---

## FEAT-041 — ESP32 Chế độ hỗ trợ: Kênh lệnh cảnh báo hai chiều (`src/fd_connect/esp32_commander.py`)

- Việc:
  - Thiết kế kênh phát tín hiệu từ máy tính xuống ESP32 để kích hoạt còi/loa/đèn cảnh báo khi phát hiện vi phạm (`ALERT:1:1500`, `ALERT:2:3000`, `STOP`, `CONNECT`).
  - Đồng bộ và khắc phục vấn đề phần cứng [`BUG-001.md`](changes/BUG-001.md):
    - Đợi và xác nhận phản hồi bắt tay `EVT,ACK,<cmd>,...` từ firmware ESP32.
    - Cấu hình thời gian chờ ACK (`ack_timeout_ms=300ms`); nếu không có ACK phát cảnh báo `E_RESP_NO_ACK` hoặc `E_CONN_TIMEOUT` tại kênh hỗ trợ.
  - An toàn đa luồng (Thread-Safety): Thiết lập cơ chế khóa cổng UART (`threading.Lock` / Mutex) hoặc hàng đợi lệnh bất đồng bộ non-blocking (`CommandQueue`) để tránh xung đột giữa luồng nhận telemetry và luồng phát lệnh cảnh báo.
- File đụng:
  - `src/fd_connect/esp32_commander.py`
  - `src/fd_connect/esp32_source.py`
  - `workbench/tests/test_esp32_commander.py`
  - `src/fd_connect/connect.md`
- Vào/Ra:
  - Vào: Lệnh cảnh báo (`AlertCommand(level, duration_ms)`).
  - Ra: Chuỗi lệnh UART mã hóa + Kết quả ACK (`AckReceipt(success, ack_payload)`).
- Lỗi: `E_RESP_NO_ACK`, `E_CONN_TIMEOUT`, `E_CONN_LOST`.
- Rủi ro: Loa ESP32 câm tiếng do firmware chưa nạp bản sửa I2S; xử lý bằng kiểm tra cờ `i2s=1` trong chuỗi ACK `EVT,ACK,ALERT,1,i2s=1`.
- Test:
  - Luồng telemetry và lệnh cảnh báo chạy đồng thời dồn tải không gây race condition;
  - Xử lý mượt mà khi firmware phản hồi ACK đúng thời hạn;
  - Báo lỗi đúng mã khi timeout ACK mà không làm crash hệ thống.

---

## Điểm cần MAIN duyệt trước khi Builder bắt đầu

1. **Duyệt hồ sơ PROPOSE-G4.md** và thứ tự ưu tiên: `FEAT-042` -> `FEAT-040` -> `FEAT-041`.
2. **Duyệt đóng GATE G3**: Ghi nhận `GATE G3: PASS` vào `feature-map.md`.
3. **Mở phạm vi G4**: Tạo file `scope/G4.json` và cập nhật `GIAI ĐOẠN HIỆN TẠI: G4`.
4. **Cung cấp lệnh giao việc**: Chỉ định `ROLE: Builder` cho `FEAT-042` kèm phạm vi file.

---

## Rủi ro chung & Tiêu chí nghiệm thu GATE G4

- Tiêu chí Gate G4 theo quy định: *"mất gói/trễ gói -> báo lỗi đúng mã, không làm sập luồng cam."*
- Bằng chứng nghiệm thu: Chạy kịch bản dồn tải kết hợp Camera 25 FPS + ESP32 Telemetry 10 Hz + Bơm lỗi ngắt cáp Serial liên tục, hệ thống duy trì luồng Camera đạt chuẩn `OK`, nhánh ESP32 chuyển `DEGRADED` và phục hồi ngay khi luồng Serial kết nối lại.
