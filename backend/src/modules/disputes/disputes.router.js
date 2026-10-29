import express from 'express';
import { handleGetDisputes, handleCreateDispute, handleResolveDispute } from './disputes.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/', handleGetDisputes);
router.post('/', handleCreateDispute);
router.patch('/:id/resolve', requireRole('ADMIN', 'MANAGER', 'DISPATCHER'), handleResolveDispute);

export default router;
