import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';
import { getAiConfig } from '../configs/configs.service.js';
import { formatDriver } from '../drivers/drivers.service.js';
import { formatVehicle, formatRoute } from '../assets/assets.service.js';

export const formatShift = (s) => {
  let parsedWarnings = [];
  try {
    parsedWarnings = s.canhBaoKhiTao ? JSON.parse(s.canhBaoKhiTao) : [];
  } catch (e) {
    parsedWarnings = [];
  }

  return {
    ...s,
    driver_id: s.driverId,
    vehicle_id: s.vehicleId,
    route_id: s.routeId,
    thoi_gian_bat_dau_du_kien: s.thoiGianBatDauDuKien,
    thoi_gian_ket_thuc_du_kien: s.thoiGianKetThucDuKien,
    trang_thai: s.trangThai,
    ly_do_override: s.lyDoOverride,
    canh_bao_khi_tao: parsedWarnings,
    driver: s.driver ? formatDriver(s.driver) : null,
    vehicle: s.vehicle ? formatVehicle(s.vehicle) : null,
    route: s.route ? formatRoute(s.route) : null,
  };
};

export const getAllShifts = async ({ driverId, status } = {}) => {
  const where = {};
  if (driverId) where.driverId = parseInt(driverId, 10);
  if (status) where.trangThai = status;

  const shifts = await prisma.shift.findMany({
    where,
    include: {
      driver: { include: { user: true } },
      vehicle: { include: { iotDevice: true } },
      route: true,
      creator: true,
      trip: true,
    },
    orderBy: { thoiGianBatDauDuKien: 'desc' },
  });

  return shifts.map(formatShift);
};

