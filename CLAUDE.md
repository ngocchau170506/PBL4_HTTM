# CLAUDE.md — Luật bắt buộc cho mọi AI làm việc trong dự án

Dự án: Hệ thống giám sát tài xế (Camera + ESP32 + Raspberry; mô phỏng trên máy tính trước).
Ngôn ngữ: Python. Chỉ dùng thư viện chuẩn cho tới khi MAIN duyệt dependency.
"MAIN" = chủ dự án (con người). Chỉ MAIN được: ghi GATE, duyệt CR, sửa comment_allowlist.txt, duyệt dependency.

Trạng thái tài liệu này: ĐÃ DUYỆT (MAIN ghi "DUYỆT LUẬT" vào feature-map.md ngày 2026-10-20). Muốn đổi luật phải qua Change Request.

## 0. Thứ tự đọc (bắt buộc trước khi làm bất cứ việc gì)

1. CLAUDE.md (file này)
2. agents.md (bạn đang đóng vai nào? Không có `ROLE:` thì chỉ được đọc và hỏi)
3. feature-map.md (giai đoạn hiện tại, dòng `GIAI ĐOẠN HIỆN TẠI`)
4. scope/<giai đoạn>.json (danh sách file được phép đụng)
5. features/component.txt (hợp đồng từng component)
6. src/contracts/logic.py, src/contracts/state.py (kiểu dữ liệu, luật luồng)
   Không đọc xong = không được sửa code.

## 1. Quy trình 1 việc (KHÔNG ĐƯỢC làm 1 lần hết)

PROPOSE -> MAIN DUYỆT -> BUILD -> TEST -> REPORT -> MAIN DUYỆT -> MERGE

- PROPOSE: nêu việc, file sẽ đụng (phải nằm trong scope), rủi ro, cách test. Chưa viết code.
- BUILD: chỉ trong phạm vi đã duyệt. Mỗi lượt tối đa 1 component.
- TEST: chạy test độc lập ở workbench/ (không chỉ nhìn Terminal).
- REPORT: dùng mẫu ở mục 8. Không báo "xong" nếu chưa có kết quả thật của `python workbench/gate.py`.
- Chuyển giai đoạn chỉ khi MAIN ghi "GATE Gx: PASS" vào feature-map.md.

## 2. Luật kiến trúc

- R1. Mỗi file 1 vai trò. Thuật toán KHÔNG chung file với xử lí ảnh/tín hiệu/kết nối/phản hồi.
- R2. Các fd chỉ nói chuyện qua fd_flow. Cấm import chéo giữa fd (trừ fd_flow được nối các fd). Kiểm bằng check_scope.py [S2].
- R3. Dữ liệu giữa fd chỉ đi bằng kiểu trong src/contracts/. Đổi kiểu = Change Request.
- R4. Mọi cấu hình (địa chỉ cam, fps, ngưỡng, timeout...) nằm trong src/fd_config/config.json, không hard-code.
- R5. Mọi gói tin có trace_id; mọi lỗi có mã lỗi (ErrorCode) + node_path.
- R6. AI/model (YOLO...) chỉ cắm qua giao diện AiAdapter. Phải chạy được bằng StubAdapter trước.
- R7. Phần nặng không chạy nổi trên Raspberry: để ở lớp "offload", không làm ô nhiễm lõi.

## 3. Luật sửa lỗi và chống đi đường vòng

