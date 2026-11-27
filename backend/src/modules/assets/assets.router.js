import express from 'express';
import {
  getVehicles, createVehicle, updateVehicleStatus,
  getDevices, createDevice,
  getRoutes, createRoute
} from './assets.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

export const vehiclesRouter = express.Router();
vehiclesRouter.use(requireAuth);
vehiclesRouter.get('/', getVehicles);
vehiclesRouter.get('/vehicles', getVehicles);
vehiclesRouter.post('/', requireRole('MANAGER', 'ADMIN'), createVehicle);
vehiclesRouter.post('/vehicles', requireRole('MANAGER', 'ADMIN'), createVehicle);
vehiclesRouter.patch('/:id/status', requireRole('MANAGER', 'ADMIN'), updateVehicleStatus);
vehiclesRouter.patch('/vehicles/:id/status', requireRole('MANAGER', 'ADMIN'), updateVehicleStatus);

export const routesRouter = express.Router();
routesRouter.use(requireAuth);
routesRouter.get('/', getRoutes);
routesRouter.get('/routes', getRoutes);
routesRouter.post('/', requireRole('MANAGER', 'ADMIN'), createRoute);
routesRouter.post('/routes', requireRole('MANAGER', 'ADMIN'), createRoute);

export const devicesRouter = express.Router();
devicesRouter.use(requireAuth);
devicesRouter.get('/', getDevices);
devicesRouter.get('/devices', getDevices);
devicesRouter.post('/', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createDevice);
devicesRouter.post('/devices', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createDevice);

// Router tổng hợp /api/assets (tương thích ngược)
const assetsRouter = express.Router();
assetsRouter.use(requireAuth);
assetsRouter.use('/vehicles', vehiclesRouter);
assetsRouter.use('/devices', devicesRouter);
assetsRouter.use('/routes', routesRouter);

// Hỗ trợ cả truy cập trực tiếp /vehicles trên assetsRouter
assetsRouter.get('/vehicles', getVehicles);
assetsRouter.post('/vehicles', requireRole('MANAGER', 'ADMIN'), createVehicle);
assetsRouter.patch('/vehicles/:id/status', requireRole('MANAGER', 'ADMIN'), updateVehicleStatus);
assetsRouter.get('/devices', getDevices);
assetsRouter.post('/devices', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createDevice);
assetsRouter.get('/routes', getRoutes);
assetsRouter.post('/routes', requireRole('MANAGER', 'ADMIN'), createRoute);

export default assetsRouter;
