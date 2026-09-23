import prisma from '../../common/prisma.js';
import { sendViolationEmailAlert } from '../../common/email.service.js';

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

  // Broadcast Real-time Telemetry event (including CO2 & AI flags) via Socket.io
  if (ioInstance) {
    ioInstance.emit('gps_update', {
      logId: logEntry.id,
      tripId: parsedTripId,
      deviceId: parsedDeviceId,
      lat,
      lng,
      speed,
      co2Level: co2,
      aiViolationFlag: aiViolationFlag || null,
      timestamp: logEntry.thoiGianGhiNhan,
      vehicle: tripInfo?.vehicle || null,
      driver: tripInfo?.driver || null,
      route: tripInfo?.route || null,
    });
  }

  // 4. AUTO DEDUCT POINTS & AI SAFETY TRIGGER LOGIC
  if (tripInfo && tripInfo.driver) {
    const driverId = tripInfo.driverId;
    let loaiViPham = null;
    let diemTru = 0;
    let videoUrl = 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4';
    let moTa = '';

    // Check specific AI Violation Flags from Pi 4 (YOLO Camera) & ESP32
    if (aiViolationFlag === 'EYES_CLOSED') {
      loaiViPham = '😴 NGỦ GẬT (AI - Nhắm mắt > 2s)';
      diemTru = 10;
      moTa = `Mô hình AI YOLO trên Raspberry Pi phát hiện nhắm mắt liên tục > 2 giây. Tự động trừ ${diemTru} điểm uy tín & bật loa/rung ghế cảnh báo gắt.`;
    } else if (aiViolationFlag === 'PHONE') {
      loaiViPham = '📱 DÙNG ĐIỆN THOẠI (AI - Mất tập trung)';
      diemTru = 5;
      moTa = `Mô hình AI YOLO phát hiện cầm điện thoại khi lái xe. Tự động trừ ${diemTru} điểm uy tín.`;
    } else if (aiViolationFlag === 'YAWN') {
      loaiViPham = '🥱 NGÁP DÀI LIÊN TỤC (AI - Dấu hiệu mệt mỏi)';
      diemTru = 2;
      moTa = `Phát hiện ngáp dài liên tục > 3 giây. Bật quạt gió cabin & nhắc nhở tài xế nghỉ ngơi. Trừ ${diemTru} điểm uy tín.`;
    } else if (co2 && co2 > 1000 && !aiViolationFlag) {
      loaiViPham = `💨 NỒNG ĐỘ CO2 CAO (${co2.toFixed(0)} ppm - Nguy cơ thiếu oxy)`;
      diemTru = 0; // Cảnh báo môi trường, không trừ điểm
      moTa = `Cảm biến MQ-135 ghi nhận Nồng độ CO2 trong cabin vượt ${co2.toFixed(0)} ppm (> 1000 ppm). Tự động bật quạt gió lấy gió ngoài.`;
    } else if (speed > 80.0 && !aiViolationFlag) {
      loaiViPham = `⚡ QUÁ TỐC ĐỘ (${speed.toFixed(1)} km/h - Giới hạn 80 km/h)`;
      diemTru = 5;
      moTa = `Hệ thống GPS phát hiện tốc độ ${speed.toFixed(1)} km/h tại tọa độ (${lat.toFixed(4)}, ${lng.toFixed(4)}). Tự động trừ ${diemTru} điểm uy tín.`;
    }

    // Process violation if triggered with anti-spam check window (45s)
    if (loaiViPham) {
      const recentViolation = await prisma.violation.findFirst({
        where: {
          driverId,
          tripId: parsedTripId,
          loaiViPham: { contains: loaiViPham.slice(0, 10) },
          thoiGianViPham: {
            gte: new Date(Date.now() - 45 * 1000), // anti-spam window 45s
          },
        },
      });

      if (!recentViolation) {
        console.log(`🚨 [AI/IOT TRIGGER] ${loaiViPham} - Vehicle ${tripInfo.vehicle.bienSo} - Driver ${tripInfo.driver.user.hoTen}`);

        let updatedDriver = tripInfo.driver;
        if (diemTru > 0) {
          const currentScore = tripInfo.driver.uyTin;
          const newScore = Math.max(0, currentScore - diemTru); // Clamp score min at 0

          updatedDriver = await prisma.driver.update({
            where: { userId: driverId },
            data: { uyTin: newScore },
          });
        }

        const newViolation = await prisma.violation.create({
          data: {
            driverId,
            tripId: parsedTripId,
            loaiViPham,
            diemTru,
            videoUrl,
            videoDuration: 15,
            moTa,
          },
        });

        // Broadcast violation alert event via Socket.io
        if (ioInstance) {
          ioInstance.emit('violation_detected', {
            violation: newViolation,
            driver: tripInfo.driver,
            vehicle: tripInfo.vehicle,
            updatedUyTin: updatedDriver.uyTin,
          });
        }

        // Send Email Alert
        sendViolationEmailAlert({
          driverName: tripInfo.driver.user.hoTen,
          driverEmail: tripInfo.driver.user.username + '@gmail.com',
          bienSo: tripInfo.vehicle.bienSo,
          loaiViPham,
          diemTru,
          uyTinConLai: updatedDriver.uyTin,
          tocDo: speed.toFixed(1),
          videoUrl,
        }).catch((err) => console.error('Error sending violation email:', err));
      }
    }
  }

  return { success: true, logEntry };
};
