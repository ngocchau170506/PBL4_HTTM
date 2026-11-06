import express from 'express';
import {
	getShifts,
	createShift,
	updateShift,
	deleteShift,
	respondToShift,
} from './shifts.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/', getShifts);
router.post('/', requireRole('ADMIN', 'MANAGER', 'DISPATCHER'), createShift);
router.patch('/:id', requireRole('ADMIN', 'MANAGER', 'DISPATCHER'), updateShift);
router.delete('/:id', requireRole('ADMIN', 'MANAGER', 'DISPATCHER'), deleteShift);
router.patch('/:id/respond', respondToShift);

export default router;
