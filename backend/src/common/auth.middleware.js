import jwt from 'jsonwebtoken';
import prisma from './prisma.js';
import { ForbiddenException, UnauthorizedException } from './exceptions/index.js';
import { sanitizeUser } from './utils/sanitize.js';

export const requireAuth = async (req, _res, next) => {
	try {
		const authHeader = req.headers.authorization;
		let token = null;

		if (authHeader && authHeader.startsWith('Bearer ')) {
			token = authHeader.split(' ')[1];
		} else if (req.cookies && req.cookies.accessToken) {
			token = req.cookies.accessToken;
		}

		if (!token) {
			return next(
				new UnauthorizedException('Không tìm thấy token xác thực, vui lòng đăng nhập.'),
			);
		}

		const secret =
			process.env.ACCESS_JWT_SECRET ||
			'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';
		const decoded = jwt.verify(token, secret);

		const user = await prisma.user.findUnique({
			where: { id: decoded.id },
			include: {
				driver: true,
			},
		});

		if (!user || user.trangThai !== 'ACTIVE') {
			return next(
				new UnauthorizedException('Tài khoản không tồn tại hoặc đã bị khóa.'),
			);
		}

		req.user = sanitizeUser(user);
		next();
	} catch (error) {
		next(
			new UnauthorizedException('Token hết hạn hoặc không hợp lệ: ' + error.message),
		);
	}
};

export const requireRole = (...roles) => {
	return (req, _res, next) => {
		if (!req.user) {
			return next(new UnauthorizedException('Yêu cầu đăng nhập.'));
		}

		const userRole = (req.user.role || '').toUpperCase();
		const effectiveRoles = roles.map((r) => r.toUpperCase());

		// Chấp nhận tương đương giữa DISPATCHER và MANAGER
		if (effectiveRoles.includes('MANAGER') || effectiveRoles.includes('DISPATCHER')) {
			effectiveRoles.push('MANAGER', 'DISPATCHER');
		}

		if (userRole !== 'ADMIN' && !effectiveRoles.includes(userRole)) {
			return next(
				new ForbiddenException(
					`Bạn không có quyền truy cập. Yêu cầu vai trò: ${roles.join(' hoặc ')}.`,
				),
			);
		}

		next();
	};
};
