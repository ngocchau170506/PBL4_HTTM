import * as driverService from './drivers.service.js';
import { getDriverBiometricProfile } from '../biometrics/biometrics.service.js';
import { getSocketIO } from '../iot/iot.service.js';

const isSelfDriverOrManager = (req, targetId) => {
  if (req.user?.role?.toUpperCase() !== 'DRIVER') return true;
  const ownDriverId = req.user.driver?.userId || req.user.id;
  return Number(targetId) === Number(ownDriverId);
};

export const handleGetDrivers = async (req, res, next) => {
  try {
    const drivers = await driverService.getAllDrivers();
    if (req.user?.role?.toUpperCase() === 'DRIVER') {
      const safeCoDrivers = drivers.map((d) => ({
        id: d.id,
        user_id: d.user_id,
        ho_ten: d.ho_ten,
        cap_canh_bao: d.cap_canh_bao,
        trang_thai_lai_xe: d.trang_thai_lai_xe,
        user: d.user ? { id: d.user.id, ho_ten: d.user.ho_ten } : null,
      }));
      return res.json({ success: true, data: safeCoDrivers });
    }
    res.json({ success: true, data: drivers });
  } catch (error) {
    next(error);
  }
};

export const handleGetDriverById = async (req, res, next) => {
  try {
    if (!isSelfDriverOrManager(req, req.params.id)) {
      return res.status(403).json({ success: false, message: 'Không có quyền xem hồ sơ tài xế khác.' });
    }
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
    const io = getSocketIO();
    if (io) {
      io.emit('violation:updated', { type: 'DRIVER_STATUS_UPDATED', driver_id: updated.id });
      if (updated.scoreChange) {
        io.emit('driver:score_changed', updated.scoreChange);
      }
    }
    res.json({ success: true, data: updated });
  } catch (error) {
    next(error);
  }
};

export const handleGetDriverScoreLogs = async (req, res, next) => {
  try {
    if (!isSelfDriverOrManager(req, req.params.id)) {
      return res.status(403).json({ success: false, message: 'Không có quyền xem sổ cái điểm tài xế khác.' });
    }
    const logs = await driverService.getDriverScoreLogs(req.params.id);
    res.json({ success: true, data: logs });
  } catch (error) {
    next(error);
  }
};

export const handleGetDriverBiometrics = async (req, res, next) => {
  try {
    if (!isSelfDriverOrManager(req, req.params.id)) {
      return res.status(403).json({ success: false, message: 'Không có quyền xem hồ sơ sinh học tài xế khác.' });
    }
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
