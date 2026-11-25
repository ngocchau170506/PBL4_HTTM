import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';
import { BadRequestException } from '../../common/exceptions/index.js';
import { getSocketIO } from '../iot/iot.service.js';
import {
	buildGoogleMapsUrl,
	computeDrivingRoute,
	findNearbyRestStops,
	haversineKm,
} from './geoNavigation.service.js';

const activeRestDispatches = new Map();

const parseRouteStops = (rawStops) => {
	if (!rawStops) return [];
	try {
		const parsed = typeof rawStops === 'string' ? JSON.parse(rawStops) : rawStops;
		return Array.isArray(parsed) ? parsed : [];
	} catch {
		return [];
	}
};

const pickClosestDbStop = (routeObj, lat, lng) => {
	const stops = parseRouteStops(routeObj?.danhSachTramDungChan);
	if (stops.length === 0) {
		return {
			name: 'Trạm Dừng Nghỉ Nam Hải Vân (Km 22)',
			lat: 16.1485,
			lng: 108.1322,
			distanceKm: Number(haversineKm(lat, lng, 16.1485, 108.1322).toFixed(1)),
			googleMapsUrl: buildGoogleMapsUrl(16.1485, 108.1322),
		};
	}

	let best = null;
	for (const s of stops) {
		const sLat = Number(s.vi_do ?? s.lat);
		const sLng = Number(s.kinh_do ?? s.lng);
		const dist =
			Number.isFinite(sLat) && Number.isFinite(sLng)
				? haversineKm(lat, lng, sLat, sLng)
				: 9999;
		const label = `${s.ten || 'Trạm dừng'}${s.kmMarker || s.km_marker ? ` (Km ${s.kmMarker || s.km_marker})` : ''}`;
		if (!best || dist < best.distanceKm) {
			best = {
				name: label,
				lat: Number.isFinite(sLat) ? sLat : null,
				lng: Number.isFinite(sLng) ? sLng : null,
				distanceKm: Number(dist.toFixed(1)),
				googleMapsUrl: buildGoogleMapsUrl(sLat, sLng),
			};
		}
	}
	return best;
};

