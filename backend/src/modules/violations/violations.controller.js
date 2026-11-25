import * as violationsService from './violations.service.js';
import { getSocketIO } from '../iot/iot.service.js';
import { asyncHandler } from '../../common/middleware/index.js';
import { BadRequestException } from '../../common/exceptions/index.js';

export const getViolations = asyncHandler(async (req, res) => {
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
});

export const getViolationTypes = asyncHandler(async (_req, res) => {
	const types = violationsService.getAllViolationTypes();
	res.json({ success: true, data: types, types });
});

export const createViolation = asyncHandler(async (req, res) => {
	const driverId = req.body.driverId || req.body.driver_id;
	const tripId = req.body.tripId || req.body.trip_id;
	const loaiViPham = req.body.loaiViPham || req.body.ma_loai || req.body.maLoai;
	const violationTypeId = req.body.violationTypeId || req.body.violation_type_id;

	if (!driverId || !tripId || (!loaiViPham && !violationTypeId)) {
		throw new BadRequestException(
			'Vui lòng cung cấp driverId, tripId và loaiViPham hoặc violationTypeId.',
		);
	}

	const result = await violationsService.createViolationRecord({
		driverId,
		tripId,
		loaiViPham,
		violationTypeId,
		diemTru: req.body.diemTru ?? req.body.diem_bi_tru,
		videoUrl: req.body.videoUrl || req.body.video_url,
		videoDuration: req.body.videoDuration || 10,
		aiConfidenceScore: req.body.aiConfidenceScore ?? req.body.ai_confidence_score,
		moTa: req.body.moTa || req.body.mo_ta,
		clientEventId: req.body.clientEventId || req.body.client_event_id,
	});

	const io = getSocketIO();
	if (io) {
		io.emit('violation:new', {
			...result.violation,
			violation_id: result.violation.id,
			bien_so_xe: result.violation.vehicle?.bien_so_xe || '43C-123.45',
			driver_name: result.violation.driver?.user?.ho_ten || 'Tài xế',
		});
		if (result.scoreChange) {
			io.emit('driver:score_changed', {
				driver_id: result.updatedDriver.userId,
				...result.scoreChange,
			});
		}
	}

	res.status(201).json({ success: true, data: result.violation, ...result });
});

export const getDriversScore = asyncHandler(async (_req, res) => {
	const drivers = await violationsService.getDriverRankings();
	res.json({ success: true, data: drivers, drivers });
});

export const reviewViolation = asyncHandler(async (req, res) => {
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
		if (result.scoreChange) {
			io.emit('driver:score_changed', {
				driver_id: result.updatedDriver.userId,
				...result.scoreChange,
			});
		}
	}

	res.json({ success: true, data: result.violation, ...result });
});
