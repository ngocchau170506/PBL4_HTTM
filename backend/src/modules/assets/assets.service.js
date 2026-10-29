import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';

export const formatVehicle = (v) => ({
  ...v,
  bien_so_xe: v.bienSo,
  bien_so: v.bienSo,
  loai_xe: v.loaiXe,
  tai_trong_tan: v.taiTrongTan || 15.0,
  trang_thai: v.trangThai,
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
});

export const formatRoute = (r) => {
  let restStops = [];
  try {
    restStops = r.danhSachTramDungChan ? JSON.parse(r.danhSachTramDungChan) : [];
  } catch (e) {
    restStops = [];
  }
  return {
    ...r,
    ten_tuyen: r.tenTuyen,
    khoang_cach_km: r.khoangCachKm,
    thoi_gian_du_kien_phut: Math.round((r.khoangCachKm || 60) * 1.5),
    danh_sach_tram_nghi_chuan: restStops,
  };
};

// Vehicles
export const getAllVehicles = async () => {
  const vehicles = await prisma.vehicle.findMany({
    include: {
      iotDevice: true,
    },
    orderBy: { id: 'desc' },
  });
  return vehicles.map(formatVehicle);
};

export const createVehicle = async ({ bienSo, bien_so_xe, loaiXe, loai_xe, taiTrongTan, tai_trong_tan, actorUser }) => {
  const plate = bienSo || bien_so_xe;
  const model = loaiXe || loai_xe || 'Xe Tải';
  const weight = taiTrongTan || tai_trong_tan ? parseFloat(taiTrongTan || tai_trong_tan) : 15.0;

  const vehicle = await prisma.vehicle.create({
    data: {
      bienSo: plate,
      loaiXe: model,
      taiTrongTan: weight,
      trangThai: 'AVAILABLE',
      iotDevice: {
        create: {
          macAddress: `00:1A:2B:${Date.now().toString().slice(-6)}`,
          maThietBi: `IOT-${plate.replace(/[^a-zA-Z0-9]/g, '').slice(-4)}`,
          phienBan: 'v2.1-GPS-AI',
          aiModelVersion: 'yolo26n-v1.1',
          trangThaiKetNoi: 'ONLINE',
          cpuTempC: 62.0,
          freeStorageMb: 25600,
          lastHeartbeatAt: new Date(),
        },
      },
    },
    include: { iotDevice: true },
  });

  logAuditAction({
    userId: actorUser?.id || 1,
    hanhDong: 'CREATE_VEHICLE',
    doiTuongLoai: 'VEHICLE',
    doiTuongId: String(vehicle.id),
    giaTriSau: vehicle,
    lyDo: `Thêm phương tiện mới vào đội xe: ${plate}`,
  });

  return formatVehicle(vehicle);
};

export const updateVehicleStatus = async (id, status, actorUser) => {
  const pId = parseInt(id, 10);
  const oldV = await prisma.vehicle.findUnique({ where: { id: pId } });

  const updated = await prisma.vehicle.update({
    where: { id: pId },
    data: { trangThai: status },
    include: { iotDevice: true },
  });

  logAuditAction({
    userId: actorUser?.id || 1,
    hanhDong: status === 'MAINTENANCE' ? 'LOCK_VEHICLE_MAINTENANCE' : 'ACTIVATE_VEHICLE',
    doiTuongLoai: 'VEHICLE',
    doiTuongId: String(updated.bienSo || pId),
    giaTriTruoc: { trangThai: oldV?.trangThai },
    giaTriSau: { trangThai: status },
    lyDo: status === 'MAINTENANCE'
      ? `Chuyển xe ${updated.bienSo} vào xưởng bảo dưỡng (Khóa xếp ca)`
      : `Mở khóa xe ${updated.bienSo} sẵn sàng nhận ca`,
  });

  return formatVehicle(updated);
};

// IoT Devices
export const getAllIotDevices = async () => {
  return prisma.iotDevice.findMany({
    include: {
      vehicle: true,
    },
    orderBy: { id: 'desc' },
  });
};

export const createIotDevice = async ({ macAddress, phienBan, vehicleId }) => {
  return prisma.iotDevice.create({
    data: {
      macAddress,
      phienBan,
      vehicleId: vehicleId ? parseInt(vehicleId, 10) : null,
    },
  });
};

// Routes
export const getAllRoutes = async () => {
  const routes = await prisma.route.findMany({
    orderBy: { id: 'desc' },
  });
  return routes.map(formatRoute);
};

export const createRoute = async ({ tenTuyen, ten_tuyen, khoangCachKm, khoang_cach_km, danhSachTramDungChan }) => {
  const route = await prisma.route.create({
    data: {
      tenTuyen: tenTuyen || ten_tuyen,
      khoangCachKm: khoangCachKm || khoang_cach_km ? parseFloat(khoangCachKm || khoang_cach_km) : 50.0,
      danhSachTramDungChan: danhSachTramDungChan ? (typeof danhSachTramDungChan === 'string' ? danhSachTramDungChan : JSON.stringify(danhSachTramDungChan)) : null,
    },
  });
  return formatRoute(route);
};
