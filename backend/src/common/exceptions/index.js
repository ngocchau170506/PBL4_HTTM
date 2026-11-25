export class AppError extends Error {
	constructor(status, message, { code = null, details = null } = {}) {
		super(message);
		this.name = this.constructor.name;
		this.status = status;
		this.code = code;
		this.details = details;
		this.isOperational = true;
		Error.captureStackTrace?.(this, this.constructor);
	}
}

export class BadRequestException extends AppError {
	constructor(message = 'Yêu cầu không hợp lệ.', details = null) {
		super(400, message, { code: 'BAD_REQUEST', details });
	}
}

export class UnauthorizedException extends AppError {
	constructor(message = 'Chưa xác thực hoặc phiên đăng nhập không hợp lệ.', details = null) {
		super(401, message, { code: 'UNAUTHORIZED', details });
	}
}

export class ForbiddenException extends AppError {
	constructor(message = 'Bạn không có quyền truy cập chức năng này.', details = null) {
		super(403, message, { code: 'FORBIDDEN', details });
	}
}

export class NotFoundException extends AppError {
	constructor(message = 'Không tìm thấy tài nguyên yêu cầu.', details = null) {
		super(404, message, { code: 'NOT_FOUND', details });
	}
}

export class ConflictException extends AppError {
	constructor(message = 'Dữ liệu đã tồn tại hoặc xảy ra xung đột.', details = null) {
		super(409, message, { code: 'CONFLICT', details });
	}
}

export class UnprocessableEntityException extends AppError {
	constructor(message = 'Dữ liệu đầu vào không hợp lệ.', details = null) {
		super(422, message, { code: 'VALIDATION_ERROR', details });
	}
}
