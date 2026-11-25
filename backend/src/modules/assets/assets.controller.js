import * as assetsService from './assets.service.js';
import { asyncHandler } from '../../common/middleware/index.js';
import { BadRequestException } from '../../common/exceptions/index.js';

// Vehicles
export const getVehicles = asyncHandler(async (_req, res) => {
	const vehicles = await assetsService.getAllVehicles();
	res.json({ success: true, data: vehicles, vehicles });
});

export const createVehicle = asyncHandler(async (req, res) => {
	const vehicle = await assetsService.createVehicle({
		...req.body,
		actorUser: req.user,
	});
	res.status(201).json({ success: true, data: vehicle });
});

export const updateVehicleStatus = asyncHandler(async (req, res) => {
	const { id } = req.params;
	const status = req.body.trangThai || req.body.trang_thai;
	if (!status) {
		throw new BadRequestException('Vui lòng cung cấp trạng thái mới.');
	}
	const vehicle = await assetsService.updateVehicleStatus(id, status, req.user);
	res.json({ success: true, data: vehicle });
});

// Devices
export const getDevices = asyncHandler(async (_req, res) => {
	const devices = await assetsService.getAllIotDevices();
	res.json({ success: true, data: devices, devices });
});

export const createDevice = asyncHandler(async (req, res) => {
	const device = await assetsService.createIotDevice(req.body);
	res.status(201).json({ success: true, data: device });
});

// Routes
export const getRoutes = asyncHandler(async (_req, res) => {
	const routes = await assetsService.getAllRoutes();
	res.json({ success: true, data: routes, routes });
});

export const createRoute = asyncHandler(async (req, res) => {
	const route = await assetsService.createRoute(req.body);
	res.status(201).json({ success: true, data: route });
});
