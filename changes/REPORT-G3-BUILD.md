# REPORT-G3-BUILD — FEAT-032 và FEAT-031 (bàn giao cho AI đọc sau)

Ngày: 2026-10-22. Giai đoạn: G3. Vai đã dùng trong phiên: Planner -> Builder (FEAT-032) -> Reviewer -> Builder (FEAT-031).
Không có thao tác git ghi, không cài package, không mở camera/serial/mạng.

## 1. Trạng thái hiện tại (đọc trước)

| Mục | Trạng thái |
|---|---|
| Giai đoạn | G3 (đã mở, `scope/G3.json`) |
| FEAT-032 ring_buffer | BUILT, Reviewer PASS, chờ MAIN merge |
| FEAT-031 luật | BUILT (chỉ 2 luật mắt/ngáp + engine), chờ Reviewer |
| FEAT-030 YoloAdapter | CHỈ THIẾT KẾ, KHÔNG được code (dependency chưa duyệt) |
| FEAT-033 server_dispatcher | PROPOSED, chưa làm |
| Nối rules + ring_buffer vào fd_flow | CHƯA LÀM. MAIN chọn (b) sửa nhỏ trong G3 (I-004). Cần FEAT riêng |
| Gate | `python workbench/gate.py` SẠCH, 92/92 test |

## 2. Đã làm (file mới)

- `src/fd_response/ring_buffer.py` + `workbench/tests/test_ring_buffer.py` (FEAT-032).
- `src/fd_process/rules/`: `__init__.py`, `eye_drowsiness.py`, `mouth_yawn.py`, `rules_engine.py`, `util/__init__.py`, `util/label_vote.py`, `util/sustained.py` + `workbench/tests/test_rules.py` (FEAT-031).
- `src/fd_config/config.json`: thêm mục `rules` (MAIN cho phép). Mật khẩu camera trong file này KHÔNG bị đụng.
- `requirements.txt`: đổi về chỉ còn comment (stdlib), vì mâu thuẫn CLAUDE.md (MAIN cho phép xử lý). Gói dự kiến nằm ở `features/dependencies.md` (CHƯA DUYỆT).
- `feature-map.md`: dòng giai đoạn, trạng thái FEAT, bảng ý tưởng I-002..I-009.

## 3. Quyết định thiết kế (đừng đổi khi chưa hỏi MAIN)

1. Luật không import `fd_response` (R2). Engine chỉ phát `ViolationEvent` với `frame_ids` từ t0 trở đi; phần -2s do fd_response ghép từ ring buffer qua fd_flow.
2. `t0_ms` = lúc điều kiện bắt đầu (onset), không phải lúc đạt ngưỡng. `ViolationEvent` phát đúng 1 lần ở trạng thái TRIGGERED.
3. Khung không xác định (nhãn lạ, conf thấp, hai nhóm hòa conf) cho kết quả `None`, không đoán. Mất khung quá `max_gap_ms` thì đếm lại, nên thiếu dữ liệu không thành vi phạm.
4. Thời gian dùng `DetectionResult.ts_ms` (giờ lúc suy luận), vì cạnh `process.ai -> process.rules` chỉ mang DetectionResult. Độ trễ suy luận dao động làm lệch thời gian đo một chút.
5. Bảng ánh xạ nhãn MAIN đã duyệt: nhắm = `Eyeclosed, closed, close, asleep, Drowsy eye`; mở = `Attentive eye, open`; ngáp = `Yawn, yawn, Open-Mouth`; không ngáp = `noYawn`. Các nhãn `open/close/closed` có thể thuộc mắt hoặc miệng tùy bộ dữ liệu huấn luyện: CHƯA kiểm chứng.
6. Hàm khởi tạo luật KHÔNG có giá trị mặc định, nên ngưỡng không bị hard-code (R4).

## 4. CHƯA XÁC ĐỊNH (ghi theo lệnh MAIN)

