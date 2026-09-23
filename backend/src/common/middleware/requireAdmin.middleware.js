import { ForbiddenException } from '../exceptions/index.js';

/**
 * Middleware kiểm tra quyền Admin.
 * Phải chạy SAU authMiddleware để có req.user.role.
 * Đọc role từ JWT payload (gắn vào req.user bởi authMiddleware).
 */
export const requireAdmin = (req, _res, next) => {
	if (!req.user || req.user.role !== 'ADMIN') {
		return next(new ForbiddenException('Bạn không có quyền truy cập chức năng này.'));
	}
	next();
};
