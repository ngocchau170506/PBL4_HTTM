import jwt from 'jsonwebtoken';
import { UnauthorizedException } from '../exceptions/index.js';
import prisma from '../../config/database.js';

const ACCESS_JWT_SECRET = process.env.ACCESS_JWT_SECRET;

export const authMiddleware = async (req, res, next) => {
	try {
		const authHeader = req.headers.authorization;

		if (!authHeader || !authHeader.startsWith('Bearer ')) {
			throw new UnauthorizedException('Authentication required');
		}

		const token = authHeader.split(' ')[1];

		// Verify token
		const payload = jwt.verify(token, ACCESS_JWT_SECRET);

		// Kiểm tra user có bị khóa không (isBanned)
		const user = await prisma.user.findUnique({
			where: { id: payload.sub },
			select: { isBanned: true }
		});

		if (!user || user.isBanned) {
			throw new UnauthorizedException('Tài khoản đã bị khóa hoặc không tồn tại');
		}

		// Attach user info to request
		req.user = { id: payload.sub, role: payload.role };

		next();
	} catch (error) {
		if (error.status) {
			next(error);
		} else if (error.name === 'TokenExpiredError') {
			next(new UnauthorizedException('Access token expired'));
		} else {
			next(new UnauthorizedException('Invalid access token'));
		}
	}
};


// MỚI: Middleware tùy chọn - Nếu có token thì lấy user, nếu không thì vẫn cho qua (cho Guest)
export const optionalAuth = async (req, res, next) => {
	try {
		const authHeader = req.headers.authorization;
		if (authHeader && authHeader.startsWith('Bearer ')) {
			const token = authHeader.split(' ')[1];
			const payload = jwt.verify(token, ACCESS_JWT_SECRET);
			
			const user = await prisma.user.findUnique({
				where: { id: payload.sub },
				select: { isBanned: true }
			});

			if (user && !user.isBanned) {
				req.user = { id: payload.sub, role: payload.role };
			}
		}
		next();
	} catch (error) {
		// Nếu token sai hoặc hết hạn cũng cho qua như Guest, hoặc có thể chọn xóa req.user
		next();
	}
};

