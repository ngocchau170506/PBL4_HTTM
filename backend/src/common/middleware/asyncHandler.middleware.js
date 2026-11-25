/**
 * Bọc async route handler để tự động đẩy lỗi sang errorHandlerMiddleware qua next(err),
 * loại bỏ hoàn toàn try/catch lặp lại ở từng controller.
 */
export const asyncHandler = (fn) => (req, res, next) => {
	Promise.resolve(fn(req, res, next)).catch(next);
};
