import * as driverService from './drivers.service.js';
import { getDriverBiometricProfile } from '../biometrics/biometrics.service.js';
import { getSocketIO } from '../iot/iot.service.js';
import { asyncHandler } from '../../common/middleware/index.js';
import {
	ForbiddenException,
	NotFoundException,
} from '../../common/exceptions/index.js';

const assertSelfDriverOrManager = (req, targetId, errorMessage) => {
	if (req.user?.role?.toUpperCase() !== 'DRIVER') return;
	const ownDriverId = req.user.driver?.userId || req.user.id;
	if (Number(targetId) !== Number(ownDriverId)) {
		throw new ForbiddenException(errorMessage);
	}
};

export const handleGetDrivers = asyncHandler(async (req, res) => {
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
});

export const handleGetDriverById = asyncHandler(async (req, res) => {
	assertSelfDriverOrManager(
		req,
		req.params.id,
		'Không có quyền xem hồ sơ tài xế khác.',
	);
	const driver = await driverService.getDriverById(req.params.id);
	if (!driver) {
		throw new NotFoundException('Không tìm thấy tài xế.');
	}
	res.json({ success: true, data: driver });
});

export const handleUpdateDriverStatus = asyncHandler(async (req, res) => {
	const updated = await driverService.updateDriverStatus(
		req.params.id,
		req.body,
		req.user,
	);
	const io = getSocketIO();
	if (io) {
		io.emit('violation:updated', {
			type: 'DRIVER_STATUS_UPDATED',
			driver_id: updated.id,
		});
		if (updated.scoreChange) {
			io.emit('driver:score_changed', updated.scoreChange);
		}
	}
	res.json({ success: true, data: updated });
});

export const handleGetDriverScoreLogs = asyncHandler(async (req, res) => {
	assertSelfDriverOrManager(
		req,
		req.params.id,
		'Không có quyền xem sổ cái điểm tài xế khác.',
	);
	const logs = await driverService.getDriverScoreLogs(req.params.id);
	res.json({ success: true, data: logs });
});

export const handleGetDriverBiometrics = asyncHandler(async (req, res) => {
	assertSelfDriverOrManager(
		req,
		req.params.id,
		'Không có quyền xem hồ sơ sinh học tài xế khác.',
	);
	const profile = await getDriverBiometricProfile(req.params.id);
	res.json({ success: true, data: profile });
});

export const handleCreateDriver = asyncHandler(async (req, res) => {
	const newDriver = await driverService.createDriverRecord(req.body);
	res.status(201).json({ success: true, data: newDriver });
});
