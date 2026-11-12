import express from 'express';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';
import {
	handleGetConfig,
	handleGetMe,
	handleGetUsers,
	handleLogin,
	handleRegister,
	handleDeviceToken,
	handleUpdateConfig,
	handleUpdateUser,
	handleUpdateOwnProfile,
	handleChangePassword,
	handleAdminResetPassword,
} from './auth.controller.js';

const router = express.Router();

router.post('/register', handleRegister);
router.post('/login', handleLogin);
router.post('/device-token', handleDeviceToken);

router.use(requireAuth);

router.get('/me', handleGetMe);
router.get('/users', requireRole('ADMIN'), handleGetUsers);
router.patch('/users/:userId', requireRole('ADMIN'), handleUpdateUser);
router.get('/config', requireRole('ADMIN', 'MANAGER'), handleGetConfig);
router.put('/config', requireRole('ADMIN'), handleUpdateConfig);
router.patch('/me', handleUpdateOwnProfile);
router.post('/change-password', handleChangePassword);
router.post(
	'/users/:userId/reset-password',
	requireRole('ADMIN'),
	handleAdminResetPassword,
);

export default router;
