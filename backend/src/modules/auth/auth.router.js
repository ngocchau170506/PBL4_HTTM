import express from 'express';
import { 
  handleRegister, 
  handleLogin, 
  handleGetMe,
  handleGetUsers,
  handleUpdateUser,
  handleResetPasswordByAdmin,
  handleGetConfig,
  handleUpdateConfig 
} from './auth.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.post('/register', handleRegister);
router.post('/login', handleLogin);

router.use(requireAuth);

router.get('/me', handleGetMe);
router.get('/users', requireRole('ADMIN'), handleGetUsers);
router.patch('/users/:userId', requireRole('ADMIN'), handleUpdateUser);

// Nghiệp vụ Admin cấp lại mật khẩu cho người dùng
router.post('/users/:userId/reset-password', requireRole('ADMIN'), handleResetPasswordByAdmin);
router.patch('/users/:userId/reset-password', requireRole('ADMIN'), handleResetPasswordByAdmin);

router.get('/config', requireRole('ADMIN', 'MANAGER', 'DISPATCHER'), handleGetConfig);
router.put('/config', requireRole('ADMIN'), handleUpdateConfig);

export default router;
