import express from 'express';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';
import {
	createTrip,
	getMyActiveTrip,
	getPlayback,
	getTripById,
	getTrips,
	handleCalibrateAndStart,
	handleSwitchDriver,
	updateTripStatus,
} from './trips.controller.js';

const router = express.Router();

router.use(requireAuth);

router.get('/', getTrips);
router.get('/my-active', getMyActiveTrip);
router.get('/:id', getTripById);
router.get('/:id/playback', getPlayback);

router.post('/', requireRole('MANAGER', 'DISPATCHER', 'ADMIN', 'DRIVER'), createTrip);
router.post('/start', handleCalibrateAndStart);
router.post('/:id/calibrate', handleCalibrateAndStart);
router.post('/:id/switch-driver', handleSwitchDriver);
router.patch('/:id/status', updateTripStatus);
router.post('/:id/complete', (req, res, next) => {
	req.body.trangThai = 'COMPLETED';
	return updateTripStatus(req, res, next);
});

export default router;
