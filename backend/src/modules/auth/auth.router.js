import express from 'express';
import { handleRegister, handleLogin, handleGetMe } from './auth.controller.js';
import { requireAuth } from '../../common/auth.middleware.js';

const router = express.Router();

router.post('/register', handleRegister);
router.post('/login', handleLogin);
router.get('/me', requireAuth, handleGetMe);

export default router;
