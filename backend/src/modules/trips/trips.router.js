import express from 'express';
import { getTrips, getMyActiveTrip, getTripById, createTrip, updateTripStatus, getPlayback } from './trips.controller.js';
import { requireAuth, requireRole } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/', getTrips);
router.get('/my-active', getMyActiveTrip);
router.get('/:id', getTripById);
router.get('/:id/playback', getPlayback);

router.post('/', requireRole('MANAGER'), createTrip);
router.patch('/:id/status', updateTripStatus);

export default router;