export const getPriorityFleet = async () => {
	const vehicles = await prisma.vehicle.findMany({
		where: {
			trangThai: { not: 'MAINTENANCE' },
		},
		include: {
			iotDevice: {
				include: {
					logs: {
						take: 1,
						orderBy: { thoiGianGhiNhan: 'desc' },
					},
				},
			},
			trips: {
				where: { trangThai: { in: ['IN_PROGRESS', 'RESTING'] } },
				include: {
					driver: { include: { user: true } },
					route: true,
					violations: {
						where: {
							trangThai: { in: ['CONFIRMED', 'PENDING_MANUAL_REVIEW'] },
						},
						orderBy: { thoiGianViPham: 'desc' },
						take: 1,
					},
				},
				orderBy: { id: 'desc' },
				take: 1,
			},
			shifts: {
				where: { trangThai: { in: ['IN_PROGRESS', 'ACCEPTED', 'SCHEDULED'] } },
				include: {
					driver: { include: { user: true } },
					route: true,
				},
				orderBy: { thoiGianBatDauDuKien: 'desc' },
				take: 1,
			},
		},
		orderBy: { id: 'asc' },
	});

	return vehicles.map((v, idx) => {
		const activeTrip = v.trips?.[0] || null;
		const activeShift = v.shifts?.[0] || null;
		const driver = activeTrip?.driver || activeShift?.driver || null;
		const routeObj = activeTrip?.route || activeShift?.route || null;
		const latestLog = v.iotDevice?.logs?.[0] || null;
		const latestViolation = activeTrip?.violations?.[0] || null;

		const isOnTrip = activeTrip?.trangThai === 'IN_PROGRESS';
		const isResting = activeTrip?.trangThai === 'RESTING';

		if (!isOnTrip) {
			activeRestDispatches.delete(v.id);
		}
		const activeDispatch = isOnTrip ? activeRestDispatches.get(v.id) || null : null;

		const isDanger =
			isOnTrip &&
			(latestViolation?.loaiViPham?.includes('NGỦ GẬT') ||
				latestViolation?.loaiViPham?.includes('MICROSLEEP') ||
				latestViolation?.loaiViPham === 'HIGH_CO2' ||
				(latestLog?.co2Level && latestLog.co2Level > 1100));

		const speed =
			isOnTrip && latestLog?.tocDo != null ? Math.round(latestLog.tocDo) : 0;
		const co2 =
			latestLog?.co2Level != null ? Math.round(latestLog.co2Level) : 450;

		const lat =
			latestLog?.viDo || 16.0544 + (idx === 0 ? 0.015 : idx * 0.018 - 0.025);
		const lng =
			latestLog?.kinhDo || 108.2022 + (idx === 0 ? -0.012 : idx * 0.015 - 0.02);

		const nearestRestStop = pickClosestDbStop(routeObj, lat, lng);

		// Đồng hồ đếm ngược 15p chỉ kích hoạt khi xe đang chạy + ở trạng thái nguy hiểm hoặc đang có lệnh điều hướng
		let countdownActive = false;
		let restDeadlineAt = null;
		if (isOnTrip && activeDispatch?.restDeadlineAt) {
			countdownActive = true;
			restDeadlineAt = activeDispatch.restDeadlineAt;
		} else if (isDanger) {
			const baseMs = latestViolation?.thoiGianViPham
				? new Date(latestViolation.thoiGianViPham).getTime()
				: Date.now();
			countdownActive = true;
			restDeadlineAt = new Date(baseMs + 15 * 60 * 1000).toISOString();
		}

		const countdownSeconds =
			countdownActive && restDeadlineAt
				? Math.max(0, Math.round((new Date(restDeadlineAt).getTime() - Date.now()) / 1000))
				: null;

		const driverDisplayName = driver?.user?.hoTen
			? isOnTrip || isResting
				? driver.user.hoTen
				: `${driver.user.hoTen} (Chờ xuất bến)`
			: 'Trống tài (Tại bãi)';

		return {
			id: v.id,
			vehicle_id: v.id,
			trip_id: activeTrip?.id || null,
			trip_status: activeTrip?.trangThai || null,
			driver_id: driver?.userId || null,
			route_id: routeObj?.id || null,
			plate: v.bienSo,
			bien_so_xe: v.bienSo,
			bien_so: v.bienSo,
			model: v.loaiXe || 'Xe Tải Thùng',
			loai_xe: v.loaiXe || 'Xe Tải Thùng',
			driver: driverDisplayName,
			driver_name: driverDisplayName,
			driverCode: driver?.userId ? `TX-010${driver.userId}` : 'CHƯA GÁN',
			experience: `${3 + (driver?.userId || idx)} năm`,
			score: driver?.uyTin ?? 100,
			diem_uy_tin: driver?.uyTin ?? 100,
			route: routeObj?.tenTuyen || 'Đang đỗ tại bãi tập kết Đà Nẵng',
			speed: `${speed} km/h`,
			co2: `${co2.toLocaleString()} ppm`,
			co2_ppm: co2,
			lat,
			lng,
			priorityLevel: isDanger ? 'DANGER' : 'SAFE',
			priorityBadge: isDanger
				? 'CAN THIỆP GẤP'
				: isResting
					? 'ĐANG TẠM NGHỈ'
					: isOnTrip
						? 'ĐANG LĂN BÁNH'
						: 'CHỜ XUẤT BẾN',
			alertType: isDanger
				? latestViolation?.maLoai || latestViolation?.loaiViPham || 'MICROSLEEP'
				: 'NORMAL',
			alertDesc: isDanger
				? latestViolation?.moTa ||
					'Phát hiện mắt nhắm hoặc nồng độ CO2 buồng lái vượt ngưỡng an toàn!'
				: isResting
					? 'Tài xế đang tạm dừng nghỉ an toàn tại trạm.'
					: isOnTrip
						? 'Phương tiện đang di chuyển ổn định, các chỉ số an toàn bình thường.'
						: 'Phương tiện đang dừng đỗ tại bãi, sẵn sàng cho ca chạy tiếp theo.',
			video_url: latestViolation?.videoUrl || null,
			ai_confidence_score: latestViolation?.aiConfidenceScore ?? null,
			latest_violation_id: latestViolation?.id || null,
			evidenceClip: latestViolation?.videoUrl
				? {
						videoUrl: latestViolation.videoUrl,
						aiConfidence: Math.round((latestViolation.aiConfidenceScore ?? 0.9) * 100),
					}
				: null,
			countdownActive,
			restDeadlineAt,
			countdownSeconds,
			activeDispatch,
			nearestRestStop,
			iot_device: v.iotDevice
				? {
						...v.iotDevice,
						ma_thiet_bi: v.iotDevice.maThietBi || `IOT-DN-0${v.id}`,
						cpu_serial: v.iotDevice.cpuSerial || null,
						cpu_temp_c: v.iotDevice.cpuTempC ?? null,
						free_storage_mb: v.iotDevice.freeStorageMb ?? null,
						ai_model_version: v.iotDevice.aiModelVersion || 'yolo26n-v1.1',
						trang_thai_ket_noi: v.iotDevice.trangThaiKetNoi || 'OFFLINE',
						last_heartbeat_at: v.iotDevice.lastHeartbeatAt || null,
					}
				: null,
		};
	});
};

