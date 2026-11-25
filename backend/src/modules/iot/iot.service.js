import {
	VIOLATION_TYPES,
	resolveViolationFromFlag,
} from '../../common/constants/violationTypes.js';
import { sendCriticalAdminEmailAlert } from '../../common/email.service.js';
import prisma from '../../common/prisma.js';
import { getAiConfig } from '../configs/configs.service.js';
import {
	createViolationRecord,
	enrichViolationRecord,
} from '../violations/violations.service.js';

let ioInstance = null;
const highCo2StartedAt = new Map();
const HIGH_CO2_SUSTAINED_MS =
	parseInt(process.env.HIGH_CO2_SUSTAINED_SEC || '300', 10) * 1000;
const HIGH_CO2_COOLDOWN_MS = 15 * 60 * 1000;

export const setSocketIO = (io) => {
	ioInstance = io;
};

export const getSocketIO = () => ioInstance;

const resolveDeviceRecord = async (rawDeviceId) => {
	if (rawDeviceId === undefined || rawDeviceId === null || rawDeviceId === '') {
		return null;
	}
	const strId = String(rawDeviceId).trim();
	if (/^\d+$/.test(strId)) {
		const numId = parseInt(strId, 10);
		if (numId > 0) {
			const byNum = await prisma.iotDevice.findUnique({
				where: { id: numId },
				include: { vehicle: true },
			});
			if (byNum) return byNum;
		}
	}
	return prisma.iotDevice.findFirst({
		where: {
			OR: [
				{ maThietBi: strId },
				{ macAddress: strId },
				{ cpuSerial: strId },
			],
		},
		include: { vehicle: true },
	});
};

