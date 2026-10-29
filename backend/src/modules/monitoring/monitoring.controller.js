import * as monitoringService from './monitoring.service.js';

export const handleGetPriorityFleet = async (req, res, next) => {
  try {
    const fleet = await monitoringService.getPriorityFleet();
    res.json({ success: true, data: fleet });
  } catch (error) {
    next(error);
  }
};

export const handleGetMonitoringStats = async (req, res, next) => {
  try {
    const stats = await monitoringService.getMonitoringStats();
    res.json({ success: true, data: stats });
  } catch (error) {
    next(error);
  }
};

export const handleDispatchRestStop = async (req, res, next) => {
  try {
    const result = await monitoringService.dispatchVehicleToRestStop(req.body, req.user);
    res.json({ success: true, data: result });
  } catch (error) {
    next(error);
  }
};