export const getMonitoringStats = async () => {
	const startOfDay = new Date();
	startOfDay.setHours(0, 0, 0, 0);

	const [vehicles, activeVehicles, microsleepAlerts, fatigueWarnings] =
		await Promise.all([
			prisma.vehicle.count({ where: { trangThai: { not: 'MAINTENANCE' } } }),
			prisma.vehicle.count({
				where: {
					OR: [
						{ trangThai: 'IN_USE' },
						{
							trips: {
								some: { trangThai: { in: ['IN_PROGRESS', 'RESTING'] } },
							},
						},
					],
				},
			}),
			prisma.violation.count({
				where: {
					loaiViPham: { in: ['MICROSLEEP', 'BUON_NGU'] },
					trangThai: {
						in: ['CONFIRMED', 'MANUALLY_CONFIRMED', 'PENDING_MANUAL_REVIEW'],
					},
					thoiGianViPham: { gte: startOfDay },
				},
			}),
			prisma.violation.count({
				where: {
					loaiViPham: {
						in: [
							'YAWNING_FATIGUE',
							'HIGH_CO2',
							'PHONE_DISTRACTION',
							'DEVICE_TAMPERING',
						],
					},
					trangThai: {
						in: ['CONFIRMED', 'MANUALLY_CONFIRMED', 'PENDING_MANUAL_REVIEW'],
					},
					thoiGianViPham: { gte: startOfDay },
				},
			}),
		]);

	return {
		activeVehicles,
		microsleepAlerts,
		fatigueWarnings,
		normalVehicles: Math.max(0, vehicles - microsleepAlerts),
	};
};

export const getNearbyRestStopsForLocation = async ({
	lat,
	lng,
	vehicleId,
	routeId,
	limit = 3,
}) => {
	const pLat = parseFloat(lat);
	const pLng = parseFloat(lng);
	if (!Number.isFinite(pLat) || !Number.isFinite(pLng)) {
		throw new BadRequestException('Tọa độ lat/lng không hợp lệ.');
	}

	let fallbackStops = [];
	if (routeId) {
		const route = await prisma.route.findUnique({
			where: { id: parseInt(routeId, 10) },
		});
		fallbackStops = parseRouteStops(route?.danhSachTramDungChan);
	} else if (vehicleId) {
		const activeTrip = await prisma.trip.findFirst({
			where: {
				vehicleId: parseInt(vehicleId, 10),
				trangThai: { in: ['IN_PROGRESS', 'RESTING'] },
			},
			include: { route: true },
			orderBy: { id: 'desc' },
		});
		fallbackStops = parseRouteStops(activeTrip?.route?.danhSachTramDungChan);
	}

	if (fallbackStops.length === 0) {
		const anyRoute = await prisma.route.findFirst({ orderBy: { id: 'asc' } });
		fallbackStops = parseRouteStops(anyRoute?.danhSachTramDungChan);
	}

	const stops = await findNearbyRestStops({
		lat: pLat,
		lng: pLng,
		fallbackStops,
		limit: Math.min(10, Math.max(1, parseInt(limit, 10) || 3)),
	});

	if (stops.length > 0) {
		const first = stops[0];
		const routeInfo = await computeDrivingRoute({
			fromLat: pLat,
			fromLng: pLng,
			toLat: first.lat,
			toLng: first.lng,
		});
		stops[0] = {
			...first,
			distanceKm: routeInfo.distanceKm,
			etaMinutes: routeInfo.etaMinutes,
			routeSource: routeInfo.source,
			geometry: routeInfo.geometry,
		};
	}

	return {
		origin: { lat: pLat, lng: pLng },
		stops,
	};
};

