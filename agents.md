# agents.md — Vai trò & quyền hạn của AI

Một AI tại một thời điểm chỉ đóng MỘT vai. MAIN chỉ định vai trong lệnh đầu phiên: `ROLE: <tên>`.
Không có ROLE = chỉ được đọc và hỏi, không được sửa file.

| Vai            | Làm được                                                                                                                                                                                                                       | Không được                                                                                                                                             |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Planner        | Viết PROPOSE, chia task nhỏ, cập nhật feature-map.md (mục kế hoạch)                                                                                                                                                            | Viết code                                                                                                                                              |
| Builder        | Viết code trong 1 component đã duyệt                                                                                                                                                                                           | Đổi contracts, đụng fd khác, tự sửa bug phát sinh                                                                                                      |
| Tester         | Viết/chạy test, workbench script, đo metrics                                                                                                                                                                                   | Sửa code chính                                                                                                                                         |
| Diagnoser      | Tái hiện lỗi ở workbench, tìm nguyên nhân gốc, ghi BUG-xxx.md                                                                                                                                                                  | Sửa code chính                                                                                                                                         |
| Fixer          | Áp dụng đúng 1 BUG đã có hồ sơ, sửa nhỏ nhất                                                                                                                                                                                   | Sửa ngoài hồ sơ BUG                                                                                                                                    |
| Fault Injector | Tạo lỗi giả (xem mục 3) để thử hệ thống tự báo lỗi đúng chỗ                                                                                                                                                                    | Để lỗi giả lọt vào nhánh chính                                                                                                                         |
| Reviewer       | Đối chiếu diff với phạm vi duyệt + CLAUDE.md, trả PASS/FAIL                                                                                                                                                                    | Sửa code                                                                                                                                               |
| Integrator     | Nối các fd qua fd_flow, chạy test tích hợp                                                                                                                                                                                     | Sửa logic bên trong fd                                                                                                                                 |
| Experimenter   | Chỉ khi MAIN yêu cầu: chạy thí nghiệm hiệu năng/chất lượng trong `workbench/experiments/EXP-xxx/`, tối ưu thử trên bản sao cấu hình, ghi hồ sơ `changes/EXP-xxx.md`, xóa mã thí nghiệm sau khi ghi kết luận (CLAUDE.md mục 10) | Sửa `src/` hoặc `contracts/`, ghi đè `config.json`, mở camera/serial/ESP32/mạng khi chưa được MAIN cho phép, báo "đã tối ưu" khi thiếu số đo trước/sau |

## 1. Phân quyền theo fd (ai được ghi vào đâu)

| Thư mục                                                      | Builder                            | Fixer        | Ghi chú                                                              |
| ------------------------------------------------------------ | ---------------------------------- | ------------ | -------------------------------------------------------------------- |
| src/fd_connect                                               | có                                 | có           | Cam, ESP32, serial/IP                                                |
| src/fd_signal                                                | có                                 | có           | Nhận & chuẩn hóa tín hiệu/ảnh thô                                    |
| src/fd_process                                               | có                                 | có           | Thuật toán; mỗi thuật toán 1 file                                    |
| src/fd_flow                                                  | Integrator                         | Integrator   | Cây luồng + cache điều phối                                          |
| src/fd_response                                              | có                                 | có           | Phản hồi/cảnh báo/gửi server                                         |
| src/fd_diagnose                                              | Diagnoser                          | —            | Tìm nguyên nhân, KHÔNG nằm trong đường chạy chính                    |
| src/fd_faultsim                                              | Fault Injector                     | —            | Mô phỏng lỗi                                                         |
| src/fd_registry                                              | Planner/Integrator                 | —            | Sổ cập nhật nhánh                                                    |
| src/contracts                                                | CHỈ qua Change Request, MAIN duyệt | —            | logic.py, state.py                                                   |
| workbench/experiments                                        | Experimenter                       | Experimenter | Mã thí nghiệm tạm, xóa sau khi ghi kết luận; không import vào `src/` |
| workbench/comment_allowlist.txt, workbench/check_comments.py | CHỈ MAIN                           | CHỈ MAIN     | Kiểm comment (F7/F8); Tester chỉ được chạy, không được sửa           |

## 2. Quy trình giao việc (handoff)

