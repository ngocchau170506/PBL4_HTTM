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
router.post('/vehicles', requireRole('MANAGER'), createVehicle);
router.patch('/vehicles/:id/status', requireRole('MANAGER'), updateVehicleStatus);

router.get('/devices', getDevices);
router.post('/devices', requireRole('MANAGER'), createDevice);

router.get('/routes', getRoutes);
router.post('/routes', requireRole('MANAGER'), createRoute);

export default router;
