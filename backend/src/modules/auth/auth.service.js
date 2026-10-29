import bcrypt from 'bcrypt';
import jwt from 'jsonwebtoken';
import prisma from '../../common/prisma.js';
import { getAiConfig, updateAiConfig } from '../configs/configs.service.js';

const JWT_SECRET = process.env.ACCESS_JWT_SECRET || 'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';

export const formatUser = (user) => ({
  id: user.id,
  username: user.username,
  hoTen: user.hoTen,
  ho_ten: user.hoTen,
  name: user.hoTen,
  sdt: user.sdt,
  so_dien_thoai: user.sdt,
  role: user.role,
  vai_tro: user.role,
  trangThai: user.trangThai,
  trang_thai: user.trangThai,
  driver: user.driver,
});

export const registerUser = async ({ username, password, hoTen, sdt, bangLai }) => {
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
      role: 'DRIVER',
      trangThai: 'ACTIVE',
      driver: { create: { bangLai: bangLai || `BL-${Date.now()}`, uyTin: 100, trangThaiTaiXe: 'AVAILABLE' } },
    },
    include: {
      driver: true,
    },
  });

  return formatUser(newUser);
};

export const loginUser = async ({ username, password }) => {
  const user = await prisma.user.findUnique({
    where: { username },
    include: {
      driver: true,
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
  };

  const token = jwt.sign(payload, JWT_SECRET, { expiresIn: '7d' });

  const formatted = formatUser(user);
  return {
    user: formatted,
    token,
    accessToken: token,
    access_token: token,
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
    },
  });

  if (!user) {
    throw new Error('Không tìm thấy người dùng.');
  }

  return formatUser(user);
};

export const getAllUsers = async () => {
  const users = await prisma.user.findMany({
    include: {
      driver: true,
    },
    orderBy: { id: 'asc' },
  });

  return users.map(formatUser);
};

export const updateUser = async (userId, data, adminId) => {
  const pUserId = parseInt(userId, 10);
  const oldUser = await prisma.user.findUnique({ where: { id: pUserId } });
  if (!oldUser) {
    throw new Error('Không tìm thấy người dùng để cập nhật.');
  }

  const updatedUser = await prisma.user.update({
    where: { id: pUserId },
    data,
    include: { driver: true },
  });

  if (adminId) {
    const { logAuditAction } = await import('../../common/audit.service.js');
    logAuditAction({
      userId: adminId,
      hanhDong: 'UPDATE_USER_ACCOUNT',
      doiTuongLoai: 'USER',
      doiTuongId: String(pUserId),
      giaTriTruoc: { trangThai: oldUser.trangThai, role: oldUser.role },
      giaTriSau: { trangThai: updatedUser.trangThai, role: updatedUser.role },
      lyDo: `Quản trị viên cập nhật tài khoản: ${oldUser.username}`,
    });
  }

  return formatUser(updatedUser);
};

export const getSystemConfig = async () => {
  return getAiConfig();
};

export const updateSystemConfig = async (newConfig, adminId) => {
  return updateAiConfig(newConfig, { id: adminId });
};
