import { NotFoundException } from '../exceptions/index.js';

export const notFoundMiddleware = (req, _res, next) => {
	next(new NotFoundException(`Không tìm thấy đường dẫn: ${req.method} ${req.originalUrl}`));
};
