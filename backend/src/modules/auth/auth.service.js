import bcrypt from 'bcrypt';
import jwt from 'jsonwebtoken';
import prisma from '../../common/prisma.js';
import { getAiConfig, updateAiConfig } from '../configs/configs.service.js';

const JWT_SECRET =
	process.env.ACCESS_JWT_SECRET ||
	'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';

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
			driver: {
				create: {
					bangLai: bangLai || `BL-${Date.now()}`,
					uyTin: 100,
					trangThaiTaiXe: 'AVAILABLE',
				},
			},
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

// Cấp device_token cho Raspberry Pi 4 / ESP32 (SRS 4.1 § 2.2)
export const issueDeviceToken = async ({ deviceCode, serial }) => {
	let device = null;
	if (deviceCode || serial) {
		device = await prisma.iotDevice.findFirst({
			where: {
				OR: [
					deviceCode ? { maThietBi: deviceCode } : undefined,
					serial ? { cpuSerial: serial } : undefined,
				].filter(Boolean),
			},
			include: { vehicle: true },
		});
	}

	if (!device) {
		device = await prisma.iotDevice.findFirst({ include: { vehicle: true } });
	}

	const payload = {
		device_id: device?.id || 1,
		ma_thiet_bi: device?.maThietBi || 'IOT-DN-01',
		vehicle_id: device?.vehicleId || 1,
		role: 'DEVICE',
	};

	const token = jwt.sign(payload, JWT_SECRET, { expiresIn: '30d' });

	return {
		device_token: token,
		token,
		expires_in: 30 * 24 * 3600,
		device: {
			id: device?.id,
			ma_thiet_bi: device?.maThietBi,
			vehicle_id: device?.vehicleId,
			cpu_serial: device?.cpuSerial,
			bien_so_xe: device?.vehicle?.bienSo,
		},
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

export const changeOwnPassword = async (
	userId,
	{ currentPassword, oldPassword, newPassword },
) => {
	const oldPass = currentPassword || oldPassword;
	if (!oldPass || !newPassword) {
		throw new Error('Vui lòng nhập đầy đủ mật khẩu hiện tại và mật khẩu mới.');
	}
	if (newPassword.length < 6) {
		throw new Error('Mật khẩu mới phải có tối thiểu 6 ký tự.');
	}

	const user = await prisma.user.findUnique({ where: { id: parseInt(userId, 10) } });
	if (!user) throw new Error('Không tìm thấy tài khoản.');

	const isMatch = await bcrypt.compare(oldPass, user.passwordHash);
	if (!isMatch) {
		throw new Error('Mật khẩu hiện tại không chính xác.');
	}

	const passwordHash = await bcrypt.hash(newPassword, 10);
	await prisma.user.update({
		where: { id: user.id },
		data: { passwordHash },
	});

	const { logAuditAction } = await import('../../common/audit.service.js');
	await logAuditAction({
		userId: user.id,
		userName: user.hoTen,
		hanhDong: 'CHANGE_OWN_PASSWORD',
		doiTuongLoai: 'USER',
		doiTuongId: String(user.id),
		lyDo: 'Tài xế tự cập nhật mật khẩu bảo mật trên Mobile App',
	});

	return { success: true, message: 'Đổi mật khẩu thành công.' };
};

export const resetUserPasswordByAdmin = async (
	targetUserId,
	newPassword = 'password123',
	adminUser,
) => {
	const pTargetId = parseInt(targetUserId, 10);
	const passwordHash = await bcrypt.hash(newPassword, 10);

	const updated = await prisma.user.update({
		where: { id: pTargetId },
		data: { passwordHash },
	});

	const { logAuditAction } = await import('../../common/audit.service.js');
	await logAuditAction({
		userId: adminUser.id,
		userName: adminUser.hoTen,
		hanhDong: 'ADMIN_RESET_PASSWORD',
		doiTuongLoai: 'USER',
		doiTuongId: String(pTargetId),
		lyDo: `Quản trị viên cấp lại mật khẩu mặc định cho tài khoản ${updated.username}`,
	});

	return { success: true, message: `Đã đặt lại mật khẩu cho ${updated.hoTen}.` };
};

export const updateOwnProfile = async (userId, { ho_ten, hoTen, so_dien_thoai, sdt }) => {
	const pId = parseInt(userId, 10);
	const data = {};
	if (ho_ten || hoTen) data.hoTen = ho_ten || hoTen;
	if (so_dien_thoai !== undefined || sdt !== undefined) data.sdt = so_dien_thoai ?? sdt;

	const updated = await prisma.user.update({
		where: { id: pId },
		data,
		include: { driver: true },
	});

	return formatUser(updated);
};
