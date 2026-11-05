import prisma from '../../common/prisma.js';
import { createViolationRecord } from '../violations/violations.service.js';
import { getAiConfig } from '../configs/configs.service.js';
import { resolveViolationFromFlag } from '../../common/constants/violationTypes.js';

let ioInstance = null;

export const setSocketIO = (io) => {
	ioInstance = io;
};

export const getSocketIO = () => ioInstance;

export const processIotDataPacket = async ({
	tripId,
	trip_id,
	deviceId,
	device_id,
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
	const parsedDeviceId = parseInt(rawDeviceId, 10);
	const lat = parseFloat(viDo ?? vi_do_gps);
	const lng = parseFloat(kinhDo ?? kinh_do_gps);
	const speed = parseFloat(tocDo ?? toc_do_kmh ?? 60);
	const rawCo2 = co2Level ?? co2_ppm;
	const co2 = rawCo2 !== undefined && rawCo2 !== null ? parseFloat(rawCo2) : null;

	// 1. Tự tìm chuyến IN_PROGRESS nếu phần cứng không truyền tripId
	if (!parsedTripId && parsedDeviceId) {
		const device = await prisma.iotDevice.findUnique({
			where: { id: parsedDeviceId },
			include: { vehicle: true },
		});
		if (device && device.vehicleId) {
			const activeTrip = await prisma.trip.findFirst({
				where: { vehicleId: device.vehicleId, trangThai: 'IN_PROGRESS' },
			});
			if (activeTrip) parsedTripId = activeTrip.id;
		}
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

	// 3. Lấy thông tin Trip & Tài xế
	let tripInfo = null;
	if (parsedTripId) {
		tripInfo = await prisma.trip.findUnique({
			where: { id: parsedTripId },
			include: {
				driver: { include: { user: true } },
				vehicle: true,
				route: true,
			},
		});
	}

	const isDangerFlag = Boolean(aiViolationFlag) || (co2 !== null && co2 > 1200);

	// Phát sự kiện telemetry:update lên Leaflet Map
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
			marker_color: isDangerFlag ? 'RED' : 'GREEN',
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

	// 4. Tự động tạo vi phạm & bắn Socket.io violation:new nếu có cờ AI
	if (tripInfo && tripInfo.driver) {
		const driverId = tripInfo.driverId;
		const videoUrl =
			'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4';

		const config = await getAiConfig();
		const co2Threshold = config.co2_warning_threshold_ppm || 1200;

		const detectedType = resolveViolationFromFlag({
			aiViolationFlag,
			co2,
			speed,
			co2Threshold,
		});

		if (detectedType) {
			const recentViolation = await prisma.violation.findFirst({
				where: {
					driverId,
					tripId: parsedTripId,
					loaiViPham: detectedType.code,
					thoiGianViPham: {
						gte: new Date(Date.now() - 45 * 1000),
					},
				},
			});

			if (!recentViolation) {
				const { violation: newViolation, updatedDriver } =
					await createViolationRecord({
						driverId,
						tripId: parsedTripId,
						violationTypeId: detectedType.id,
						loaiViPham: detectedType.code,
						diemTru: detectedType.defaultPoints,
						videoUrl: detectedType.need_video ? videoUrl : null,
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
					ioInstance.emit('violation_detected', {
						violation: socketViolationPayload,
						driver: tripInfo.driver,
						vehicle: tripInfo.vehicle,
						updatedUyTin: updatedDriver.uyTin,
					});
					ioInstance.emit('driver:score_changed', {
						driver_id: driverId,
						diem_sau: updatedDriver.uyTin,
						so_diem_thay_doi: -newViolation.diem_bi_tru,
						cap_canh_bao: updatedDriver.capCanhBao,
						ly_do: `Vi phạm ${detectedType.code} #${newViolation.id}`,
					});
				}
			}
		}
	}

	return { success: true, logEntry };
};