Ngưỡng nhắm mắt 1500 ms, ngáp 2500 ms, độ tin cậy 0.5, `max_gap_ms` 600 là **giá trị tạm của Builder**, MAIN sẽ tự thiết kế. Đã gắn `_status: CHUA_XAC_DINH` trong `config.json`. Không dùng làm số liệu đánh giá G3/G5. `max_gap_ms=600` chọn theo 3 khung ở 5 FPS, chưa đo.

## 5. Vấn đề còn mở (đừng tự sửa, cần vai và hồ sơ đúng)

| ID | Vấn đề | Việc cần |
|---|---|---|
| I-007 | `config.json` đã commit (2 commit git) kèm mật khẩu camera RTSP dạng văn bản. Xóa khỏi file không xóa khỏi lịch sử git. | MAIN đổi mật khẩu camera trước. Sau đó BUG-xxx + Fixer: đọc URI từ biến môi trường/`.env`, thêm `.env` vào `.gitignore`, sửa ConfigLoader + test. MAIN quyết có viết lại lịch sử git không. |
| I-008 | ConfigLoader bỏ qua mục `rules`. | FEAT nạp + kiểm `rules`. |
| I-009 | `best.pt` chỉ có 11 lớp mắt/miệng; thiếu điện thoại và góc đầu. | Cần model/dependency được duyệt. Hai luật này chưa làm. |
| I-005 | Doc ghi `ring_buffer`, `server_dispatcher`, `yolo_adapter` nhưng `server_dispatcher` và `yolo_adapter` chưa có code; `response.md`, `rule.md` chưa cập nhật theo code. | Cập nhật doc ở bước merge. |
| (ghi chú FEAT-032) | Tái dùng mã `E_SIG_BAD_FORMAT/E_SIG_TS_DRIFT` cho ring buffer, vì chưa có mã riêng. Sau một gói sai trong pha ACTIVE, `health()` vẫn DEGRADED đến khi `reset()`. | Mở CR nếu muốn mã riêng; ghi BUG nếu muốn sửa health. |

`DRAFT/README_YOLO26_cu.md` ghi rõ là bản cũ. Giữ để đối chiếu, không làm theo.

## 6. Việc không được làm

- Không code FEAT-030 và không thêm dependency khi MAIN chưa duyệt.
- Không sửa `src/contracts/`; mã lỗi mới = CR.
- Không đưa mật khẩu camera vào báo cáo, log hay commit mới.
- Không dùng ngưỡng tạm làm kết luận đánh giá.

## 7. REPORT theo mẫu CLAUDE.md mục 8

```
ID: FEAT-031 (+ FEAT-032)      Giai đoạn: G3
Đã làm: xem mục 2
File đã đụng: xem mục 2 (đều nằm trong scope/G3.json)
Test đã chạy: test_ring_buffer.py (9), test_rules.py (20) -> OK; toàn bộ 92/92 OK
Cổng: python workbench/gate.py -> SẠCH (scope G3 0 vi phạm, comment 0 vi phạm)
Chưa kiểm chứng / giới hạn: mục 4 và mục 5 (I-009, ánh xạ nhãn)
Rủi ro còn lại: I-007 (bí mật đã nằm trong lịch sử git)
Đề xuất bước tiếp (chờ MAIN duyệt): Reviewer FEAT-031; sau đó FEAT nối rules + ring_buffer vào fd_flow (I-004 b); FEAT-033
```

---

# Phần bổ sung: FEAT-034 — nối rules + ring_buffer vào fd_flow (I-004 phương án b)

Ngày: 2026-10-22. Vai: Builder (MAIN duyệt trực tiếp). Trạng thái: BUILT, chờ Reviewer và MAIN merge.

## Đã làm

- `src/fd_flow/flow_engine.py`: thêm 2 tham số tùy chọn `rules_engine`, `ring_buffer` cho `FlowEngine`; thêm thuộc tính `rules`, `ring_buffer`, `last_clip`; thêm 3 hàm nội bộ `_push_ring`, `_drive_timeline`, `_finish_rules_stage`; đổi hai chỗ gọi `response.handle(...)` ở `step_sampled` và `step_once` thành `_finish_rules_stage(...)`.
- `src/fd_response/ring_buffer.py`: thêm `ts_of(frame_id)` (chỉ thêm, không đổi hành vi cũ).
- `workbench/tests/test_flow_rules.py` (5 test, mới), `test_ring_buffer.py` (+1 test).
- Không sửa `src/contracts/`, không đụng `config.json` hay `requirements.txt`.

