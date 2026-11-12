import prisma from '../../common/prisma.js';
import { formatDriver } from '../drivers/drivers.service.js';
import { formatVehicle, formatRoute } from '../assets/assets.service.js';

export const formatTrip = (t) => {
	const latestLog = t.logs?.[0] || null;
	const isDanger = t.violations?.some((v) =>
		['CONFIRMED', 'PENDING_MANUAL_REVIEW'].includes(v.trangThai),
	);

	return {
		...t,
		shift_id: t.shiftId,
		active_driver_id: t.driverId,
		driver_id: t.driverId,
		vehicle_id: t.vehicleId,
		route_id: t.routeId,
		trang_thai: t.trangThai,
		thoi_gian_bat_dau: t.thoiGianBatDau,
		thoi_gian_ket_thuc: t.thoiGianKetThuc,
		driver: t.driver ? formatDriver(t.driver) : null,
		vehicle: t.vehicle ? formatVehicle(t.vehicle) : null,
		route: t.route ? formatRoute(t.route) : null,
		latest_telemetry: latestLog
			? {
					vi_do_gps: latestLog.viDo,
					kinh_do_gps: latestLog.kinhDo,
					toc_do_kmh: latestLog.tocDo,
					co2_ppm: latestLog.co2Level || 680,
					marker_color: isDanger ? 'RED' : 'GREEN',
					thoi_gian_ghi_nhan: latestLog.thoiGianGhiNhan,
				}
			: {
					vi_do_gps: 16.0544,
					kinh_do_gps: 108.2022,
					toc_do_kmh: 60.0,
					co2_ppm: 680,
					marker_color: isDanger ? 'RED' : 'GREEN',
					thoi_gian_ghi_nhan: new Date(),
				},
	};
};

export const getAllTrips = async ({ driverId, status } = {}) => {
	const where = {};
	if (driverId) where.driverId = parseInt(driverId, 10);
	if (status) where.trangThai = status;

	const trips = await prisma.trip.findMany({
		where,
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
			violations: true,
			logs: { take: 1, orderBy: { thoiGianGhiNhan: 'desc' } },
		},
		orderBy: { createdAt: 'desc' },
	});

	return trips.map(formatTrip);
};

export const getDriverActiveTrip = async (driverId) => {
	const id = parseInt(driverId, 10);
	const activeTrip = await prisma.trip.findFirst({
		where: {
			driverId: id,
			trangThai: 'IN_PROGRESS',
		},
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
			violations: true,
			logs: { take: 1, orderBy: { thoiGianGhiNhan: 'desc' } },
		},
	});

	if (activeTrip) return formatTrip(activeTrip);

	const plannedTrip = await prisma.trip.findFirst({
		where: {
			driverId: id,
			trangThai: 'PLANNED',
		},
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
			violations: true,
			logs: { take: 1, orderBy: { thoiGianGhiNhan: 'desc' } },
		},
		orderBy: { createdAt: 'desc' },
	});

	return plannedTrip ? formatTrip(plannedTrip) : null;
};

export const getTripById = async (id) => {
	const trip = await prisma.trip.findUnique({
		where: { id: parseInt(id, 10) },
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
			violations: true,
			logs: { take: 1, orderBy: { thoiGianGhiNhan: 'desc' } },
		},
	});

	return trip ? formatTrip(trip) : null;
};

export const createTrip = async ({ driverId, vehicleId, routeId, shiftId }) => {
	const parsedDriverId = parseInt(driverId, 10);
	const parsedVehicleId = parseInt(vehicleId, 10);

	const existingDriverTrip = await prisma.trip.findFirst({
		where: { driverId: parsedDriverId, trangThai: 'IN_PROGRESS' },
	});
	if (existingDriverTrip) {
		throw new Error('Tài xế này hiện đang trong một chuyến xe khác chưa hoàn thành.');
	}

	const existingVehicleTrip = await prisma.trip.findFirst({
		where: { vehicleId: parsedVehicleId, trangThai: 'IN_PROGRESS' },
	});
	if (existingVehicleTrip) {
		throw new Error(
			'Phương tiện này hiện đang trong một chuyến xe khác chưa hoàn thành.',
		);
	}

	const trip = await prisma.trip.create({
		data: {
			shiftId: shiftId ? parseInt(shiftId, 10) : null,
			driverId: parsedDriverId,
			vehicleId: parsedVehicleId,
			routeId: parseInt(routeId, 10),
			trangThai: 'PLANNED',
		},
		include: {
			driver: { include: { user: true } },
			vehicle: true,
			route: true,
		},
	});

	return formatTrip(trip);
};

