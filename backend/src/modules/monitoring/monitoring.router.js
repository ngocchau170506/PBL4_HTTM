import express from 'express';
import { handleGetPriorityFleet, handleGetMonitoringStats, handleDispatchRestStop } from './monitoring.controller.js';
import { requireAuth } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.get('/priority-fleet', handleGetPriorityFleet);
router.get('/stats', handleGetMonitoringStats);
router.post('/dispatch-rest-stop', handleDispatchRestStop);

export default router;