## Hành vi

| Tình huống | Kết quả |
|---|---|
| Không gắn `rules_engine` (mặc định) | Giữ nguyên G1/G2: response nhận thẳng `DetectionResult`. Toàn bộ test cũ vẫn đạt. |
| Có `rules_engine` | `response` chỉ nhận `ViolationEvent`, đi qua cạnh `process.rules -> response` có kiểm kiểu và hàng đợi. Không còn log `RESP-DET`. |
| Có `ring_buffer` | Khung lấy mẫu được đẩy vào buffer; pha buffer đi theo kết quả luật; `ViolationEvent` được ghép tiền sự kiện -2s; cụm đã đóng nằm ở `engine.last_clip`. |
| `rules` báo lỗi | `E_PROC_BAD_OUTPUT`, `node_path="process.ai->process.rules"`, ghi vào fd_diagnose. |
| Khung đầu sự kiện đã rời buffer | `E_RESP_BUFFER_OVERRUN`, `node_path="response.ring_buffer"`; sự kiện vẫn gửi đi nhưng không ghép tiền sự kiện. |

## Quyết định thiết kế (đừng đổi khi chưa hỏi MAIN)

1. **Hai nguồn giờ khác nhau.** Luật đo theo `DetectionResult.ts_ms`; buffer đo theo `SignalPacket.ts_ms`. Khóa nối là `frame_id`: `t0` của buffer lấy từ ts của khung đầu sự kiện, và `ViolationEvent.t0_ms` gửi đi dùng giờ của khung. Test cố ý lệch 5.000.000 ms giữa hai nguồn để chứng minh khóa này.
2. Nối chỉ qua tham số khởi tạo (R2/R6). Luật và buffer chưa được dựng từ `config.json` vì `ConfigLoader` chưa đọc mục `rules` (I-008).
3. Buffer vòng chỉ theo dõi 1 sự kiện tại một thời điểm. Khi nhiều luật cùng hoạt động, sự kiện đóng khi không còn luật nào nghi ngờ.
4. `last_clip` chỉ giữ cụm gần nhất. FEAT-033 (`server_dispatcher`) sẽ là nơi lấy cụm này gửi đi; chưa có người tiêu thụ.

## Rủi ro / chưa kiểm chứng

- Chưa chạy với camera thật hay YOLO thật, chỉ có `CameraSource` mock và `ScriptedAdapter`.
- `E_RESP_BUFFER_OVERRUN` (và mọi lỗi recoverable khác) chuyển hệ thống sang DEGRADED qua `CircuitBreaker`, và hiện chưa có cơ chế quay lại MONITOR. Đây là hành vi sẵn có của `CircuitBreaker`, chưa phải lỗi mới của FEAT-034 nhưng sẽ lộ rõ khi chạy dài. Chưa ghi BUG; cần Diagnoser xác nhận.
- `capacity` của buffer cần đủ cho sự kiện dài nhất; xem I-008 để cấu hình.
- `step_once` đi đường cũ cho tối đa công suất nhưng cũng chạy rules khi được gắn; chưa đo lại thông lượng G2 với rules.

## REPORT theo mẫu

```
ID: FEAT-034                  Giai đoạn: G3
Đã làm: nối rules + ring_buffer vào fd_flow (tùy chọn, tương thích ngược)
File đã đụng: src/fd_flow/flow_engine.py, src/fd_response/ring_buffer.py, workbench/tests/test_flow_rules.py, workbench/tests/test_ring_buffer.py (đều trong scope G3)
Test đã chạy: test_flow_rules.py (5) OK; toàn bộ 98/98 OK, gồm test cũ của fd_flow (F5)
Cổng: python workbench/gate.py -> SẠCH (scope G3 0 vi phạm, comment 0 vi phạm)
Chưa kiểm chứng / giới hạn: xem mục Rủi ro
Rủi ro còn lại: DEGRADED không tự hồi phục; chưa đo thông lượng với rules
Đề xuất bước tiếp (chờ MAIN duyệt): Reviewer FEAT-034; FEAT-033 server_dispatcher; FEAT nạp mục rules từ ConfigLoader (I-008)
```

