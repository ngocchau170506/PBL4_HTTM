import * as shiftsService from './shifts.service.js';
import { getSocketIO } from '../iot/iot.service.js';

export const getShifts = async (req, res) => {
	try {
		const { driverId, driver_id, status, trang_thai } = req.query;
		let targetDriverId = driverId || driver_id;
		if (req.user.role === 'DRIVER') {
			targetDriverId = req.user.driver?.userId || req.user.id;
		}

		const shifts = await shiftsService.getAllShifts({
			driverId: targetDriverId,
			status: status || trang_thai,
		});
		res.json({ success: true, data: shifts, shifts });
	} catch (error) {
		res.status(500).json({ success: false, message: error.message });
	}
};

export const createShift = async (req, res) => {
	try {
		const shift = await shiftsService.createShift({
			...req.body,
			createdBy: req.user.id,
		});
		const io = getSocketIO();
		if (io) io.emit('violation:updated', { type: 'SHIFT_CREATED', shift });
		res.status(201).json({ success: true, data: shift });
	} catch (error) {
		res.status(400).json({ success: false, message: error.message });
	}
};

export const updateShift = async (req, res) => {
	try {
		const { id } = req.params;
		const shift = await shiftsService.updateShift(id, req.body, req.user);
		const io = getSocketIO();
		if (io) io.emit('violation:updated', { type: 'SHIFT_UPDATED', shift });
		res.json({ success: true, data: shift });
	} catch (error) {
		res.status(400).json({ success: false, message: error.message });
	}
};

export const deleteShift = async (req, res) => {
	try {
		const { id } = req.params;
		const reason = req.body?.ly_do || req.body?.reason || 'Hủy ca phân công';
		const result = await shiftsService.deleteShift(id, reason, req.user);
		const io = getSocketIO();
		if (io) io.emit('violation:updated', { type: 'SHIFT_DELETED', id });
		res.json({ success: true, data: result });
	} catch (error) {
		res.status(400).json({ success: false, message: error.message });
	}
};

export const respondToShift = async (req, res) => {
	try {
		const { id } = req.params;
		const shift = await shiftsService.respondToShift(id, req.body);
		const io = getSocketIO();
		if (io) io.emit('violation:updated', { type: 'SHIFT_RESPONDED', shift });
		res.json({ success: true, data: shift });
	} catch (error) {
		res.status(400).json({ success: false, message: error.message });
	}
};
