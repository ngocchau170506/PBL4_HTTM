import express from 'express';
import { getViolations, getViolationTypes, createViolation, getDriversScore, reviewViolation } from './violations.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/types', getViolationTypes);
router.get('/', getViolations);
router.get('/drivers-score', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), getDriversScore);
router.post('/', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createViolation);

// Hỗ trợ cả POST và PATCH cho duyệt vi phạm
router.post('/:id/review', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), reviewViolation);
router.patch('/:id/review', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), reviewViolation);

export default router;
