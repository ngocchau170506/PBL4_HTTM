import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';

export const getPriorityFleet = async () => {
	// Chỉ lấy các phương tiện đang hoạt động (Không đưa xe đang bảo dưỡng MAINTENANCE lên bản đồ cao tốc)
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

		const isOnTrip = Boolean(activeTrip);
		const isDanger =
			isOnTrip &&
			(latestViolation?.loaiViPham?.includes('NGỦ GẬT') ||
				latestViolation?.loaiViPham?.includes('MICROSLEEP') ||
				(latestLog?.co2Level && latestLog.co2Level > 1100));

		const speed =
			isOnTrip && latestLog?.tocDo != null ? Math.round(latestLog.tocDo) : 0;
		const co2 =
			latestLog?.co2Level != null ? Math.round(latestLog.co2Level) : 450;

		let restStopName = 'Trạm Dừng Nghỉ Nam Hải Vân (Km 22)';
		if (routeObj?.danhSachTramDungChan) {
			try {
				const parsedStops = JSON.parse(routeObj.danhSachTramDungChan);
				if (Array.isArray(parsedStops) && parsedStops.length > 0 && parsedStops[0]?.ten) {
					restStopName = `${parsedStops[0].ten}${parsedStops[0].kmMarker ? ` (Km ${parsedStops[0].kmMarker})` : ''}`;
				}
			} catch {
				// fallback to default name
			}
		}

		const driverDisplayName = driver?.user?.hoTen
			? isOnTrip
				? driver.user.hoTen
				: `${driver.user.hoTen} (Chờ xuất bến)`
			: 'Trống tài (Tại bãi)';

		return {
			id: v.id,
			vehicle_id: v.id,
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
			lat: latestLog?.viDo || 16.0544 + (idx === 0 ? 0.015 : idx * 0.018 - 0.025),
			lng:
				latestLog?.kinhDo || 108.2022 + (idx === 0 ? -0.012 : idx * 0.015 - 0.02),
			priorityLevel: isDanger ? 'DANGER' : 'SAFE',
			priorityBadge: isDanger
				? 'CAN THIỆP GẤP'
				: isOnTrip
					? 'ĐANG LĂN BÁNH'
					: 'CHỜ XUẤT BẾN',
			alertType: isDanger
				? latestViolation?.maLoai || latestViolation?.loaiViPham || 'MICROSLEEP'
				: 'NORMAL',
			alertDesc: isDanger
				? latestViolation?.moTa ||
					'Phát hiện mắt nhắm hoặc nồng độ CO2 buồng lái vượt ngưỡng an toàn!'
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
			countdownSeconds: isDanger ? 769 : 900,
			nearestRestStop: {
				name: restStopName,
			},
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

export const dispatchVehicleToRestStop = async (
	{ vehiclePlate, restStopName },
	currentUser,
) => {
	logAuditAction({
		userId: currentUser?.id || 1,
		hanhDong: 'DISPATCH_REST_STOP',
		doiTuongLoai: 'VEHICLE',
		doiTuongId: vehiclePlate,
		giaTriSau: { vehiclePlate, restStopName },
		lyDo: `Điều phối viên phát lệnh điều hướng xe ${vehiclePlate} vào ${restStopName}`,
	});

	return {
		success: true,
		message: `Đã phát lệnh điều hướng xe ${vehiclePlate} vào ${restStopName}!`,
	};
};
