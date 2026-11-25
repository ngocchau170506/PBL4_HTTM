import * as tripsService from './trips.service.js';
import { getSocketIO } from '../iot/iot.service.js';
import { asyncHandler } from '../../common/middleware/index.js';
import {
	BadRequestException,
	ForbiddenException,
	NotFoundException,
} from '../../common/exceptions/index.js';

const assertDriverOwnsTrip = async (req, tripId, forbiddenMessage) => {
	if (req.user?.role?.toUpperCase() !== 'DRIVER') return null;
	const trip = await tripsService.getTripById(tripId);
	if (!trip) return null;
	const ownDriverId = req.user.driver?.userId || req.user.id;
	if (Number(trip.driverId) !== Number(ownDriverId)) {
		throw new ForbiddenException(forbiddenMessage);
	}
	return trip;
};

export const getTrips = asyncHandler(async (req, res) => {
	const { driverId, status } = req.query;
	let targetDriverId = driverId;
	if (req.user.role === 'DRIVER') {
		targetDriverId = req.user.driver?.userId || req.user.id;
	}

	const trips = await tripsService.getAllTrips({
		driverId: targetDriverId,
		status,
	});
	res.json({ success: true, data: trips, trips });
});

export const getMyActiveTrip = asyncHandler(async (req, res) => {
	const driverId = req.user.driver?.userId || req.user.id;
	const activeTrip = await tripsService.getDriverActiveTrip(driverId);
	res.json({ success: true, data: activeTrip, trip: activeTrip });
});

export const getTripById = asyncHandler(async (req, res) => {
	const ownedTrip = await assertDriverOwnsTrip(
		req,
		req.params.id,
		'Không có quyền xem chuyến xe của tài xế khác.',
	);
	const target = ownedTrip || (await tripsService.getTripById(req.params.id));
	if (!target) {
		throw new NotFoundException('Chuyến xe không tồn tại.');
	}
	res.json({ success: true, data: target, trip: target });
});

export const createTrip = asyncHandler(async (req, res) => {
	const trip = await tripsService.createTrip(req.body);
	const io = getSocketIO();
	if (io) io.emit('violation:updated', { type: 'TRIP_CREATED', trip });
	res.status(201).json({ success: true, data: trip, trip });
});

export const updateTripStatus = asyncHandler(async (req, res) => {
	const { id } = req.params;
	await assertDriverOwnsTrip(
		req,
		id,
		'Không có quyền thao tác chuyến xe của tài xế khác.',
	);
	const { trangThai, trang_thai } = req.body;
	const status = trangThai || trang_thai;
	if (!status) {
		throw new BadRequestException(
			'Vui lòng cung cấp trạng thái mới cho chuyến xe.',
		);
	}
	const trip = await tripsService.updateTripStatus(id, status);

	// Bắn Socket.io để Web Dashboard đồng bộ lại danh sách Xe, Tài xế và Bản đồ ngay lập tức
	const io = getSocketIO();
	if (io) {
		io.emit('violation:updated', { type: 'TRIP_STATUS_CHANGED', trip });
		if (trip.rewardScoreChange) {
			io.emit('driver:score_changed', trip.rewardScoreChange);
		}
	}

	res.json({ success: true, data: trip, trip });
});

export const getPlayback = asyncHandler(async (req, res) => {
	await assertDriverOwnsTrip(
		req,
		req.params.id,
		'Không có quyền xem lịch sử chuyến xe của tài xế khác.',
	);
	const playbackData = await tripsService.getTripPlaybackData(req.params.id);
	res.json({ success: true, data: playbackData });
});

export const handleCalibrateAndStart = asyncHandler(async (req, res) => {
	const isDriver = req.user?.role?.toUpperCase() === 'DRIVER';
	const driverId = isDriver
		? req.user.driver?.userId || req.user.id
		: req.body.driverId ||
			req.body.driver_id ||
			req.user.driver?.userId ||
			req.user.id;
	const trip = await tripsService.calibrateAndStartTrip({
		driverId,
		shiftId: req.body.shiftId || req.body.shift_id,
		tripId: req.params.id || req.body.tripId || req.body.trip_id,
		isOfflineMode: Boolean(req.body.isOfflineMode),
	});

	// Bắn Socket.io để Web Dashboard thấy ngay xe đã chuyển sang "Đang vận hành"
	const io = getSocketIO();
	if (io) {
		io.emit('violation:updated', { type: 'TRIP_STARTED', trip });
	}

	res.json({ success: true, data: trip, trip });
});

export const handleSwitchDriver = asyncHandler(async (req, res) => {
	await assertDriverOwnsTrip(
		req,
		req.params.id,
		'Không có quyền đổi tài trên chuyến xe của tài xế khác.',
	);
	const trip = await tripsService.switchActiveDriver(req.params.id, req.body);
	const io = getSocketIO();
	if (io) {
		io.emit('violation:updated', { type: 'DRIVER_SWITCHED', trip });
	}
	res.json({ success: true, data: trip, trip });
});
