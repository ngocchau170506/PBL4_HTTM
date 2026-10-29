import express from 'express';
import { handleGetAiConfig, handleUpdateAiConfig } from './configs.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/', handleGetAiConfig);
router.put('/', requireRole('ADMIN'), handleUpdateAiConfig);

export default router;
