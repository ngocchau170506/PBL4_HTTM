import express from 'express';
import { getViolations, createViolation, getDriversScore } from './violations.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/', getViolations);
router.get('/drivers-score', requireRole('MANAGER'), getDriversScore);
router.post('/', requireRole('MANAGER'), createViolation);

export default router;
