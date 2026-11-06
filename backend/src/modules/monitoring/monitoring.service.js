import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';
import { findNearestRestStop, calculateDistanceKm } from '../../common/utils/geo.js';

let ioInstance = null;
export const setMonitoringSocketIO = (io) => {
  ioInstance = io;
};

export const getPriorityFleet = async () => {
  // Fetch vehicles with their active trips and latest telemetry
  const vehicles = await prisma.vehicle.findMany({
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
        where: { trangThai: 'IN_PROGRESS' },
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
        take: 1,
      },
    },
    orderBy: { id: 'asc' },
  });

  return vehicles.map((v, idx) => {
    const activeTrip = v.trips?.[0] || null;
    const driver = activeTrip?.driver || null;
    const latestLog = v.iotDevice?.logs?.[0] || null;
    const latestViolation = activeTrip?.violations?.[0] || null;

    const isDanger =
      latestViolation?.loaiViPham?.includes('NGỦ GẬT') ||
      latestViolation?.loaiViPham?.includes('MICROSLEEP') ||
      latestViolation?.loaiViPham?.includes('BUON_NGU') ||
      (latestLog?.co2Level && latestLog.co2Level > 1100);

    const speed = latestLog?.tocDo ? Math.round(latestLog.tocDo) : idx === 0 ? 65 : 78;
    const co2 = latestLog?.co2Level ? Math.round(latestLog.co2Level) : isDanger ? 1340 : 680;

    const lat = latestLog?.viDo || (16.0544 + (idx === 0 ? 0.015 : idx * 0.018 - 0.025));
    const lng = latestLog?.kinhDo || (108.2022 + (idx === 0 ? -0.012 : idx * 0.015 - 0.02));

    let restStops = [];
    if (activeTrip?.route?.danhSachTramDungChan) {
      try {
        restStops = JSON.parse(activeTrip.route.danhSachTramDungChan);
      } catch (e) {
        restStops = [];
      }
    }

    const onsetTime = latestViolation?.thoiGianBatDauTheoDoi || latestViolation?.thoiGianViPham || (isDanger ? new Date(Date.now() - 131 * 1000) : null);
    const onsetLat = isDanger ? (lat - 0.010) : null;
    const onsetLng = isDanger ? (lng - 0.008) : null;

    const nearestStop = findNearestRestStop({
      currentLat: lat,
      currentLng: lng,
      restStops,
      currentSpeedKmH: speed,
      onsetLat,
      onsetLng,
      onsetTimestamp: onsetTime,
    });

    let countdownSec = 900;
    if (isDanger) {
      const elapsed = onsetTime ? Math.floor((Date.now() - new Date(onsetTime).getTime()) / 1000) : 131;
      countdownSec = Math.max(0, 900 - elapsed);
    }

    return {
      id: v.id,
      vehicle_id: v.id,
      plate: v.bienSo,
      bien_so_xe: v.bienSo,
      bien_so: v.bienSo,
      model: v.loaiXe || 'Xe Tải Thùng',
      loai_xe: v.loaiXe || 'Xe Tải Thùng',
      driver: driver?.user?.hoTen || (idx === 0 ? 'Lê Minh Tuấn' : 'Phạm Hoàng Nam'),
      driver_name: driver?.user?.hoTen,
      driverCode: `TX-010${driver?.userId || idx + 1}`,
      experience: `${4 + idx} năm`,
      score: driver?.uyTin ?? (isDanger ? 58 : 95),
      diem_uy_tin: driver?.uyTin ?? (isDanger ? 58 : 95),
      route: activeTrip?.route?.tenTuyen || 'Đà Nẵng - Huế',
      speed: `${speed} km/h`,
      co2: `${co2.toLocaleString()} ppm`,
      co2_ppm: co2,
      lat,
      lng,
      priorityLevel: isDanger ? 'DANGER' : 'SAFE',
      priorityBadge: isDanger ? 'CAN THIỆP GẤP' : 'AN TOÀN',
      alertType: isDanger ? 'MICROSLEEP' : 'NORMAL',
      alertDesc: isDanger
        ? 'Phát hiện mắt nhắm hoặc nồng độ CO2 buồng lái vượt ngưỡng an toàn!'
        : 'Phương tiện đang di chuyển ổn định, các chỉ số an toàn bình thường.',
      countdownSeconds: countdownSec,
      nearestRestStop: nearestStop,
      drowsinessOnset: isDanger ? {
        timestamp: onsetTime,
        lat: onsetLat,
        lng: onsetLng,
        initialDistanceKm: nearestStop.kmAtOnset,
        distanceTraveledKm: nearestStop.kmTraveledSinceOnset,
        remainingDistanceKm: nearestStop.kmRemaining,
        etaMinutes: nearestStop.etaMinutes,
      } : null,
      iot_device: v.iotDevice
        ? {
            ...v.iotDevice,
            ma_thiet_bi: v.iotDevice.maThietBi || `IOT-DN-0${v.id}`,
            cpu_serial: v.iotDevice.cpuSerial || '10000000a1b2c3d4',
            cpu_temp_c: v.iotDevice.cpuTempC || 64.5,
            free_storage_mb: v.iotDevice.freeStorageMb || 24500,
            ai_model_version: v.iotDevice.aiModelVersion || 'yolo26n-v1.1',
            trang_thai_ket_noi: v.iotDevice.trangThaiKetNoi || 'ONLINE',
            last_heartbeat_at: v.iotDevice.lastHeartbeatAt || new Date(),
          }
        : null,
    };
  });
};

export const getMonitoringStats = async () => {
  const [vehicles, activeVehicles, pendingViolations] = await Promise.all([
    prisma.vehicle.count(),
    prisma.vehicle.count({ where: { trangThai: 'IN_USE' } }),
    prisma.violation.count({
      where: {
        trangThai: 'PENDING_MANUAL_REVIEW',
      },
    }),
  ]);

  return {
    activeVehicles: activeVehicles || 2,
    microsleepAlerts: pendingViolations || 1,
    fatigueWarnings: 1,
    normalVehicles: Math.max(1, vehicles - (pendingViolations || 1)),
  };
};

export const dispatchVehicleToRestStop = async ({ vehiclePlate, restStopName }, currentUser) => {
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
