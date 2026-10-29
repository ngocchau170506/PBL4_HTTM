import * as assetsService from './assets.service.js';

// Vehicles
export const getVehicles = async (req, res) => {
  try {
    const vehicles = await assetsService.getAllVehicles();
    res.json({ success: true, data: vehicles, vehicles });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const createVehicle = async (req, res) => {
  try {
    const vehicle = await assetsService.createVehicle({
      ...req.body,
      actorUser: req.user,
    });
    res.status(201).json({ success: true, data: vehicle });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};

export const updateVehicleStatus = async (req, res) => {
  try {
    const { id } = req.params;
    const status = req.body.trangThai || req.body.trang_thai;
    if (!status) {
      return res.status(400).json({ success: false, message: 'Vui lòng cung cấp trạng thái mới.' });
    }
    const vehicle = await assetsService.updateVehicleStatus(id, status, req.user);
    res.json({ success: true, data: vehicle });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};

// Devices
export const getDevices = async (req, res) => {
  try {
    const devices = await assetsService.getAllIotDevices();
    res.json({ success: true, data: devices, devices });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const createDevice = async (req, res) => {
  try {
    const device = await assetsService.createIotDevice(req.body);
    res.status(201).json({ success: true, data: device });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};

// Routes
export const getRoutes = async (req, res) => {
  try {
    const routes = await assetsService.getAllRoutes();
    res.json({ success: true, data: routes, routes });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const createRoute = async (req, res) => {
  try {
    const route = await assetsService.createRoute(req.body);
    res.status(201).json({ success: true, data: route });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};
