import * as authService from './auth.service.js';
import { asyncHandler } from '../../common/middleware/index.js';
import { UnauthorizedException } from '../../common/exceptions/index.js';

export const handleRegister = asyncHandler(async (req, res) => {
	const user = await authService.registerUser(req.body);
	res.status(201).json({ success: true, data: user });
});

export const handleLogin = asyncHandler(async (req, res) => {
	const { username, password } = req.body;
	const result = await authService.loginUser({ username, password });

	res.cookie('accessToken', result.token, {
		httpOnly: true,
		secure: process.env.NODE_ENV === 'production',
		sameSite: 'strict',
		maxAge: 7 * 24 * 60 * 60 * 1000,
	});

	res.json({ success: true, data: result, ...result });
});

export const handleDeviceToken = asyncHandler(async (req, res) => {
	const {
		ma_thiet_bi,
		maThietBi,
		device_secret,
		deviceSecret,
		cpu_serial,
		cpuSerial,
	} = req.body;
	const deviceCode = ma_thiet_bi || maThietBi;
	const secret = device_secret || deviceSecret;
	const serial = cpu_serial || cpuSerial;

	const expectedSecret =
		process.env.IOT_DEVICE_SECRET || 'your_iot_device_secret_token';
	if (!secret || secret !== expectedSecret) {
		throw new UnauthorizedException('device_secret không hợp lệ hoặc bị thiếu.');
	}

	const result = await authService.issueDeviceToken({ deviceCode, serial });
	res.json({ success: true, data: result, ...result });
});

export const handleGetMe = asyncHandler(async (req, res) => {
	const user = await authService.getUserProfile(req.user.id);
	res.json({ success: true, data: user, user });
});

export const handleGetUsers = asyncHandler(async (_req, res) => {
	const users = await authService.getAllUsers();
	res.json({ success: true, data: users, users });
});

export const handleUpdateUser = asyncHandler(async (req, res) => {
	const { userId } = req.params;
	const updated = await authService.updateUser(userId, req.body, req.user.id);
	res.json({ success: true, data: updated });
});

export const handleGetConfig = asyncHandler(async (_req, res) => {
	const config = await authService.getSystemConfig();
	res.json({ success: true, data: config, config });
});

export const handleUpdateConfig = asyncHandler(async (req, res) => {
	const updated = await authService.updateSystemConfig(req.body, req.user.id);
	res.json({ success: true, data: updated, config: updated });
});

export const handleChangePassword = asyncHandler(async (req, res) => {
	const result = await authService.changeOwnPassword(req.user.id, req.body);
	res.json({ success: true, data: result, ...result });
});

export const handleAdminResetPassword = asyncHandler(async (req, res) => {
	const { userId } = req.params;
	const { newPassword } = req.body;
	const result = await authService.resetUserPasswordByAdmin(
		userId,
		newPassword,
		req.user,
	);
	res.json({ success: true, data: result, ...result });
});

export const handleUpdateOwnProfile = asyncHandler(async (req, res) => {
	const updated = await authService.updateOwnProfile(req.user.id, req.body);
	res.json({ success: true, data: updated, user: updated });
});
