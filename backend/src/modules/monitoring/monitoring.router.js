import express from 'express';
import {
	handleDispatchRestStop,
	handleGetMonitoringStats,
	handleGetNearbyRestStops,
	handleGetPriorityFleet,
} from './monitoring.controller.js';
import { requireAuth } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/priority-fleet', handleGetPriorityFleet);
router.get('/stats', handleGetMonitoringStats);
router.get('/nearby-rest-stops', handleGetNearbyRestStops);
router.post('/dispatch-rest-stop', handleDispatchRestStop);

export default router;
