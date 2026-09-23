import * as violationsService from './violations.service.js';

export const getViolations = async (req, res) => {
  try {
    const { driverId, tripId } = req.query;
    let targetDriverId = driverId;
    if (req.user.role === 'DRIVER') {
      targetDriverId = req.user.driver?.userId || req.user.id;
    }

    const violations = await violationsService.getAllViolations({ driverId: targetDriverId, tripId });
    res.json({ violations });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};

export const createViolation = async (req, res) => {
  try {
    const { driverId, tripId, loaiViPham, diemTru, videoUrl, videoDuration, moTa } = req.body;
    if (!driverId || !tripId || !loaiViPham) {
      return res.status(400).json({ message: 'Vui lòng cung cấp driverId, tripId và loaiViPham.' });
    }

    const result = await violationsService.createViolationRecord({
      driverId, tripId, loaiViPham, diemTru, videoUrl, videoDuration, moTa,
    });
    res.status(201).json(result);
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
};

export const getDriversScore = async (req, res) => {
  try {
    const drivers = await violationsService.getDriverRankings();
    res.json({ drivers });
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
};
