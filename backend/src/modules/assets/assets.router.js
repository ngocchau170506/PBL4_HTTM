import express from 'express';
import {
  getVehicles, createVehicle, updateVehicleStatus,
  getDevices, createDevice,
  getRoutes, createRoute
} from './assets.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/vehicles', getVehicles);
router.post('/vehicles', requireRole('MANAGER', 'ADMIN'), createVehicle);
router.patch('/vehicles/:id/status', requireRole('MANAGER', 'ADMIN'), updateVehicleStatus);

router.get('/devices', getDevices);
router.post('/devices', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createDevice);

// Routes
router.get('/routes', getRoutes);
router.post('/routes', requireRole('MANAGER', 'ADMIN'), createRoute);

export default router;
