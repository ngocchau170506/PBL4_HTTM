import { HttpResponse } from '../dtos/index.js';
import { StatusCodes } from 'http-status-codes';
export const errorHandlerMiddleware = (err, req, res, next) => {
	if (err && err.status) {
		return new HttpResponse(res).exception(err);
	}
	console.error('INTERNAL ERROR:', err);
	return res.status(StatusCodes.INTERNAL_SERVER_ERROR).json({
		success: false,
		message: 'Internal Server Error. Please try again later.',
		// Chỉ hiện chi tiết lỗi nếu đang ở môi trường dev
		error: process.env.NODE_ENV === 'development' ? err.message : undefined,
	});
};
