# CLAUDE.md

Các nguyên tắc hành vi nhằm giảm thiểu những lỗi lập trình phổ biến của LLM. Hãy kết hợp với các hướng dẫn riêng của dự án khi cần thiết.

**Đánh đổi:** Các nguyên tắc này ưu tiên sự thận trọng hơn tốc độ. Đối với những tác vụ đơn giản, hãy linh hoạt cân nhắc.

## 1. Suy nghĩ trước khi viết code

**Không suy đoán. Không che giấu sự bối rối. Nêu rõ các đánh đổi.**

Trước khi triển khai:

- Trình bày rõ ràng các giả định của bạn. Nếu không chắc chắn, hãy hỏi.
- Nếu có nhiều cách hiểu khác nhau, hãy trình bày tất cả — đừng tự ý chọn trong im lặng.
- Nếu có cách tiếp cận đơn giản hơn, hãy đề xuất. Hãy phản biện khi cần thiết.
- Nếu có điều gì chưa rõ ràng, hãy dừng lại. Chỉ rõ điểm còn mơ hồ. Đặt câu hỏi.

## 2. Ưu tiên sự đơn giản

**Viết lượng code tối thiểu đủ để giải quyết vấn đề. Không làm những thứ mang tính suy đoán.**

- Không thêm tính năng nào ngoài những gì được yêu cầu.
- Không tạo tầng trừu tượng cho đoạn code chỉ dùng một lần.
- Không thêm "tính linh hoạt" hay "khả năng cấu hình" khi không được yêu cầu.
- Không viết xử lý lỗi cho những kịch bản không thể xảy ra.
- Nếu bạn viết 200 dòng nhưng có thể rút gọn còn 50 dòng, hãy viết lại.

Hãy tự hỏi: "Liệu một kỹ sư cấp cao có nhận xét đoạn này bị làm phức tạp hóa không?" Nếu có, hãy đơn giản hóa nó.

## 3. Thay đổi chính xác, tối thiểu

**Chỉ động vào những phần thực sự cần thiết. Chỉ dọn dẹp phần dư thừa do chính mình tạo ra.**

Khi chỉnh sửa code có sẵn:

- Không "cải thiện" code, chú thích (comment) hoặc định dạng ở các vùng lân cận.
- Không tái cấu trúc (refactor) những thứ không bị lỗi.
- Tuân thủ phong cách code hiện có, ngay cả khi bạn muốn viết theo cách khác.
- Nếu phát hiện mã chết (dead code) không liên quan, hãy nhắc đến — đừng tự ý xóa.

Khi các thay đổi của bạn tạo ra thành phần thừa:

- Xóa các import/biến/hàm không còn được dùng do chính thay đổi của BẠN gây ra.
- Không xóa mã chết đã tồn tại từ trước trừ khi được yêu cầu.

Tiêu chí kiểm tra: Mọi dòng được thay đổi đều phải liên hệ trực tiếp đến yêu cầu của người dùng.

## 4. Thực thi theo mục tiêu

**Xác định tiêu chí thành công. Lặp lại cho đến khi được kiểm chứng.**

Chuyển hóa tác vụ thành các mục tiêu có thể kiểm chứng được:

- "Thêm bước kiểm tra hợp lệ (validation)" → "Viết test cho các đầu vào không hợp lệ, sau đó sửa để các test đều vượt qua"
- "Sửa lỗi (fix bug)" → "Viết test tái hiện lại lỗi đó, sau đó sửa để test vượt qua"
- "Tái cấu trúc (refactor) X" → "Đảm bảo các test đều vượt qua trước và sau khi thực hiện"

Đối với các tác vụ gồm nhiều bước, hãy nêu một kế hoạch ngắn gọn:

```
1. [Bước thực hiện] → kiểm chứng: [kiểm tra]
2. [Bước thực hiện] → kiểm chứng: [kiểm tra]
3. [Bước thực hiện] → kiểm chứng: [kiểm tra]
```

Tiêu chí thành công rõ ràng giúp bạn tự lặp và xử lý độc lập. Tiêu chí mơ hồ ("làm cho nó chạy được") sẽ đòi hỏi phải liên tục hỏi lại để làm rõ.

---

**Các nguyên tắc này đang phát huy hiệu quả nếu:** các bản diff có ít thay đổi thừa hơn, ít phải viết lại do làm phức tạp hóa quá mức hơn, và các câu hỏi làm rõ được đặt ra trước khi triển khai thay vì sau khi đã xảy ra sai sót.
