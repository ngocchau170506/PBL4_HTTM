import jwt from 'jsonwebtoken';
import prisma from './prisma.js';

export const requireAuth = async (req, res, next) => {
  try {
    const authHeader = req.headers.authorization;
    let token = null;

    if (authHeader && authHeader.startsWith('Bearer ')) {
      token = authHeader.split(' ')[1];
    } else if (req.cookies && req.cookies.accessToken) {
      token = req.cookies.accessToken;
    }

    if (!token) {
      return res.status(401).json({ message: 'Không tìm thấy token xác thực, vui lòng đăng nhập.' });
    }

    const secret = process.env.ACCESS_JWT_SECRET || 'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';
    const decoded = jwt.verify(token, secret);

    const user = await prisma.user.findUnique({
      where: { id: decoded.id },
      include: {
        driver: true,
        manager: true,
      },
    });

    if (!user || user.trangThai !== 'ACTIVE') {
      return res.status(401).json({ message: 'Tài khoản không tồn tại hoặc đã bị khóa.' });
    }

    req.user = user;
    next();
  } catch (error) {
    return res.status(401).json({ message: 'Token hết hạn hoặc không hợp lệ: ' + error.message });
  }
};

export const requireRole = (...roles) => {
  return (req, res, next) => {
    if (!req.user) {
      return res.status(401).json({ message: 'Yêu cầu đăng nhập.' });
    }

    if (!roles.includes(req.user.role)) {
      return res.status(403).json({ message: `Bạn không có quyền truy cập. Yêu cầu vai trò: ${roles.join(' hoặc ')}.` });
    }

    next();
  };
};
