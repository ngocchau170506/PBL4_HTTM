import express from 'express';
import {
  getVehicles,
  createVehicle,
  updateVehicleStatus,
  getDevices,
  createDevice,
  getRoutes,
  createRoute,
  handleGetNearestRestStop,
} from './assets.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/vehicles', getVehicles);
router.post('/vehicles', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createVehicle);
router.patch('/vehicles/:id/status', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), updateVehicleStatus);

router.get('/devices', getDevices);
router.post('/devices', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createDevice);

// Routes
router.get('/routes', getRoutes);
router.post('/routes', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createRoute);

// Hỗ trợ cả khi mount tại /api/assets/routes/... lẫn /api/routes/...
router.get('/routes/nearest-rest-stop', handleGetNearestRestStop);
router.get('/routes/:id/nearest-rest-stop', handleGetNearestRestStop);
router.get('/nearest-rest-stop', handleGetNearestRestStop);
router.get('/:id/nearest-rest-stop', handleGetNearestRestStop);

export default router;
