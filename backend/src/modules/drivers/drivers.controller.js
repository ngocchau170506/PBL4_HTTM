import * as driverService from './drivers.service.js';
import { getDriverBiometricProfile } from '../biometrics/biometrics.service.js';

export const handleGetDrivers = async (req, res, next) => {
  try {
    const drivers = await driverService.getAllDrivers();
    res.json({ success: true, data: drivers });
  } catch (error) {
    next(error);
  }
};

export const handleGetDriverById = async (req, res, next) => {
  try {
    const driver = await driverService.getDriverById(req.params.id);
    if (!driver) {
      return res.status(404).json({ success: false, message: 'Không tìm thấy tài xế.' });
    }
    res.json({ success: true, data: driver });
  } catch (error) {
    next(error);
  }
};

export const handleUpdateDriverStatus = async (req, res, next) => {
  try {
    const updated = await driverService.updateDriverStatus(req.params.id, req.body, req.user);
    res.json({ success: true, data: updated });
  } catch (error) {
    next(error);
  }
};

export const handleGetDriverScoreLogs = async (req, res, next) => {
  try {
    const logs = await driverService.getDriverScoreLogs(req.params.id);
    res.json({ success: true, data: logs });
  } catch (error) {
    next(error);
  }
};

export const handleGetDriverBiometrics = async (req, res, next) => {
  try {
    const profile = await getDriverBiometricProfile(req.params.id);
    res.json({ success: true, data: profile });
  } catch (error) {
    next(error);
  }
};

export const handleCreateDriver = async (req, res, next) => {
  try {
    const newDriver = await driverService.createDriverRecord(req.body);
    res.status(201).json({ success: true, data: newDriver });
  } catch (error) {
    next(error);
  }
};