- F1. Cấm sửa lỗi dựa riêng vào output Terminal. Phải tái hiện lỗi ở workbench/ rồi tìm nguyên nhân.
- F2. Mỗi bug có 1 hồ sơ: changes/BUG-xxx.md (dùng changes/BUG-TEMPLATE.md).
- F3. Cấm "sửa kèm": 1 bug = 1 thay đổi nhỏ nhất. Thấy lỗi khác -> ghi BUG mới, KHÔNG sửa luôn.
- F4. Cấm nuốt lỗi (except rỗng, bỏ warning, hạ log level để im). Warning phải được xử lí hoặc ghi rõ lí do chấp nhận.
- F5. Sửa xong phải chạy lại: test của node đó + test tích hợp của node kề (theo cây luồng).
- F6. Không đổi hợp đồng để "cho test qua". Test sai thì báo MAIN.
- F7. Cấm dùng comment để đi đường vòng: comment tắt kiểm tra (`# noqa`, `type: ignore`, `pragma: no cover`), comment giữ chỗ (`TODO`, `FIXME`, `...`, "làm sau"), code bị comment lại để né lỗi, hàm/khối chỉ có comment hoặc `pass`, `except` rỗng. Chưa làm xong thì báo BLOCKED, không để code giả. Comment giải thích "vì sao" được phép.
- F8. `python workbench/gate.py` phải SẠCH trước mọi REPORT. Ngoại lệ comment chỉ MAIN ghi vào workbench/comment_allowlist.txt. AI không được sửa file đó, không được sửa script kiểm để né.

## 4. Cấm tuyệt đối

- Viết lại/refactor lớn khi chưa có Change Request được duyệt.
- Đụng file ngoài scope/<giai đoạn>.json.
- Tự thêm dependency (kể cả sửa requirements.txt) khi chưa có MAIN duyệt.
- Xóa test, hạ ngưỡng đánh giá, sửa số liệu để "đạt".
- Chạy lệnh Terminal ảnh hưởng hệ thống khi chưa được MAIN cho phép (mục 6).
- Sửa scope/\*.json, feature-map.md (trừ mục kế hoạch), hoặc src/contracts/ khi chưa có CR.

## 5. Khi thiếu thông tin

Dừng, hỏi MAIN bằng tối đa 3 câu hỏi cụ thể. Không tự đoán rồi làm tiếp.

## 6. Lệnh Terminal

- Được phép tự do: `python workbench/gate.py`, `python workbench/check_scope.py <Gx>`, `python workbench/check_comments.py <đường dẫn>`, `python -m unittest discover -s workbench/tests`, và mọi lệnh chỉ đọc.
- Cần MAIN cho phép: cài package, mở camera/cổng serial/ESP32, ghi đè file cấu hình, xóa file, chạy dài hơn 60s, mở mạng, `git` ghi vào repo.

## 7. Ghi nhận thay đổi

Mỗi lần merge: cập nhật feature-map.md (trạng thái) + 1 dòng vào changes/LOG.md (ngày, ID, fd, tóm tắt, ai duyệt).

## 8. Mẫu REPORT (bắt buộc)

```
ID: <FEAT/BUG/CR-xxx>      Giai đoạn: Gx
Đã làm: ...
File đã đụng: ... (phải khớp scope)
Test đã chạy: <tên test> -> <kết quả thật>
Cổng: python workbench/gate.py -> <SẠCH / CÓ VI PHẠM + nội dung>
Chưa kiểm chứng / giới hạn: ...
Rủi ro còn lại: ...
Đề xuất bước tiếp (chờ MAIN duyệt): ...
```

## 9. Nhiều công cụ AI

Luật này áp dụng cho Claude Code, Antigravity, Gemini, GPT và mọi công cụ khác.

- Công cụ tự đọc file này (Claude Code): làm theo như trên.
- Công cụ không tự đọc: MAIN dán nguyên mục 0, 1, 3, 4 và dòng `ROLE:` vào đầu phiên.
- Chốt chặn thật không nằm ở lời hứa của AI mà ở `python workbench/gate.py` và phạm vi trong scope/\*.json. Mọi công cụ đều phải chạy được cổng này.
- Xây dựng để agent hiểu bản chất vấn đề ngay trước khi xảy ra chứ không nhất thiết phải trong quá trình xử lý , hãy giả định các lỗi sẽ xảy ra và đưa ra các đề xuất giải pháp phù hợp cho MAIN và không để phát sinh những cấu trúc rườm rà không cần thiết .
- Kiểm tra và giải phóng bộ nhớ nếu gặp tình trạng lỗi đè lỗi hoặc lỗi phát sinh trước/sau nhưng vẫn còn ảnh hưởng đến quá trình xử lý mà agent nhận định đúng .