export const processIotDataPacket = async ({
	tripId,
	trip_id,
	deviceId,
	device_id,
	active_driver_id,
	kinhDo,
	kinh_do_gps,
	viDo,
	vi_do_gps,
	tocDo,
	toc_do_kmh,
	co2Level,
	co2_ppm,
	aiViolationFlag,
}) => {
	const rawTripId = tripId ?? trip_id;
	const rawDeviceId = deviceId ?? device_id ?? 1;
	let parsedTripId = rawTripId ? parseInt(rawTripId, 10) : null;
	let parsedDeviceId = parseInt(rawDeviceId, 10);

	const lat = parseFloat(viDo ?? vi_do_gps ?? 16.0544);
	const lng = parseFloat(kinhDo ?? kinh_do_gps ?? 108.2022);
	const speed = parseFloat(tocDo ?? toc_do_kmh ?? 60);
	const rawCo2 = co2Level ?? co2_ppm;
	const co2 = rawCo2 !== undefined && rawCo2 !== null ? parseFloat(rawCo2) : null;

	// 1. Tự tìm thông tin Device và Chuyến đi (Hỗ trợ cả ID số, mã IOT-DN-xx, MAC hoặc CPU Serial)
	let targetDevice = await resolveDeviceRecord(rawDeviceId);

	if (!targetDevice) {
		targetDevice = await prisma.iotDevice.findFirst({ include: { vehicle: true } });
	}
	if (targetDevice) {
		parsedDeviceId = targetDevice.id;
	}

	let tripInfo = null;
	if (parsedTripId) {
		tripInfo = await prisma.trip.findUnique({
			where: { id: parsedTripId },
			include: {
				driver: { include: { user: true } },
				vehicle: { include: { iotDevice: true } },
				route: true,
			},
		});
		if (tripInfo?.vehicle?.iotDevice?.id) {
			parsedDeviceId = tripInfo.vehicle.iotDevice.id;
		}
	} else if (targetDevice?.vehicleId) {
		tripInfo = await prisma.trip.findFirst({
			where: {
				vehicleId: targetDevice.vehicleId,
				trangThai: { in: ['IN_PROGRESS', 'RESTING', 'CALIBRATING'] },
			},
			include: {
				driver: { include: { user: true } },
				vehicle: { include: { iotDevice: true } },
				route: true,
			},
			orderBy: { id: 'desc' },
		});
		if (tripInfo) parsedTripId = tripInfo.id;
	}

	// 2. Lưu log Telemetry vào DB
	const logEntry = await prisma.iotDataLog.create({
		data: {
			tripId: parsedTripId,
			deviceId: parsedDeviceId,
			kinhDo: lng,
			viDo: lat,
			tocDo: speed,
			co2Level: co2,
			thoiGianGhiNhan: new Date(),
		},
	});

	const config = await getAiConfig();
	const co2Threshold = config.co2_warning_threshold_ppm || 1200;
	const isCo2Elevated = co2 !== null && co2 > co2Threshold;
	const markerColor = aiViolationFlag
		? 'RED'
		: isCo2Elevated
			? 'YELLOW'
			: 'GREEN';

	// 3. Phát sự kiện telemetry:update lên Leaflet Map
	if (ioInstance) {
		const telemetryPayload = {
			logId: logEntry.id,
			tripId: parsedTripId,
			trip_id: parsedTripId,
			deviceId: parsedDeviceId,
			vehicle_id: tripInfo?.vehicle?.id || parsedDeviceId,
			bien_so_xe: tripInfo?.vehicle?.bienSo || '43C-123.45',
			active_driver_name: tripInfo?.driver?.user?.hoTen || 'Tài xế',
			lat,
			lng,
			vi_do_gps: lat,
			kinh_do_gps: lng,
			speed,
			toc_do_kmh: speed,
			co2Level: co2,
			co2_ppm: co2 || 680,
			marker_color: markerColor,
			aiViolationFlag: aiViolationFlag || null,
			timestamp: logEntry.thoiGianGhiNhan,
			thoi_gian_ghi_nhan: logEntry.thoiGianGhiNhan,
			vehicle: tripInfo?.vehicle || null,
			driver: tripInfo?.driver || null,
			route: tripInfo?.route || null,
		};

		ioInstance.emit('gps_update', telemetryPayload);
		ioInstance.emit('telemetry:update', telemetryPayload);
	}

	// 4. Tự động tạo vi phạm & bắn Socket.io violation:new nếu có cờ AI hoặc CO2 vượt ngưỡng kéo dài
	if (tripInfo && tripInfo.driver) {
		const driverId = tripInfo.driverId;
		let detectedType = null;
		let cooldownMs = 45 * 1000;

		if (aiViolationFlag) {
			detectedType = resolveViolationFromFlag({
				aiViolationFlag,
				co2: null,
				speed,
				co2Threshold,
			});
		} else if (co2 !== null) {
			const co2Key = `trip_${parsedTripId || 'none'}_dev_${parsedDeviceId}`;
			if (co2 > co2Threshold) {
				const nowMs = Date.now();
				if (!highCo2StartedAt.has(co2Key)) {
					highCo2StartedAt.set(co2Key, nowMs);
				}
				if (nowMs - highCo2StartedAt.get(co2Key) >= HIGH_CO2_SUSTAINED_MS) {
					detectedType = VIOLATION_TYPES.HIGH_CO2;
					cooldownMs = HIGH_CO2_COOLDOWN_MS;
					highCo2StartedAt.set(co2Key, nowMs);
				}
			} else if (co2 < Math.min(co2Threshold - 200, 1000)) {
				highCo2StartedAt.delete(co2Key);
			}
		}

		if (detectedType) {
			const recentViolation = await prisma.violation.findFirst({
				where: {
					driverId,
					tripId: parsedTripId,
					loaiViPham: detectedType.code,
					thoiGianViPham: {
						gte: new Date(Date.now() - cooldownMs),
					},
				},
			});

			if (!recentViolation) {
				const { violation: newViolation, scoreChange } =
					await createViolationRecord({
						driverId,
						tripId: parsedTripId,
						violationTypeId: detectedType.id,
						loaiViPham: detectedType.code,
						diemTru: detectedType.defaultPoints,
						videoUrl: null,
						videoDuration: 10,
						aiConfidenceScore: 0.92,
						moTa: `${detectedType.icon} ${detectedType.description}`,
					});

				if (ioInstance) {
					const socketViolationPayload = {
						...newViolation,
						violation_id: newViolation.id,
						bien_so_xe: tripInfo.vehicle?.bienSo || '43C-123.45',
						driver_name: tripInfo.driver?.user?.hoTen || 'Tài xế',
						muc_do_nghiem_trong:
							detectedType.muc_do_nghiem_trong || 'CRITICAL',
					};

					ioInstance.emit('violation:new', socketViolationPayload);
					ioInstance.emit('new_violation', socketViolationPayload);
					if (scoreChange) {
						ioInstance.emit('driver:score_changed', {
							driver_id: driverId,
							...scoreChange,
						});
					}
				}
			}
		}
	}

	return { success: true, logEntry };
};

