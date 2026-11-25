import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';
import { NotFoundException } from '../../common/exceptions/index.js';
import { sanitizeUser } from '../../common/utils/sanitize.js';
import bcrypt from 'bcrypt';

// Helper serialize driver for frontend compatibility (both snake_case and camelCase)
export const formatDriver = (d) => {
  const score = d.uyTin ?? 100;
  let computedWarning = d.capCanhBao || 'NORMAL';
  if (score <= 20) computedWarning = 'SUSPENDED_RED';
  else if (score <= 65 && computedWarning === 'NORMAL') computedWarning = 'WARNING_YELLOW';

  const safeUser = sanitizeUser(d.user);

  return {
    ...d,
    id: d.userId,
    user_id: d.userId,
    so_bang_lai: d.bangLai,
    hang_bang_lai: d.hangBangLai || 'FC',
    diem_uy_tin: score,
    cap_canh_bao: computedWarning,
    trang_thai_lai_xe: d.trangThaiTaiXe,
    user: safeUser
      ? {
          ...safeUser,
          ho_ten: safeUser.hoTen,
          so_dien_thoai: safeUser.sdt,
          vai_tro: safeUser.role,
          trang_thai: safeUser.trangThai,
        }
      : null,
  };
};

export const getAllDrivers = async () => {
  const drivers = await prisma.driver.findMany({
    include: {
      user: true,
      biometricProfiles: { take: 1, orderBy: { createdAt: 'desc' } },
    },
    orderBy: { uyTin: 'desc' },
  });

  return drivers.map(formatDriver);
};

export const getDriverById = async (driverId) => {
  const id = parseInt(driverId, 10);
  const driver = await prisma.driver.findUnique({
    where: { userId: id },
    include: {
      user: true,
      biometricProfiles: { take: 1, orderBy: { createdAt: 'desc' } },
      scoreLogs: { orderBy: { createdAt: 'desc' }, take: 20 },
      violations: { orderBy: { thoiGianViPham: 'desc' }, take: 10 },
    },
  });

  if (!driver) return null;
  return formatDriver(driver);
};

export const updateDriverStatus = async (driverId, { cap_canh_bao, ly_do }, actorUser) => {
  const id = parseInt(driverId, 10);
  const driver = await prisma.driver.findUnique({
    where: { userId: id },
    include: { user: true },
  });

  if (!driver) {
    throw new NotFoundException('Tài xế không tồn tại trong hệ thống.');
  }

  const oldWarning = driver.capCanhBao;
  const oldScore = driver.uyTin;
  const newStatus = cap_canh_bao;

  let updateData = { capCanhBao: newStatus };
  if (newStatus === 'SUSPENDED_RED') {
    updateData.uyTin = Math.min(driver.uyTin, 15);
    updateData.trangThaiTaiXe = 'OFF_DUTY';
  } else if (newStatus === 'NORMAL') {
    if (driver.uyTin <= 20) {
      updateData.uyTin = 85;
    }
    updateData.trangThaiTaiXe = 'AVAILABLE';
  }

  const targetScore = updateData.uyTin ?? oldScore;
  const scoreDiff = targetScore - oldScore;
  const scoreReason =
    ly_do ||
    (newStatus === 'SUSPENDED_RED'
      ? 'Quản trị viên khóa khẩn cấp tài khoản tài xế'
      : 'Quản trị viên mở khóa và khôi phục điểm uy tín tài xế');

  const updatedDriver = await prisma.$transaction(async (tx) => {
    const savedDriver = await tx.driver.update({
      where: { userId: id },
      data: updateData,
      include: { user: true },
    });

    if (scoreDiff !== 0) {
      await tx.driverScoreLog.create({
        data: {
          driverId: id,
          loaiThayDoi: scoreDiff > 0 ? 'CONG_DIEM' : 'TRU_DIEM',
          soDiemThayDoi: scoreDiff,
          diemTruoc: oldScore,
          diemSau: savedDriver.uyTin,
          lyDo: scoreReason,
        },
      });
    }

    return savedDriver;
  });

  logAuditAction({
    userId: actorUser?.id || 1,
    hanhDong: newStatus === 'SUSPENDED_RED' ? 'SUSPEND_DRIVER' : 'UNLOCK_DRIVER',
    doiTuongLoai: 'DRIVER',
    doiTuongId: String(id),
    giaTriTruoc: { cap_canh_bao: oldWarning, diem_uy_tin: oldScore },
    giaTriSau: { cap_canh_bao: newStatus, diem_uy_tin: updatedDriver.uyTin },
    lyDo: ly_do || 'Cập nhật trạng thái tài xế từ bảng điều khiển',
  });

  return {
    ...formatDriver(updatedDriver),
    scoreChange:
      scoreDiff !== 0
        ? {
            driver_id: id,
            diem_truoc: oldScore,
            diem_sau: updatedDriver.uyTin,
            so_diem_thay_doi: scoreDiff,
            cap_canh_bao: updatedDriver.capCanhBao,
            ly_do: scoreReason,
          }
        : null,
  };
};

export const getDriverScoreLogs = async (driverId) => {
  const id = parseInt(driverId, 10);
  const logs = await prisma.driverScoreLog.findMany({
    where: { driverId: id },
    orderBy: { createdAt: 'desc' },
  });

  return logs.map((l) => ({
    ...l,
    driver_id: l.driverId,
    loai_thay_doi: l.loaiThayDoi,
    so_diem_thay_doi: l.soDiemThayDoi,
    diem_truoc: l.diemTruoc,
    diem_sau: l.diemSau,
    ly_do: l.lyDo,
    violation_id: l.violationId,
    trip_id: l.tripId,
    created_at: l.createdAt,
  }));
};

export const createDriverRecord = async ({
  username,
  password,
  hoTen,
  ho_ten,
  sdt,
  so_dien_thoai,
  bangLai,
  so_bang_lai,
  hangBangLai,
  hang_bang_lai,
  diem_uy_tin,
}) => {
  const finalUsername = username || `driver_${Date.now()}`;
  const finalPassword = password || 'password123';
  const finalHoTen = hoTen || ho_ten || 'Tài xế mới';
  const finalSdt = sdt || so_dien_thoai || null;
  const finalBangLai = bangLai || so_bang_lai || `BL-${Date.now().toString().slice(-6)}`;
  const finalHang = hangBangLai || hang_bang_lai || 'C';
  const initialScore = diem_uy_tin ? parseInt(diem_uy_tin, 10) : 100;

  const passwordHash = await bcrypt.hash(finalPassword, 10);

  const newUser = await prisma.user.create({
    data: {
      username: finalUsername,
      passwordHash,
      hoTen: finalHoTen,
      sdt: finalSdt,
      role: 'DRIVER',
      trangThai: 'ACTIVE',
      driver: {
        create: {
          bangLai: finalBangLai,
          hangBangLai: finalHang,
          uyTin: initialScore,
          capCanhBao: initialScore <= 20 ? 'SUSPENDED_RED' : initialScore <= 65 ? 'WARNING_YELLOW' : 'NORMAL',
          trangThaiTaiXe: 'AVAILABLE',
        },
      },
    },
    include: { driver: { include: { user: true } } },
  });

  return formatDriver(newUser.driver);
};
