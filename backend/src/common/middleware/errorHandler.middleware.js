import logger from '../utils/logger.js';

const normalizeError = (err) => {
	if (!err) {
		return {
			status: 500,
			code: 'INTERNAL_SERVER_ERROR',
			message: 'Lỗi hệ thống nội bộ. Vui lòng thử lại sau.',
			details: null,
		};
	}

	// 1. Operational errors from AppError subclasses
	if (err.isOperational) {
		return {
			status: err.status,
			code: err.code || (err.status === 404 ? 'NOT_FOUND' : 'CLIENT_ERROR'),
			message: err.message || 'Yêu cầu không hợp lệ.',
			details: err.details || err.errors || null,
		};
	}

	// 2. Malformed JSON payload from express.json() (has err.status === 400 + err.body)
	if (err instanceof SyntaxError && err.status === 400 && 'body' in err) {
		return {
			status: 400,
			code: 'INVALID_JSON',
			message: 'Định dạng JSON gửi lên không hợp lệ.',
			details: null,
		};
	}

	// 3. Prisma ORM known errors
	if (err.code === 'P2002') {
		const target = Array.isArray(err.meta?.target) ? err.meta.target.join(', ') : 'unique';
		return {
			status: 409,
			code: 'PRISMA_UNIQUE_CONFLICT',
			message: `Dữ liệu bị trùng lặp (${target}).`,
			details: err.meta || null,
		};
	}
	if (err.code === 'P2025') {
		return {
			status: 404,
			code: 'PRISMA_NOT_FOUND',
			message: 'Không tìm thấy bản ghi cần xử lý.',
			details: null,
		};
	}
	if (err.code === 'P2003') {
		return {
			status: 400,
			code: 'PRISMA_FK_ERROR',
			message: 'Dữ liệu liên kết không hợp lệ hoặc không tồn tại.',
			details: null,
		};
	}
	if (err.name === 'PrismaClientValidationError') {
		return {
			status: 400,
			code: 'PRISMA_VALIDATION_ERROR',
			message: 'Dữ liệu truy vấn không hợp lệ.',
			details: null,
		};
	}

	// 4. Multer upload errors
	if (err.name === 'MulterError') {
		return {
			status: 400,
			code: err.code || 'UPLOAD_ERROR',
			message:
				err.code === 'LIMIT_FILE_SIZE'
					? 'Dung lượng tệp vượt quá giới hạn cho phép.'
					: err.message || 'Lỗi tải tệp lên hệ thống.',
			details: null,
		};
	}

	// 5. JWT signature / expiration errors
	if (err.name === 'JsonWebTokenError' || err.name === 'TokenExpiredError') {
		return {
			status: 401,
			code: 'INVALID_TOKEN',
			message: 'Token xác thực không hợp lệ hoặc đã hết hạn.',
			details: null,
		};
	}

	// 6. CORS rejection
	if (typeof err.message === 'string' && err.message.startsWith('CORS:')) {
		return {
			status: 403,
			code: 'CORS_BLOCKED',
			message: err.message,
			details: null,
		};
	}

	// 7. Other explicit 4xx HTTP errors attached to Error objects
	if (Number.isInteger(err.status) && err.status >= 400 && err.status < 500) {
		return {
			status: err.status,
			code: err.code || (err.status === 404 ? 'NOT_FOUND' : 'CLIENT_ERROR'),
			message: err.message || 'Yêu cầu không hợp lệ.',
			details: err.details || err.errors || null,
		};
	}

	// 8. Fallback 500 Internal Error
	return {
		status: 500,
		code: 'INTERNAL_SERVER_ERROR',
		message: 'Lỗi hệ thống nội bộ. Vui lòng thử lại sau.',
		details: null,
	};
};

export const errorHandlerMiddleware = (err, req, res, _next) => {
	const { status, code, message, details } = normalizeError(err);

	if (status >= 500) {
		logger.error(`[${req.method} ${req.originalUrl}] ${status}:`, err?.stack || err?.message || err);
	} else {
		logger.warn(`[${req.method} ${req.originalUrl}] ${status} (${code}): ${message}`);
	}

	const errorBody = {
		message,
		code,
		...(details ? { details } : {}),
		...(status >= 500 && process.env.NODE_ENV === 'development' && err?.stack
			? { stack: err.stack }
			: {}),
	};

	return res.status(status).json({
		success: false,
		message,
		error: errorBody,
	});
};