export const updateTripStatus = async (id, trangThai) => {
	const tripId = parseInt(id, 10);
	const trip = await prisma.trip.findUnique({ where: { id: tripId } });
	if (!trip) throw new Error('Chuyến xe không tồn tại.');

	const data = { trangThai };

	const updatedTrip = await prisma.$transaction(async (tx) => {
		if (trangThai === 'IN_PROGRESS') {
			// Tự động kết thúc các chuyến cũ bị treo của cùng tài xế hoặc cùng xe để không bị kẹt khi Demo
			await tx.trip.updateMany({
				where: {
					OR: [{ driverId: trip.driverId }, { vehicleId: trip.vehicleId }],
					trangThai: 'IN_PROGRESS',
					id: { not: tripId },
				},
				data: { trangThai: 'COMPLETED', thoiGianKetThuc: new Date() },
			});

			data.thoiGianBatDau = trip.thoiGianBatDau || new Date();
			await tx.driver.update({
				where: { userId: trip.driverId },
				data: { trangThaiTaiXe: 'ON_TRIP' },
			});
			await tx.vehicle.update({
				where: { id: trip.vehicleId },
				data: { trangThai: 'IN_USE' },
			});
			if (trip.shiftId) {
				await tx.shift.update({
					where: { id: trip.shiftId },
					data: { trangThai: 'IN_PROGRESS' },
				});
			}
		} else if (trangThai === 'RESTING') {
			await tx.driver.update({
				where: { userId: trip.driverId },
				data: { trangThaiTaiXe: 'ON_TRIP' },
			});
		} else if (trangThai === 'COMPLETED' || trangThai === 'CANCELLED') {
			data.thoiGianKetThuc = new Date();
			await tx.driver.update({
				where: { userId: trip.driverId },
				data: { trangThaiTaiXe: 'AVAILABLE' },
			});
			await tx.vehicle.update({
				where: { id: trip.vehicleId },
				data: { trangThai: 'AVAILABLE' },
			});
			if (trip.shiftId) {
				await tx.shift.update({
					where: { id: trip.shiftId },
					data: {
						trangThai: trangThai === 'COMPLETED' ? 'COMPLETED' : 'CANCELLED',
					},
				});
			}
		}

		return tx.trip.update({
			where: { id: tripId },
			data,
			include: {
				driver: { include: { user: true } },
				vehicle: { include: { iotDevice: true } },
				route: true,
			},
		});
	});

	if (trangThai === 'COMPLETED') {
		try {
			await grantSafeTripReward(tripId);
		} catch (e) {
			console.error('Error awarding safe trip reward:', e);
		}
	}

	return formatTrip(updatedTrip);
};

export const grantSafeTripReward = async (tripId) => {
	const pTripId = parseInt(tripId, 10);
	const trip = await prisma.trip.findUnique({
		where: { id: pTripId },
		include: { route: true, violations: true, driver: true },
	});

	if (!trip || trip.trangThai !== 'COMPLETED') return null;

	if (!trip.thoiGianBatDau || !trip.thoiGianKetThuc) return null;
	const durationSeconds =
		(new Date(trip.thoiGianKetThuc).getTime() -
			new Date(trip.thoiGianBatDau).getTime()) /
		1000;
	if (durationSeconds < 30) {
		return null;
	}

	const existingRewardLog = await prisma.driverScoreLog.findFirst({
		where: {
			tripId: pTripId,
			loaiThayDoi: { in: ['CONG_DIEM', 'CONG_DIEM_THUONG'] },
		},
	});
	if (existingRewardLog) return null;

	const hasViolations = trip.violations.some((v) =>
		[
			'CONFIRMED',
			'MANUALLY_CONFIRMED',
			'PENDING_MANUAL_REVIEW',
			'PENDING_VERIFICATION',
		].includes(v.trangThai),
	);

	if (hasViolations) return null;

	const distance = trip.route?.khoangCachKm || 50;
	const rewardPts = distance >= 100 ? 10 : 5;

	const currentUyTin = trip.driver.uyTin;
	const newUyTin = Math.min(100, currentUyTin + rewardPts);

	return prisma.$transaction(async (tx) => {
		const updatedDriver = await tx.driver.update({
			where: { userId: trip.driverId },
			data: { uyTin: newUyTin },
		});

		const scoreLog = await tx.driverScoreLog.create({
			data: {
				driverId: trip.driverId,
				loaiThayDoi: 'CONG_DIEM_THUONG',
				soDiemThayDoi: rewardPts,
				diemTruoc: currentUyTin,
				diemSau: newUyTin,
				lyDo: `Thưởng chuyến xe an toàn (${trip.route?.tenTuyen || 'Tuyến đường'}, ${distance}km).`,
				tripId: pTripId,
			},
		});

		return { updatedDriver, scoreLog };
	});
};