## 10. Thí nghiệm hiệu năng và chất lượng (chỉ khi MAIN yêu cầu)

Kích hoạt: MAIN ghi `ROLE: Experimenter. EXP-xxx. Mục tiêu: <đo gì, đạt ngưỡng nào>`. Không có yêu cầu thì AI không tự mở thí nghiệm.

Đã được MAIN duyệt trước cho vai Experimenter (không cần hỏi lại từng bước):

- Tạo, chạy, xóa mã thí nghiệm trong `workbench/experiments/EXP-xxx/`.
- Chạy lệnh dài hơn 60s nằm trong thư mục đó.
- Tối ưu thử ngay trong lúc thí nghiệm (ngưỡng, fps, kích thước buffer, tham số model) trên bản sao cấu hình đặt trong thư mục thí nghiệm. Không ghi đè `src/fd_config/config.json`.

Vẫn cấm hoặc vẫn phải xin MAIN:

- Sửa `src/` hoặc `src/contracts/`: phải đi theo PROPOSE hoặc Change Request như thường lệ (F3: không sửa kèm).
- Mở camera, cổng serial, ESP32, mạng, cài package: MAIN phải cho phép trong chính EXP đó.
- Hạ ngưỡng đánh giá, sửa số liệu, nuốt lỗi (mục 4, F4, F7 vẫn áp dụng).
- Để mã thí nghiệm lọt vào `src/`.

Mỗi thí nghiệm có hồ sơ `changes/EXP-xxx.md` (mẫu `changes/EXP-TEMPLATE.md`) và một dòng trong `changes/EXPERIMENT-LOG.md`. Trạng thái phải ghi trung thực, dùng đúng các mã sau:

| Mã                | Ý nghĩa                                                                              |
| ----------------- | ------------------------------------------------------------------------------------ |
| CHUA_SUA_DUOC     | Đã thấy vấn đề, chưa sửa được                                                        |
| SAI_NGAM          | Kết quả trông đúng nhưng sai ngầm; ghi cách đã phát hiện                             |
| FIX_CO_VAN_DE     | Bản sửa gây vấn đề mới hoặc không đạt; ghi vấn đề đó                                 |
| DA_GHI_BAO_CAO    | Đã ghi báo cáo đầy đủ vào EXP-xxx.md                                                 |
| CAN_PHAT_HIEN_LOI | Cần thêm hoặc sửa nơi phát hiện lỗi (ErrorEvent); ghi node_path và ErrorCode đề xuất |
| DA_XOA_THI_NGHIEM | Đã xóa mã thí nghiệm, chỉ giữ kết luận cho các giai đoạn sau                         |

Quy tắc kết thúc:

- Chỉ xóa `workbench/experiments/EXP-xxx/` khi trạng thái đã có `DA_GHI_BAO_CAO`. Hồ sơ `EXP-xxx.md` và số đo trước/sau không bao giờ bị xóa.
- Kết luận dùng cho giai đoạn sau ghi vào mục "Kết luận cho các giai đoạn sau" của EXP-xxx.md và một dòng tóm tắt vào mục "Bài học thí nghiệm" của feature-map.md.
- Không báo "đã tối ưu" nếu thiếu số đo trước và sau, cùng điều kiện đo (máy, fps, độ phân giải, số mẫu).
- Phát hiện kiểu lỗi mới: ghi vị trí đề xuất. ErrorCode mới = Change Request. Thêm kiểm tra phát hiện lỗi trong `src/` = PROPOSE riêng.
- Hãy kiểm tra kỹ và đảm bảo rằng trước khi đưa ra bất cứ giải pháp nào , hãy lường trước các tình huống có thể xảy ra và đưa ra các đề xuất phù hợp , không để phát sinh những cấu trúc rườm rà không cần thiết .
- Khi phát sinh hay tạo ra cần truy vấn để biết MAIN đang xử lí hoặc có thể kiểm tra tại các file và hiểu được agent đang và làm những gì để tránh những trường hợp lỗi chồng chất.
- Khi đóng báo cáo cần ghi rõ ngày giờ thực hiện để có thể đối chiếu các thay đổi sau này .