1. MAIN: `ROLE: Planner. Việc: <mô tả>.`
2. Planner trả PROPOSE -> MAIN duyệt, gắn ID (FEAT-xxx).
3. MAIN: `ROLE: Builder. FEAT-xxx. Phạm vi: <thư mục/file>.`
4. Builder trả code + REPORT. MAIN: `ROLE: Tester`.
5. Tester trả kết quả test. Có lỗi -> `ROLE: Diagnoser` -> hồ sơ BUG -> `ROLE: Fixer`.
6. `ROLE: Reviewer` -> PASS/FAIL -> MAIN merge.
   Mỗi bước dừng chờ MAIN. Không agent nào tự chuyển sang bước sau.

## 3. Cơ chế mô phỏng lỗi (Fault Injection)

Mục tiêu: thử xem prompt/yêu cầu sai có bị hệ thống chặn và đưa về nhánh <xử lí> không.

- Lỗi prompt: yêu cầu đụng file ngoài phạm vi, yêu cầu gộp thuật toán vào file xử lí ảnh, yêu cầu bỏ qua test.
  -> Agent PHẢI từ chối + ghi `E_SCOPE_VIOLATION`, đề nghị MAIN ra Change Request.
- Lỗi dữ liệu: cam rớt khung, ảnh đen, fps lệch, ESP32 mất gói, gói trễ, trace_id thiếu.
  -> Node phải phát `ErrorEvent` đúng mã + nodePath, không crash im lặng.
- Lỗi kết nối ngầm giữa 2+ file: gói sai kiểu ở cạnh (edge) cây luồng.
  -> fd_flow báo cạnh nào (từ A tới B), không báo chung chung.
- Kịch bản lỗi đặt trong `workbench/faults/*.yaml`; chạy bằng lệnh MAIN cho phép.

## 4. Cache xử lí / "hệ điều hành nhỏ" (fd_flow)

Nhiệm vụ: hàng đợi gói theo node, theo dõi trạng thái từng node, khi có lỗi thì (1) cô lập node/cạnh lỗi, (2) trả ErrorEvent có nodePath, (3) đẩy hồ sơ sang fd_diagnose, (4) cho phần còn lại chạy tiếp ở chế độ suy giảm nếu hợp đồng cho phép. Không tự sửa.

## 5. Điều kiện dừng khẩn

Agent dừng và hỏi MAIN nếu: test tích hợp đỏ sau 2 lần sửa, cần đổi contracts, phát hiện lỗi nằm ở nhiều hơn 2 fd, hoặc yêu cầu mâu thuẫn với CLAUDE.md.

## 6. Phạm vi giai đoạn (scope)

Mỗi giai đoạn có scope/<Gx>.json. AI chỉ được ghi file nằm trong danh sách đó.
Kiểm bằng `python workbench/gate.py` (gọi check_scope). Đụng file ngoài scope = E_SCOPE_VIOLATION.

## 7. Quy trình thí nghiệm (Experimenter)

1. MAIN: `ROLE: Experimenter. EXP-xxx. Mục tiêu: <đo gì, ngưỡng nào>.` Experimenter tạo `changes/EXP-xxx.md` từ `changes/EXP-TEMPLATE.md` và thêm một dòng vào `changes/EXPERIMENT-LOG.md`.
2. Chạy, đo, tối ưu thử trong `workbench/experiments/EXP-xxx/`. Mỗi lần đổi tham số ghi số đo trước/sau vào hồ sơ.
3. Gặp tình huống nào thì ghi đúng mã trạng thái (CHUA_SUA_DUOC, SAI_NGAM, FIX_CO_VAN_DE, CAN_PHAT_HIEN_LOI), không giấu, không bỏ qua.
4. Xong: ghi báo cáo (DA_GHI_BAO_CAO) -> xóa `workbench/experiments/EXP-xxx/` (DA_XOA_THI_NGHIEM) -> chuyển kết luận sang feature-map.md mục "Bài học thí nghiệm".
5. Muốn áp kết quả vào `src/`: Experimenter chỉ đề xuất; việc sửa đi theo PROPOSE (hoặc CR nếu đụng contracts) với ROLE Builder/Fixer.
   Điều kiện dừng khẩn ở mục 5 vẫn áp dụng cho Experimenter.
