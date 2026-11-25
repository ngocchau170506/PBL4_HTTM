import * as monitoringService from './monitoring.service.js';
import { asyncHandler } from '../../common/middleware/index.js';

export const handleGetPriorityFleet = asyncHandler(async (_req, res) => {
	const fleet = await monitoringService.getPriorityFleet();
	res.json({ success: true, data: fleet });
});

export const handleGetMonitoringStats = asyncHandler(async (_req, res) => {
	const stats = await monitoringService.getMonitoringStats();
	res.json({ success: true, data: stats });
});

export const handleGetNearbyRestStops = asyncHandler(async (req, res) => {
	const result = await monitoringService.getNearbyRestStopsForLocation(req.query);
	res.json({ success: true, data: result });
});

export const handleDispatchRestStop = asyncHandler(async (req, res) => {
	const result = await monitoringService.dispatchVehicleToRestStop(
		req.body,
		req.user,
	);
	res.json({ success: true, data: result, ...result });
});
