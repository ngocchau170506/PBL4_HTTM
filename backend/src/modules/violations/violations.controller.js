import * as violationsService from './violations.service.js';
import { getSocketIO } from '../iot/iot.service.js';

export const getViolations = async (req, res) => {
	try {
		const { driverId, driver_id, tripId, trip_id, status, trang_thai_xac_thuc } =
			req.query;
		let targetDriverId = driverId || driver_id;
		if (req.user.role === 'DRIVER') {
			targetDriverId = req.user.driver?.userId || req.user.id;
		}

		const violations = await violationsService.getAllViolations({
			driverId: targetDriverId,
			tripId: tripId || trip_id,
			status: status || trang_thai_xac_thuc,
		});
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

		// Bắn Socket.io để Frontend & Mobile tự động đồng bộ lại danh sách và điểm
		const io = getSocketIO();
		if (io) {
			io.emit('violation:updated', result.violation);
			io.emit('driver:score_changed', {
				driver_id: result.updatedDriver.userId,
				diem_sau: result.updatedDriver.uyTin,
				cap_canh_bao: result.updatedDriver.capCanhBao,
			});
		}

		res.json({ success: true, data: result.violation, ...result });
	} catch (error) {
		res.status(400).json({ success: false, message: error.message });
	}
};

export const handleResolveViolationSafe = async (req, res) => {
  try {
    const { id } = req.params;
    const result = await violationsService.resolveViolationSafe(id, req.body, req.user);
    res.json({ success: true, data: result.violation, ...result });
  } catch (error) {
    res.status(400).json({ success: false, message: error.message });
  }
};