// 5. Xử lý API POST /api/v1/edge/violations (Có chống trùng Idempotency-Key & Upload Video)
export const processEdgeViolation = async ({
	clientEventId,
	device_id,
	deviceId,
	ma_thiet_bi,
	maThietBi,
	trip_id,
	tripId,
	driver_id,
	driverId,
	ma_loai,
	loaiViPham,
	edge_confidence,
	ai_confidence_score,
	videoUrl,
	mo_ta,
	moTa,
}) => {
	// Kiểm tra Idempotency-Key: Nếu Pi 4 gửi lại cùng client_event_id thì trả về bản ghi cũ, KHÔNG trừ điểm 2 lần!
	if (clientEventId) {
		const existing = await prisma.violation.findFirst({
			where: { clientEventId: String(clientEventId) },
			include: {
				driver: { include: { user: true } },
				trip: { include: { vehicle: true, route: true } },
			},
		});
		if (existing) {
			return {
				isDuplicate: true,
				violation: enrichViolationRecord(existing),
			};
		}
	}

	const rawTripId = trip_id ?? tripId;
	const explicitTripId = rawTripId ? parseInt(rawTripId, 10) : NaN;
	let parsedDriverId = parseInt(driver_id || driverId || 0, 10);

	const tripInclude = {
		driver: { include: { user: true } },
		vehicle: true,
	};

	let tripInfo = null;
	if (!Number.isNaN(explicitTripId) && explicitTripId > 0) {
		tripInfo = await prisma.trip.findUnique({
			where: { id: explicitTripId },
			include: tripInclude,
		});
	}

	let targetDevice = null;
	if (!tripInfo) {
		targetDevice = await resolveDeviceRecord(
			device_id ?? deviceId ?? ma_thiet_bi ?? maThietBi,
		);
		if (targetDevice?.vehicleId) {
			tripInfo = await prisma.trip.findFirst({
				where: {
					vehicleId: targetDevice.vehicleId,
					trangThai: { in: ['IN_PROGRESS', 'RESTING', 'CALIBRATING'] },
				},
				include: tripInclude,
				orderBy: { id: 'desc' },
			});
		}
	}

	if (!tripInfo) {
		tripInfo = await prisma.trip.findFirst({
			where: {
				trangThai: { in: ['IN_PROGRESS', 'RESTING', 'CALIBRATING'] },
			},
			include: tripInclude,
			orderBy: { id: 'desc' },
		});
	}

	if (!tripInfo && targetDevice?.vehicleId) {
		tripInfo = await prisma.trip.findFirst({
			where: { vehicleId: targetDevice.vehicleId },
			include: tripInclude,
			orderBy: { id: 'desc' },
		});
	}

	if (!tripInfo) {
		throw new Error(
			'Không tìm thấy chuyến đi đang hoạt động cho thiết bị gửi vi phạm.',
		);
	}

	const parsedTripId = tripInfo.id;
	if (tripInfo.driverId) {
		parsedDriverId = tripInfo.driverId;
	}

	const confidence = parseFloat(ai_confidence_score ?? edge_confidence ?? 0.82);

	const result = await createViolationRecord({
		driverId: parsedDriverId,
		tripId: parsedTripId,
		loaiViPham: ma_loai || loaiViPham || 'MICROSLEEP',
		videoUrl: videoUrl || null,
		videoDuration: 10,
		aiConfidenceScore: confidence,
		moTa: mo_ta || moTa,
		clientEventId: clientEventId ? String(clientEventId) : null,
	});

	if (ioInstance) {
		const socketPayload = {
			...result.violation,
			violation_id: result.violation.id,
			bien_so_xe: tripInfo?.vehicle?.bienSo || '43H-567.89',
			driver_name: tripInfo?.driver?.user?.hoTen || 'Tài xế',
		};
		ioInstance.emit('violation:new', socketPayload);
		if (result.scoreChange) {
			ioInstance.emit('driver:score_changed', {
				driver_id: parsedDriverId,
				...result.scoreChange,
			});
		}
	}

	return {
		isDuplicate: false,
		violation: result.violation,
	};
};

