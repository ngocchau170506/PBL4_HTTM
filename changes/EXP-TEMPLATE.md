# EXP-xxx — <tên thí nghiệm>

- Ngày bắt đầu / kết thúc:
- Giai đoạn liên quan: Gx
- Yêu cầu bởi MAIN: <nguyên văn mục tiêu>
- Trạng thái (một hoặc nhiều mã trong CLAUDE.md mục 10): CHUA_SUA_DUOC | SAI_NGAM | FIX_CO_VAN_DE | DA_GHI_BAO_CAO | CAN_PHAT_HIEN_LOI | DA_XOA_THI_NGHIEM

## Điều kiện đo
Máy (PC hay Raspberry), CPU/RAM, nguồn video, fps, độ phân giải, số mẫu, số lần lặp.

## Tham số đã thử
Mỗi dòng một lần thử: tham số đổi, giá trị trước -> sau. Bản sao cấu hình dùng nằm ở `workbench/experiments/EXP-xxx/`.

## Số đo trước / sau
| Chỉ số | Trước | Sau | Ghi chú |
|---|---|---|---|
| FPS thực | | | |
| Độ trễ (ms) | | | |
| Tỷ lệ phát hiện đúng | | | |
| Tỷ lệ báo sai | | | |
| RAM / nhiệt (nếu đo) | | | |

## Vấn đề phát hiện
Mỗi vấn đề một mục, gắn mã trạng thái:
- [CHUA_SUA_DUOC] mô tả, đã thử gì, vì sao chưa sửa được.
- [SAI_NGAM] kết quả trông đúng nhưng sai; cách đã phát hiện ra.
- [FIX_CO_VAN_DE] bản sửa nào, gây vấn đề gì.

## Nơi phát hiện lỗi cần thêm hoặc sửa
Mỗi dòng: node_path | ErrorCode hiện có hoặc đề xuất mới | cần CR? | gợi ý kiểm tra gì.

## Kết luận cho các giai đoạn sau
Ngắn, dùng được mà không cần đọc lại cả thí nghiệm. Có thể áp dụng vào giai đoạn nào, giá trị khuyến nghị và điều kiện đúng.

## Mã thí nghiệm
- Đường dẫn: `workbench/experiments/EXP-xxx/`
- Đã xóa vào ngày: (điền khi DA_XOA_THI_NGHIEM; chỉ xóa sau khi đã DA_GHI_BAO_CAO)
