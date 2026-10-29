import * as violationsService from './violations.service.js';

export const getViolations = async (req, res) => {
  try {
    const { driverId, tripId, status } = req.query;
    let targetDriverId = driverId;
    if (req.user.role === 'DRIVER') {
      targetDriverId = req.user.driver?.userId || req.user.id;
    }

    const violations = await violationsService.getAllViolations({ driverId: targetDriverId, tripId, status });
    res.json({ success: true, data: violations, violations });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const getViolationTypes = (req, res) => {
  try {
    const types = violationsService.getAllViolationTypes();
    res.json({ success: true, data: types, types });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const createViolation = async (req, res) => {
  try {
    const { driverId, tripId, loaiViPham, violationTypeId, diemTru, videoUrl, videoDuration, moTa, clientEventId } = req.body;
    if (!driverId || !tripId || (!loaiViPham && !violationTypeId)) {
      return res.status(400).json({ success: false, message: 'Vui lòng cung cấp driverId, tripId và loaiViPham hoặc violationTypeId.' });
    }

    const result = await violationsService.createViolationRecord({
      driverId,
      tripId,
      loaiViPham,
      violationTypeId,
      diemTru,
      videoUrl,
      videoDuration,
      moTa,
      clientEventId,
    });
    res.status(201).json({ success: true, data: result.violation, ...result });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};

export const getDriversScore = async (req, res) => {
  try {
    const drivers = await violationsService.getDriverRankings();
    res.json({ success: true, data: drivers, drivers });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};

export const reviewViolation = async (req, res) => {
  try {
    const { id } = req.params;
    const reviewerId = req.user.id;

    const result = await violationsService.reviewViolationManual(id, {
      ...req.body,
      reviewerId,
    });
    res.json({ success: true, data: result.violation, ...result });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};