// 6. Xử lý API POST /api/v1/edge/heartbeat (Cập nhật nhiệt độ CPU, bộ nhớ trống)
export const processDeviceHeartbeat = async ({
	device_id,
	deviceId,
	ma_thiet_bi,
	cpu_temp_c,
	free_storage_mb,
	trang_thai_ket_noi,
	ai_model_version,
}) => {
	let targetDevice = null;
	if (device_id || deviceId) {
		targetDevice = await prisma.iotDevice.findUnique({
			where: { id: parseInt(device_id || deviceId, 10) },
		});
	} else if (ma_thiet_bi) {
		targetDevice = await prisma.iotDevice.findFirst({
			where: { maThietBi: ma_thiet_bi },
		});
	}

	if (!targetDevice) {
		targetDevice = await prisma.iotDevice.findFirst();
	}

	if (!targetDevice) {
		throw new Error('Không tìm thấy thiết bị IoT để cập nhật heartbeat.');
	}

	const updated = await prisma.iotDevice.update({
		where: { id: targetDevice.id },
		data: {
			cpuTempC:
				cpu_temp_c !== undefined ? parseFloat(cpu_temp_c) : targetDevice.cpuTempC,
			freeStorageMb:
				free_storage_mb !== undefined
					? parseInt(free_storage_mb, 10)
					: targetDevice.freeStorageMb,
			trangThaiKetNoi: trang_thai_ket_noi || 'ONLINE',
			aiModelVersion: ai_model_version || targetDevice.aiModelVersion,
			lastHeartbeatAt: new Date(),
		},
	});

	if (ioInstance) {
		ioInstance.emit('device:health', {
			device_id: updated.id,
			vehicle_id: updated.vehicleId,
			ma_thiet_bi: updated.maThietBi,
			cpu_temp_c: updated.cpuTempC,
			free_storage_mb: updated.freeStorageMb,
			trang_thai_ket_noi: updated.trangThaiKetNoi,
			last_heartbeat_at: updated.lastHeartbeatAt,
		});
	}

	// Tự động kích hoạt quy tắc chống gian lận rút cáp Camera USB khi xe đang chạy (SRS § 3.1 & AC_08)
	if (trang_thai_ket_noi === 'ERROR_CAMERA' && updated.vehicleId) {
		try {
			const activeTrip = await prisma.trip.findFirst({
				where: { vehicleId: updated.vehicleId, trangThai: 'IN_PROGRESS' },
				include: { driver: { include: { user: true } }, vehicle: true },
			});

			if (activeTrip && activeTrip.driverId) {
				const recentTampering = await prisma.violation.findFirst({
					where: {
						driverId: activeTrip.driverId,
						tripId: activeTrip.id,
						loaiViPham: 'DEVICE_TAMPERING',
						thoiGianViPham: { gte: new Date(Date.now() - 5 * 60 * 1000) },
					},
				});

				if (!recentTampering) {
					const { violation: tamperViolation, scoreChange: tamperScoreChange } =
						await createViolationRecord({
							driverId: activeTrip.driverId,
							tripId: activeTrip.id,
							loaiViPham: 'DEVICE_TAMPERING',
							diemTru: 15,
							aiConfidenceScore: 0.99,
							moTa: 'CẢNH BÁO: Mất tín hiệu Camera USB khi xe đang vận hành (Nghi vấn can thiệp thiết bị / rút cáp).',
						});

					if (ioInstance) {
						ioInstance.emit('violation:new', {
							...tamperViolation,
							violation_id: tamperViolation.id,
							bien_so_xe: activeTrip.vehicle?.bienSo || 'Xe IoT',
							driver_name: activeTrip.driver?.user?.hoTen || 'Tài xế',
							muc_do_nghiem_trong: 'CRITICAL',
						});
						if (tamperScoreChange) {
							ioInstance.emit('driver:score_changed', {
								driver_id: activeTrip.driverId,
								...tamperScoreChange,
							});
						}
					}

					sendCriticalAdminEmailAlert({
						alertType: 'DEVICE_TAMPERING',
						driverName: activeTrip.driver?.user?.hoTen || 'Tài xế',
						bienSo: activeTrip.vehicle?.bienSo || updated.maThietBi,
						uyTinConLai: tamperScoreChange?.diem_sau,
						detail: tamperViolation.moTa,
					}).catch(() => {});
				}
			}
		} catch (err) {
			console.error('Lỗi khi kích hoạt vi phạm rút cáp camera:', err);
		}
	}

	return updated;
};
