import * as assetsService from './assets.service.js';

export const getVehicles = async (req, res) => {
  try {
    const vehicles = await assetsService.getAllVehicles();
    res.json({ vehicles });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};

export const createVehicle = async (req, res) => {
  try {
    const { bienSo, loaiXe } = req.body;
    if (!bienSo) return res.status(400).json({ message: 'Biển số xe là bắt buộc.' });
    const vehicle = await assetsService.createVehicle({ bienSo, loaiXe });
    res.status(201).json({ vehicle });
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};

export const updateVehicleStatus = async (req, res) => {
  try {
    const { id } = req.params;
    const { trangThai } = req.body;
    const vehicle = await assetsService.updateVehicleStatus(id, trangThai);
    res.json({ vehicle });
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};

export const getDevices = async (req, res) => {
  try {
    const devices = await assetsService.getAllIotDevices();
    res.json({ devices });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};

export const createDevice = async (req, res) => {
  try {
    const { macAddress, phienBan, vehicleId } = req.body;
    if (!macAddress) return res.status(400).json({ message: 'MAC Address là bắt buộc.' });
    const device = await assetsService.createIotDevice({ macAddress, phienBan, vehicleId });
    res.status(201).json({ device });
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};

export const getRoutes = async (req, res) => {
  try {
    const routes = await assetsService.getAllRoutes();
    res.json({ routes });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};

export const createRoute = async (req, res) => {
  try {
    const { tenTuyen, khoangCachKm } = req.body;
    if (!tenTuyen) return res.status(400).json({ message: 'Tên tuyến đường là bắt buộc.' });
    const route = await assetsService.createRoute({ tenTuyen, khoangCachKm });
    res.status(201).json({ route });
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};