export const createShift = async ({
  driverId,
  driver_id,
  vehicleId,
  vehicle_id,
  routeId,
  route_id,
  thoiGianBatDauDuKien,
  thoi_gian_bat_dau_du_kien,
  startTime: rawStart,
  thoiGianKetThucDuKien,
  thoi_gian_ket_thuc_du_kien,
  endTime: rawEnd,
  durationHours,
  ly_do_override,
  createdBy,
}) => {
  const pDriverId = parseInt(driverId || driver_id, 10);
  const pVehicleId = parseInt(vehicleId || vehicle_id, 10);
  const pRouteId = parseInt(routeId || route_id, 10);
  const pCreatedBy = parseInt(createdBy || 1, 10);

  const startStr = thoiGianBatDauDuKien || thoi_gian_bat_dau_du_kien || rawStart;
  let endStr = thoiGianKetThucDuKien || thoi_gian_ket_thuc_du_kien || rawEnd;

  const startTime = new Date(startStr);
  let endTime = endStr ? new Date(endStr) : null;
  if (!endTime && durationHours) {
    endTime = new Date(startTime.getTime() + Number(durationHours) * 3600000);
  }

  if (isNaN(startTime.getTime()) || !endTime || isNaN(endTime.getTime())) {
    throw new Error('Thời gian ca làm việc không hợp lệ.');
  }

  if (startTime >= endTime) {
    throw new Error('Thời gian bắt đầu ca phải diễn ra trước thời gian kết thúc.');
  }

  // 1. Kiểm tra Tài xế đình chỉ
  const driver = await prisma.driver.findUnique({ where: { userId: pDriverId } });
  if (driver && (driver.uyTin <= 20 || driver.capCanhBao === 'SUSPENDED_RED')) {
    throw new Error('CHẶN: Tài xế này đang bị đình chỉ công tác (Điểm uy tín <= 20 hoặc Báo động đỏ). Không thể xếp ca.');
  }

  // 2. Kiểm tra Xe bảo dưỡng
  const vehicle = await prisma.vehicle.findUnique({ where: { id: pVehicleId } });
  if (vehicle && vehicle.trangThai === 'MAINTENANCE') {
    throw new Error('CHẶN: Phương tiện này đang trong xưởng bảo dưỡng. Không thể xếp ca.');
  }

  // 3. Lấy cấu hình hệ thống động
  const config = await getAiConfig();
  const minRestHours = config.min_rest_hours ?? 8.0;

  // 4. Kiểm tra trùng lặp ca trực của Tài xế (Driver Overlap Check)
  const overlappingDriverShift = await prisma.shift.findFirst({
    where: {
      driverId: pDriverId,
      trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
      thoiGianBatDauDuKien: { lt: endTime },
      thoiGianKetThucDuKien: { gt: startTime },
    },
  });
  if (overlappingDriverShift) {
    throw new Error('CHẶN: Tài xế này đã có một ca làm việc khác trùng với khoảng thời gian đã chọn.');
  }

  // 5. Kiểm tra trùng lặp lịch phương tiện (Vehicle Overlap Check)
  const overlappingVehicleShift = await prisma.shift.findFirst({
    where: {
      vehicleId: pVehicleId,
      trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
      thoiGianBatDauDuKien: { lt: endTime },
      thoiGianKetThucDuKien: { gt: startTime },
    },
  });
  if (overlappingVehicleShift) {
    throw new Error('CHẶN: Phương tiện này đã được xếp vào một ca làm việc khác trong khoảng thời gian đã chọn.');
  }

  // 6. Kiểm tra khoảng thời gian nghỉ tối thiểu
  const prevShift = await prisma.shift.findFirst({
    where: {
      driverId: pDriverId,
      trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
      thoiGianKetThucDuKien: { lte: startTime },
    },
    orderBy: { thoiGianKetThucDuKien: 'desc' },
  });

  if (prevShift) {
    const restHoursBefore = (startTime.getTime() - new Date(prevShift.thoiGianKetThucDuKien).getTime()) / (1000 * 3600);
    if (restHoursBefore < minRestHours) {
      throw new Error(`CHẶN: Khoảng thời gian nghỉ giữa ca trước và ca này chỉ có ${restHoursBefore.toFixed(1)}h (< ${minRestHours}h tối thiểu theo quy định). Không thể tạo ca.`);
    }
  }

  const nextShift = await prisma.shift.findFirst({
    where: {
      driverId: pDriverId,
      trangThai: { notIn: ['CANCELLED', 'DECLINED'] },
      thoiGianBatDauDuKien: { gte: endTime },
    },
    orderBy: { thoiGianBatDauDuKien: 'asc' },
  });

  if (nextShift) {
    const restHoursAfter = (new Date(nextShift.thoiGianBatDauDuKien).getTime() - endTime.getTime()) / (1000 * 3600);
    if (restHoursAfter < minRestHours) {
      throw new Error(`CHẶN: Khoảng thời gian nghỉ giữa ca này và ca sau chỉ có ${restHoursAfter.toFixed(1)}h (< ${minRestHours}h tối thiểu theo quy định). Không thể tạo ca.`);
    }
  }

  // 7. Kiểm tra Hồ sơ Sinh học (Biometric Profile Check)
  const warnings = [];
  const profile = await prisma.driverBiometricProfile.findFirst({
    where: { driverId: pDriverId },
    orderBy: { createdAt: 'desc' },
  });

  if (profile) {
    const shiftMinutes = (endTime.getTime() - startTime.getTime()) / (1000 * 60);
    if (shiftMinutes > profile.thoiGianLaiToiDaKhuyenNghi) {
      warnings.push(`CẢNH BÁO: Tổng thời gian lái ca dự kiến (${(shiftMinutes / 60).toFixed(1)}h) vượt mức khuyến nghị sinh học (${(profile.thoiGianLaiToiDaKhuyenNghi / 60).toFixed(1)}h).`);
    }

    const startHour = startTime.getHours();
    if ((startHour >= 13 && startHour <= 15) || (startHour >= 1 && startHour <= 4)) {
      warnings.push(`LƯU Ý: Ca rơi vào khung giờ yếu sinh học của tài xế (${startHour}:00h). Nhắc nhở tài xế nghỉ ngơi đầy đủ.`);
    }
  }

  const shift = await prisma.shift.create({
    data: {
      driverId: pDriverId,
      vehicleId: pVehicleId,
      routeId: pRouteId,
      thoiGianBatDauDuKien: startTime,
      thoiGianKetThucDuKien: endTime,
      trangThai: 'SCHEDULED',
      createdBy: pCreatedBy,
      lyDoOverride: ly_do_override || null,
      canhBaoKhiTao: JSON.stringify(warnings),
    },
    include: {
      driver: { include: { user: true } },
      vehicle: { include: { iotDevice: true } },
      route: true,
    },
  });

  const hasOverride = Boolean(ly_do_override?.trim());
  logAuditAction({
    userId: pCreatedBy,
    hanhDong: hasOverride ? 'CREATE_SHIFT_OVERRIDE' : 'CREATE_SHIFT',
    doiTuongLoai: 'SHIFT',
    doiTuongId: String(shift.id),
    giaTriSau: shift,
    lyDo: hasOverride
      ? `Ghi đè cảnh báo Smart Scheduling: "${ly_do_override}"`
      : warnings.length > 0 ? warnings.join(' | ') : 'Tạo ca làm việc mới.',
  });

  return formatShift(shift);
};

export const respondToShift = async (shiftId, { trangThai, trang_thai, lyDoTuChoi, ly_do_tu_choi }) => {
  const id = parseInt(shiftId, 10);
  const shift = await prisma.shift.findUnique({ where: { id } });
  if (!shift) throw new Error('Ca làm việc không tồn tại.');

  if (shift.trangThai === 'IN_PROGRESS' || shift.trangThai === 'COMPLETED') {
    throw new Error('Không thể chỉnh sửa ca làm việc khi đã bắt đầu hoặc hoàn thành.');
  }

  const newStatus = trangThai || trang_thai;
  const reason = lyDoTuChoi || ly_do_tu_choi;

  const updatedShift = await prisma.shift.update({
    where: { id },
    data: {
      trangThai: newStatus,
      lyDoTuChoi: reason || null,
    },
    include: {
      driver: { include: { user: true } },
      vehicle: true,
      route: true,
    },
  });

  return formatShift(updatedShift);
};
