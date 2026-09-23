import bcrypt from 'bcrypt';
import jwt from 'jsonwebtoken';
import prisma from '../../common/prisma.js';

const JWT_SECRET = process.env.ACCESS_JWT_SECRET || 'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';

export const registerUser = async ({ username, password, hoTen, sdt, role, bangLai, vaiTro }) => {
  const existingUser = await prisma.user.findUnique({ where: { username } });
  if (existingUser) {
    throw new Error('Tên đăng nhập đã tồn tại trong hệ thống.');
  }

  const saltRounds = 10;
  const passwordHash = await bcrypt.hash(password, saltRounds);

  const newUser = await prisma.user.create({
    data: {
      username,
      passwordHash,
      hoTen,
      sdt,
      role: role || 'DRIVER',
      trangThai: 'ACTIVE',
      ...(role === 'MANAGER'
        ? { manager: { create: { vaiTro: vaiTro || 'COORDINATOR' } } }
        : { driver: { create: { bangLai: bangLai || 'B2', uyTin: 100, trangThaiTaiXe: 'AVAILABLE' } } }),
    },
    include: {
      driver: true,
      manager: true,
    },
  });

  return newUser;
};

export const loginUser = async ({ username, password }) => {
  const user = await prisma.user.findUnique({
    where: { username },
    include: {
      driver: true,
      manager: true,
    },
  });

  if (!user) {
    throw new Error('Tên đăng nhập hoặc mật khẩu không chính xác.');
  }

  if (user.trangThai !== 'ACTIVE') {
    throw new Error('Tài khoản của bạn đã bị khóa.');
  }

  const isValidPassword = await bcrypt.compare(password, user.passwordHash);
  if (!isValidPassword) {
    throw new Error('Tên đăng nhập hoặc mật khẩu không chính xác.');
  }

  const payload = {
    id: user.id,
    username: user.username,
    role: user.role,
    hoTen: user.hoTen,
    driverId: user.driver?.userId || null,
    managerId: user.manager?.userId || null,
  };

  const token = jwt.sign(payload, JWT_SECRET, { expiresIn: '1d' });

  return {
    user: {
      id: user.id,
      username: user.username,
      hoTen: user.hoTen,
      sdt: user.sdt,
      role: user.role,
      driver: user.driver,
      manager: user.manager,
    },
    token,
  };
};

export const getUserProfile = async (userId) => {
  const user = await prisma.user.findUnique({
    where: { id: userId },
    include: {
      driver: {
        include: {
          violations: {
            take: 5,
            orderBy: { thoiGianViPham: 'desc' },
          },
        },
      },
      manager: true,
    },
  });

  if (!user) {
    throw new Error('Không tìm thấy người dùng.');
  }

  return user;
};