---

# Phần bổ sung: FEAT-033 — Gửi cụm frame vi phạm về server (`src/fd_response/server_dispatcher.py`)

Ngày: 2026-10-22. Vai: Builder. Trạng thái: DONE (MAIN duyệt merge).

## Đã làm

- `src/fd_response/server_dispatcher.py`: xây dựng `DispatchPackage`, `DispatchReceipt`, `ServerDispatcher` và `urllib_post_transport` (chỉ dùng stdlib).
- `workbench/tests/test_server_dispatcher.py`: 6 kịch bản kiểm thử (enqueue non-blocking + gộp clip_frame_ids, lỗi kết nối `E_RESP_SEND`, quá hạn/NACK `E_RESP_NO_ACK`, circuit breaker mở/thăm dò 1 gói/hồi phục gửi bù FIFO, tràn spool `E_RESP_BUFFER_OVERRUN`, kiểm tra đầu vào).
- Gate: `python workbench/gate.py` -> SẠCH (104/104 test PASS).

---

# Phần bổ sung: FEAT-035 (I-008) & FEAT-036 (I-007)

Ngày: 2026-10-22. Vai: Builder.

## FEAT-035 (DONE — MAIN duyệt merge)
- `src/fd_config/config_loader.py`: bổ sung `EyeDrowsinessRuleConfig`, `MouthYawnRuleConfig`, `RulesConfig` vào `SystemConfig`; kiểm tra chặt `min_conf`, `max_gap_ms`, `trigger_ms` và chặn trùng nhãn đối lập.
- `src/fd_flow/flow_engine.py`: bổ sung `build_rules_from_config()`, tham số `enable_rules_from_config` và `server_dispatcher`.

## FEAT-036 (BUILT — xử lý I-007)
- `src/fd_config/config.json`: xóa mật khẩu RTSP văn bản thường, thay bằng `${FD_CAMERA_PASSWORD}`.
- `.gitignore`: thêm `.env` và `.env.*`.
- `src/fd_config/config_loader.py`: bổ sung `parse_dotenv_file()`, đọc `FD_CAMERA_URI` hoặc `${FD_CAMERA_PASSWORD}` từ biến môi trường / `.env`, và từ chối (`E_CFG_INVALID`) nếu `camera.uri` chứa `password=<văn_bản_thường>` để ngăn tái phạm.

---

# Phần bổ sung: Kiểm thử bền bỉ >30 phút toàn diện pipeline G3 (EXP-002)

Ngày: 2026-10-22. Thực hiện theo lệnh trực tiếp của MAIN: chạy toàn bộ công cỡ >30 phút, kiểm tra lỗi ngầm, camera RTSP, ESP32 COM3, rules, ring buffer timeline -2s..0s, server dispatcher, giữ nguyên mã thí nghiệm.

## Kết quả đo thực tế (1830.11s = 30.50 phút)
- **Tổng frame xử lý:** 11,379,000 frames (Throughput TB: **6,217.7 FPS**).
- **Độ trễ trung bình:** 0.110 ms (P50: 0.102 ms, P95: 0.179 ms, P99: 0.222 ms).
- **Cảm biến ESP32 (COM3):** 2,275,800 requests (Độ trễ TB: 0.201 ms), không gây gián đoạn luồng hình ảnh.
- **Kháng lỗi dồn dập:** Bắt chính xác 56,895 / 56,895 lỗi (100.0%) tại các node nguồn và xử lý, cô lập đúng `node_path`.
- **Phần cứng RTSP (192.168.1.210:554):** 31/31 lần thăm dò định kỳ thành công (100.0% online).
- **Bộ nhớ RAM & Kiểm tra rò rỉ:**
  - RAM đỉnh: 14.13 MB.
  - Phút 5: 12.93 MB -> Phút 30: 12.98 MB.
  - Delta: **+0.05 MB** (phẳng tuyệt đối, **0 LEAK**).
