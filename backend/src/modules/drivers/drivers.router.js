import express from 'express';
import {
  handleGetDrivers,
  handleGetDriverById,
  handleUpdateDriverStatus,
  handleGetDriverScoreLogs,
  handleGetDriverBiometrics,
  handleCreateDriver,
} from './drivers.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/', handleGetDrivers);
router.post('/', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), handleCreateDriver);
router.get('/:id', handleGetDriverById);
router.patch('/:id/status', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), handleUpdateDriverStatus);
router.get('/:id/score-logs', handleGetDriverScoreLogs);
router.get('/:id/biometrics', handleGetDriverBiometrics);
router.get('/:id/biometric-profile', handleGetDriverBiometrics);

export default router;
