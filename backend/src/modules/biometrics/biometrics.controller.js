import * as biometricsService from './biometrics.service.js';
import { asyncHandler } from '../../common/middleware/index.js';

const resolveDriverTarget = (req, requestedId) =>
	req.user.role === 'DRIVER'
		? req.user.driver?.userId || req.user.id
		: requestedId;

export const getBiometricProfile = asyncHandler(async (req, res) => {
	const targetDriverId = resolveDriverTarget(req, req.params.driverId);
	const profile = await biometricsService.getDriverBiometricProfile(targetDriverId);
	res.json({ profile });
});

export const getCalibrations = asyncHandler(async (req, res) => {
	const targetDriverId = resolveDriverTarget(req, req.params.driverId);
	const calibrations = await biometricsService.getDriverCalibrations(targetDriverId);
	res.json({ calibrations });
});

export const saveCalibration = asyncHandler(async (req, res) => {
	const { driverId, tripId, shiftId, vectorDacTrung, ketQua } = req.body;
	const targetDriverId = resolveDriverTarget(req, driverId);

	const calibration = await biometricsService.saveCalibrationData({
		driverId: targetDriverId,
		tripId,
		shiftId,
		vectorDacTrung,
		ketQua,
	});
	res.status(201).json({ calibration });
});