export const getTripPlaybackData = async (tripId) => {
	const id = parseInt(tripId, 10);
	const trip = await prisma.trip.findUnique({
		where: { id },
		include: {
			route: true,
			vehicle: { include: { iotDevice: true } },
			driver: { include: { user: true } },
			violations: true,
		},
	});

	if (!trip) {
		throw new Error('Không tìm thấy chuyến xe.');
	}

	const logs = await prisma.iotDataLog.findMany({
		where: { tripId: id },
		orderBy: { thoiGianGhiNhan: 'asc' },
	});

	return {
		trip: formatTrip(trip),
		logs,
		violations: trip.violations,
	};
};

export const calibrateAndStartTrip = async ({
	driverId,
	shiftId,
	tripId,
	isOfflineMode = false,
}) => {
	const pDriverId = parseInt(driverId, 10);
	let targetTrip = null;

	if (shiftId) {
		targetTrip = await prisma.trip.findUnique({
			where: { shiftId: parseInt(shiftId, 10) },
		});
	}
	if (!targetTrip && tripId) {
		targetTrip = await prisma.trip.findUnique({
			where: { id: parseInt(tripId, 10) },
		});
	}

	// Nếu ca trực chưa có Trip thì tự động tạo Trip từ Shift
	if (!targetTrip && shiftId) {
		const shift = await prisma.shift.findUnique({
			where: { id: parseInt(shiftId, 10) },
		});
		if (shift) {
			targetTrip = await prisma.trip.create({
				data: {
					shiftId: shift.id,
					driverId: shift.driverId,
					vehicleId: shift.vehicleId,
					routeId: shift.routeId,
					trangThai: 'PLANNED',
				},
			});
		}
	}

	// Chuyển Trip, Shift, Vehicle, Driver sang trạng thái đang vận hành (IN_PROGRESS / IN_USE / ON_TRIP)
	if (targetTrip) {
		targetTrip = await updateTripStatus(targetTrip.id, 'IN_PROGRESS');
	}

	// Lưu dữ liệu hiệu chuẩn
	await prisma.driverCalibrationData.create({
		data: {
			driverId: pDriverId,
			tripId: targetTrip?.id || null,
			shiftId: shiftId ? parseInt(shiftId, 10) : targetTrip?.shift_id || null,
			vectorDacTrung: JSON.stringify({
				ear_baseline: 0.285,
				mode: isOfflineMode ? 'PENDING_CALIBRATION' : 'ONLINE_SUCCESS',
			}),
			ketQua: 'SUCCESS',
		},
	});

	return targetTrip;
};

export const switchActiveDriver = async (
	tripId,
	{ newDriverId, isOfflineMode = false },
) => {
	const id = parseInt(tripId, 10);
	const trip = await prisma.trip.findUnique({ where: { id } });
	if (!trip) throw new Error('Không tìm thấy chuyến đi để đổi tài.');

	// Nếu Mobile không truyền newDriverId, tự tìm một tài xế AVAILABLE khác trong đội
	let targetDriverId = newDriverId ? parseInt(newDriverId, 10) : null;
	if (!targetDriverId) {
		const anotherDriver = await prisma.driver.findFirst({
			where: {
				userId: { not: trip.driverId },
				capCanhBao: { not: 'SUSPENDED_RED' },
			},
		});
		targetDriverId = anotherDriver ? anotherDriver.userId : trip.driverId;
	}

	const updated = await prisma.trip.update({
		where: { id },
		data: { driverId: targetDriverId },
		include: {
			driver: { include: { user: true } },
			vehicle: { include: { iotDevice: true } },
			route: true,
		},
	});

	await prisma.driverCalibrationData.create({
		data: {
			driverId: targetDriverId,
			tripId: id,
			shiftId: trip.shiftId,
			vectorDacTrung: JSON.stringify({
				action: 'SWITCH_CO_DRIVER',
				mode: isOfflineMode ? 'PENDING_CALIBRATION' : 'ONLINE_SUCCESS',
			}),
			ketQua: 'SUCCESS',
		},
	});

	return formatTrip(updated);
};
