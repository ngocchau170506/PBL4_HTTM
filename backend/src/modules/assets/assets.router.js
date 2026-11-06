import express from 'express';
import {
	getVehicles,
	createVehicle,
	updateVehicleStatus,
	getDevices,
	createDevice,
	getRoutes,
	createRoute,
} from './assets.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/vehicles', getVehicles);
router.post('/vehicles', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createVehicle);
router.patch(
	'/vehicles/:id/status',
	requireRole('MANAGER', 'DISPATCHER', 'ADMIN'),
	updateVehicleStatus,
);

router.get('/devices', getDevices);
router.post('/devices', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createDevice);

router.get('/routes', getRoutes);
router.post('/routes', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), createRoute);

router.get('/', (req, res, next) => {
	if (req.baseUrl.endsWith('/routes')) {
		return getRoutes(req, res, next);
	}
	if (req.baseUrl.endsWith('/devices')) {
		return getDevices(req, res, next);
	}
	return getVehicles(req, res, next);
});

router.post('/', requireRole('MANAGER', 'DISPATCHER', 'ADMIN'), (req, res, next) => {
	if (req.baseUrl.endsWith('/routes')) {
		return createRoute(req, res, next);
	}
	if (req.baseUrl.endsWith('/devices')) {
		return createDevice(req, res, next);
	}
	return createVehicle(req, res, next);
});

router.patch(
	'/:id/status',
	requireRole('MANAGER', 'DISPATCHER', 'ADMIN'),
	updateVehicleStatus,
);

export default router;