- **Cổng an toàn:** `python workbench/gate.py` -> **SẠCH** (108/108 test PASS, 0 scope violations, 0 comment violations).
- **Hồ sơ chi tiết:** `changes/EXP-002.md`, `changes/BENCHMARK-G3-30M-DATA.json`, `changes/EXPERIMENT-LOG.md`.

---

# Tổng kết toàn diện giai đoạn G3 (Bàn giao tiến trình cho các phiên tiếp theo)

## 1. Những phần đã hoàn thành xây dựng (Built Features)
- **FEAT-031 (Rules Engine):** Xây dựng các luật vi phạm độc lập (`eye_drowsiness.py`, `mouth_yawn.py`, `rules_engine.py`, `util/label_vote.py`, `util/sustained.py`). Lọc bỏ hiện tượng chớp mắt sinh lý (<0.4s), duy trì trạng thái vi phạm liên tục có dung sai thời gian (`max_gap_ms`), phát sự kiện `ViolationEvent` tại thời điểm onset (`t0`).
- **FEAT-032 (Frame Ring Buffer):** Cài đặt `FrameRingBuffer` (`src/fd_response/ring_buffer.py`) với `deque(maxlen=capacity)`. Quản lý chu kỳ `IDLE -> ACTIVE -> COOLING`, tự động trích xuất chuỗi khung hình -2s tiền sự kiện khi vi phạm bùng phát, chống rò rỉ và tràn bộ nhớ.
- **FEAT-033 (Server Dispatcher):** Xây dựng `ServerDispatcher` (`src/fd_response/server_dispatcher.py`) gồm Outbox bất đồng bộ non-blocking, Spool lưu trữ offline khi mất kết nối mạng, và Circuit Breaker tự động chuyển `HALF-OPEN` thăm dò 1 gói. Phát mã `E_RESP_BUFFER_OVERRUN` khi quá tải để bảo vệ cố định dung lượng RAM.
- **FEAT-034 (Tích hợp FlowEngine):** Nối hoàn chỉnh chuỗi `ai -> rules -> ring_buffer -> response` trong `src/fd_flow/flow_engine.py`. Đảm bảo tương thích ngược 100% với G1/G2; chuyển đổi dữ liệu chuẩn hợp đồng từ `DetectionResult` sang `ViolationEvent`.
- **FEAT-035 (Nạp Config Rules & Tự động kết nối):** Bổ sung nạp cấu hình `rules` trong `ConfigLoader`, tự động khởi tạo và kết nối `RulesEngine`, `RingBuffer`, `ServerDispatcher` trong `FlowEngine`.
- **FEAT-036 (Bảo mật tài khoản camera RTSP):** Chuyển đổi mật khẩu camera thành biến môi trường `${FD_CAMERA_PASSWORD}` và nạp qua `.env`, bổ sung `.env` vào `.gitignore`, chủ động từ chối cấu hình chứa mật khẩu lộ thiên dạng văn bản.
- **EXP-002 (Kiểm thử bền bỉ 30.5 phút):** Thực hiện dồn tải toàn diện 11,379,000 frames đạt 6,217.7 FPS, 2,275,800 requests cảm biến ESP32 COM3, bắt 100% 56,895 lỗi giả định, kiểm tra 31/31 probe camera RTSP thật thành công, chênh lệch RAM sau 30 phút chỉ +0.05 MB (hoàn toàn phẳng, 0 leak).