export const dispatchVehicleToRestStop = async (
	{ vehicleId, vehiclePlate, restStopName, lat, lng, fromLat, fromLng, category },
	currentUser,
) => {
	let vehicle = null;
	if (vehicleId) {
		vehicle = await prisma.vehicle.findUnique({
			where: { id: parseInt(vehicleId, 10) },
			include: {
				iotDevice: { include: { logs: { take: 1, orderBy: { thoiGianGhiNhan: 'desc' } } } },
				trips: {
					where: { trangThai: { in: ['IN_PROGRESS', 'RESTING'] } },
					include: { driver: true, route: true },
					orderBy: { id: 'desc' },
					take: 1,
				},
			},
		});
	} else if (vehiclePlate) {
		vehicle = await prisma.vehicle.findUnique({
			where: { bienSo: String(vehiclePlate) },
			include: {
				iotDevice: { include: { logs: { take: 1, orderBy: { thoiGianGhiNhan: 'desc' } } } },
				trips: {
					where: { trangThai: { in: ['IN_PROGRESS', 'RESTING'] } },
					include: { driver: true, route: true },
					orderBy: { id: 'desc' },
					take: 1,
				},
			},
		});
	}

	const activeTrip = vehicle?.trips?.[0] || null;
	const latestLog = vehicle?.iotDevice?.logs?.[0] || null;
	const plate = vehicle?.bienSo || vehiclePlate || 'Xe IoT';

	const originLat = Number.isFinite(Number(fromLat))
		? Number(fromLat)
		: latestLog?.viDo || 16.0544;
	const originLng = Number.isFinite(Number(fromLng))
		? Number(fromLng)
		: latestLog?.kinhDo || 108.2022;

	let destLat = Number.isFinite(Number(lat)) ? Number(lat) : null;
	let destLng = Number.isFinite(Number(lng)) ? Number(lng) : null;
	let resolvedName = restStopName;
	let resolvedCategory = category || 'REST_AREA';

	if (destLat === null || destLng === null) {
		const fallbackStops = parseRouteStops(activeTrip?.route?.danhSachTramDungChan);
		const nearby = await findNearbyRestStops({
			lat: originLat,
			lng: originLng,
			fallbackStops,
			limit: 1,
		});
		if (nearby.length > 0) {
			destLat = nearby[0].lat;
			destLng = nearby[0].lng;
			resolvedName = resolvedName || nearby[0].name;
			resolvedCategory = category || nearby[0].category;
		} else {
			const fallbackStop = pickClosestDbStop(activeTrip?.route, originLat, originLng);
			destLat = fallbackStop.lat;
			destLng = fallbackStop.lng;
			resolvedName = resolvedName || fallbackStop.name;
		}
	}

	const routeInfo = await computeDrivingRoute({
		fromLat: originLat,
		fromLng: originLng,
		toLat: destLat,
		toLng: destLng,
	});

	const restDeadlineAt = new Date(Date.now() + 15 * 60 * 1000).toISOString();
	const dispatchPayload = {
		vehicle_id: vehicle?.id || null,
		vehiclePlate: plate,
		trip_id: activeTrip?.id || null,
		driver_id: activeTrip?.driverId || null,
		restStopName: resolvedName || 'Trạm dừng nghỉ gần nhất',
		category: resolvedCategory,
		lat: destLat,
		lng: destLng,
		originLat,
		originLng,
		distanceKm: routeInfo.distanceKm,
		etaMinutes: routeInfo.etaMinutes,
		geometry: routeInfo.geometry,
		routeSource: routeInfo.source,
		googleMapsUrl: buildGoogleMapsUrl(destLat, destLng),
		dispatchedAt: new Date().toISOString(),
		restDeadlineAt,
		dispatchedBy: currentUser?.hoTen || 'Điều phối viên',
	};

	if (vehicle?.id) {
		activeRestDispatches.set(vehicle.id, dispatchPayload);
	}

	logAuditAction({
		userId: currentUser?.id || 1,
		hanhDong: 'DISPATCH_REST_STOP',
		doiTuongLoai: 'VEHICLE',
		doiTuongId: plate,
		giaTriSau: {
			vehiclePlate: plate,
			restStopName: dispatchPayload.restStopName,
			lat: destLat,
			lng: destLng,
			distanceKm: routeInfo.distanceKm,
			etaMinutes: routeInfo.etaMinutes,
		},
		lyDo: `Điều phối viên phát lệnh điều hướng xe ${plate} vào ${dispatchPayload.restStopName} (${routeInfo.distanceKm} km, ~${routeInfo.etaMinutes} phút)`,
	});

	const io = getSocketIO();
	if (io) {
		io.emit('dispatch:rest_stop', dispatchPayload);
	}

	return {
		success: true,
		message: `Đã phát lệnh điều hướng xe ${plate} vào ${dispatchPayload.restStopName} (${routeInfo.distanceKm} km • ~${routeInfo.etaMinutes} phút)!`,
		dispatch: dispatchPayload,
	};
};
