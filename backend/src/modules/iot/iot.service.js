import prisma from '../../common/prisma.js';
import { createViolationRecord } from '../violations/violations.service.js';
import { getAiConfig } from '../configs/configs.service.js';
import { resolveViolationFromFlag } from '../../common/constants/violationTypes.js';

let ioInstance = null;

export const setSocketIO = (io) => {
  ioInstance = io;
};

export const processIotDataPacket = async ({ tripId, deviceId, kinhDo, viDo, tocDo, co2Level, aiViolationFlag }) => {
  let parsedTripId = tripId ? parseInt(tripId, 10) : null;
  const parsedDeviceId = parseInt(deviceId, 10);
  const lat = parseFloat(viDo);
  const lng = parseFloat(kinhDo);
  const speed = parseFloat(tocDo);
  const co2 = co2Level !== undefined ? parseFloat(co2Level) : null;

  // 1. Auto-lookup active IN_PROGRESS trip if tripId is omitted by hardware
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

  // 2. Log Telemetry (GPS + MQ-135 CO2 Level) to Database
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

  // 3. Fetch Trip & Driver info for live tracking and auto-trigger
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

  const isDangerFlag = Boolean(aiViolationFlag) || (co2 !== null && co2 > 1000);

  // Broadcast Real-time Telemetry event via Socket.io (hỗ trợ cả gps_update và telemetry:update cho Frontend Zustand store)
  if (ioInstance) {
    const telemetryPayload = {
      logId: logEntry.id,
      tripId: parsedTripId,
      trip_id: parsedTripId,
      deviceId: parsedDeviceId,
      vehicle_id: tripInfo?.vehicle?.id || parsedDeviceId,
      bien_so_xe: tripInfo?.vehicle?.bienSo || 'Xe IoT',
      active_driver_name: tripInfo?.driver?.user?.hoTen || 'Tài xế',
      lat,
      lng,
      vi_do_gps: lat,
      kinh_do_gps: lng,
      speed,
      toc_do_kmh: speed,
      co2Level: co2,
      co2_ppm: co2 || 450,
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

  // 4. AUTO DEDUCT POINTS & AI SAFETY TRIGGER LOGIC
  if (tripInfo && tripInfo.driver) {
    const driverId = tripInfo.driverId;
    let videoUrl = 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4';

    const config = await getAiConfig();
    const co2Threshold = config.co2_warning_threshold_ppm || 1000;

    // Chuẩn hóa loại lỗi thông qua bộ từ điển VIOLATION_TYPES
    const detectedType = resolveViolationFromFlag({
      aiViolationFlag,
      co2,
      speed,
      co2Threshold,
    });

    // Process violation if triggered with anti-spam check window (45s)
    if (detectedType) {
      const recentViolation = await prisma.violation.findFirst({
        where: {
          driverId,
          tripId: parsedTripId,
          loaiViPham: detectedType.code,
          thoiGianViPham: {
            gte: new Date(Date.now() - 45 * 1000), // anti-spam window 45s
          },
        },
      });

      if (!recentViolation) {
        console.log(`🚨 [AI/IOT TRIGGER] [ID ${detectedType.id}] ${detectedType.code} (${detectedType.name}) - Vehicle ${tripInfo.vehicle.bienSo} - Driver ${tripInfo.driver.user.hoTen}`);

        const { violation: newViolation, updatedDriver } = await createViolationRecord({
          driverId,
          tripId: parsedTripId,
          violationTypeId: detectedType.id,
          loaiViPham: detectedType.code,
          diemTru: detectedType.defaultPoints,
          videoUrl,
          videoDuration: 10,
          aiConfidenceScore: 0.95,
          moTa: `${detectedType.icon} ${detectedType.description}`,
        });

        // Broadcast violation alert event via Socket.io
        if (ioInstance) {
          const alertPayload = {
            violation: newViolation,
            driver: tripInfo.driver,
            vehicle: tripInfo.vehicle,
            updatedUyTin: updatedDriver.uyTin,
          };
          ioInstance.emit('violation_detected', alertPayload);
          ioInstance.emit('new_violation', newViolation);
        }
      }
    }
  }

  return { success: true, logEntry };
};