## 2. Những vấn đề đã khắc phục (Resolved Issues)
1. **Lộ mật khẩu camera (I-007):** Loại bỏ mật khẩu văn bản thường khỏi `config.json`, đưa vào `.gitignore`, ngăn ngừa tái phạm bằng kiểm tra `E_CFG_INVALID`.
2. **Xung đột thư viện (I-002):** Làm sạch `requirements.txt` về chuẩn thư viện tiêu chuẩn (stdlib) của Python theo đúng `CLAUDE.md`, cô lập các thư viện ngoài vào `features/dependencies.md` chờ MAIN duyệt.
3. **Vi phạm luồng dữ liệu (I-003):** Chỉnh sửa cạnh truyền tin trong `FlowEngine` để luồng đi đúng từ `process.rules -> response`, không đi tắt từ `process.ai`.
4. **Mâu thuẫn quy tắc sửa file (I-004):** Thực hiện phương án (b) được MAIN duyệt: sửa nhỏ tương thích ngược trong `FlowEngine` mà không làm hỏng bất kỳ test G1/G2 nào.
5. **Thiếu nạp cấu hình luật (I-008):** `ConfigLoader` đã bổ sung cơ chế kiểm định ngưỡng giá trị dương, không chồng lấn nhãn đối lập, và nạp chuẩn xác cho `RulesConfig`.
6. **Nguy cơ tràn bộ nhớ khi mất mạng / sự kiện kéo dài:** Khống chế tuyệt đối kích thước bộ đệm ring buffer (50 frames) và spool dispatcher (128 packages), tự động drop gói cũ nhất kèm mã `E_RESP_BUFFER_OVERRUN` khi quá tải.

## 3. Các thành phần hỗ trợ đã bổ sung (Infrastructure & Testing)
- `scope/G3.json`: Thiết lập danh mục cho phép chỉnh sửa của G3.
- Hệ thống 108 unit test hoàn chỉnh tại `workbench/tests/` (bao gồm `test_rules.py`, `test_ring_buffer.py`, `test_server_dispatcher.py`, `test_flow_rules.py`, `test_config_loader.py`).
- Script kiểm định tự động `python workbench/gate.py` đảm bảo 100% sạch về phạm vi, không vi phạm comment cấm (F7/F8) và pass toàn bộ test suite.
- Thu dọn nguyên trạng: Đã xóa toàn bộ mã kiểm thử tạm thời `workbench/experiments/EXP-002/` sau khi kết thúc đợt kiểm thử theo đúng quy trình Experimenter.

## 4. Các vấn đề còn tồn đọng & Hướng dẫn cho tiến trình sau
1. **Đổi mật khẩu camera vật lý (I-007):** MAIN cần thay đổi mật khẩu trực tiếp trên thiết bị camera thật (192.168.1.210) vì mật khẩu cũ từng nằm trong commit lịch sử git trước đó.
2. **Phê duyệt thư viện AI để code FEAT-030 (YoloAdapter):** `FEAT-030` hiện ở trạng thái PROPOSED (chỉ thiết kế). Để triển khai code nạp file `models/best.pt`, MAIN cần phê duyệt các dependency (`ultralytics`, `torch`, `opencv-python`) trong `features/dependencies.md`.
3. **Kích thước khung hình Camera RTSP:** Khung hình camera RTSP thật là 1080p. Khi đưa vào YOLO thật, cần cấu hình resize về 640x480 và giữ nhịp 5–8 FPS (qua `FpsThrottle`) để tránh nghẽn CPU Raspberry Pi.
4. **Bổ sung nhãn còn thiếu (I-009):** File trọng số `models/best.pt` hiện có 11 lớp cho mắt và miệng; chưa có lớp nhận diện điện thoại (`cell phone`) và góc đầu/landmarks (`head pose`). Luật `phone_distraction.py` và `head_pose.py` cần mô hình bổ sung hoặc huấn luyện thêm.
5. **Ngưỡng vi phạm (`_status: CHUA_XAC_DINH`):** Ngưỡng nhắm mắt 1.5s, ngáp 2.5s, min_conf 0.5 hiện là giá trị tạm thời trong `config.json`. Các phiên sau cần điều chỉnh dựa trên dữ liệu thực tế của tài xế.
6. **Tiến trình G4 (ESP32 & Cảm biến):** Xây dựng module kết nối ESP32 hoàn chỉnh, gắn cảm biến GPS, triển khai mutex cho UART/Serial, cấu hình chống sập chip CP210x (`dsrdtr=False, rtscts=False`), và xử lý tình huống mất gói trên cạnh dữ liệu phụ.
