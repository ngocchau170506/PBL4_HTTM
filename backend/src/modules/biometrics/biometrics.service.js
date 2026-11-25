import prisma from '../../common/prisma.js';

export const getDriverBiometricProfile = async (driverId) => {
  const pDriverId = parseInt(driverId, 10);

  const [driver, existingProfile] = await Promise.all([
    prisma.driver.findUnique({ where: { userId: pDriverId } }),
    prisma.driverBiometricProfile.findFirst({
      where: { driverId: pDriverId },
      orderBy: { createdAt: 'desc' },
    }),
  ]);

  let profile = existingProfile;

  if (!profile) {
    // Generate default profile if none exists
    const now = new Date();
    const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    
    // Count violations in last 30 days
    const recentViolations = await prisma.violation.findMany({
      where: {
        driverId: pDriverId,
        thoiGianViPham: { gte: thirtyDaysAgo },
        trangThai: { in: ['CONFIRMED', 'MANUALLY_CONFIRMED'] },
      },
    });

    const violationCounts = {};
    recentViolations.forEach((v) => {
      violationCounts[v.loaiViPham] = (violationCounts[v.loaiViPham] || 0) + 1;
    });

    profile = await prisma.driverBiometricProfile.create({
      data: {
        driverId: pDriverId,
        khungGioYeuSinhHoc: JSON.stringify(['13:00-15:00', '01:00-04:00']),
        thoiGianLaiToiDaKhuyenNghi: 240, // 4 hours
        thoiGianLaiTrungBinhTruocMetMoi: 180, // 3 hours
        tanSuatViPham30Ngay: JSON.stringify(violationCounts),
        isDefaultProfile: true,
        kyTinhToanTu: thirtyDaysAgo,
        kyTinhToanDen: now,
      },
    });
  }

  const currentScore = driver?.uyTin ?? 100;
  const currentWarn =
    driver?.capCanhBao ||
    (currentScore <= 20
      ? 'SUSPENDED_RED'
      : currentScore <= 65
        ? 'WARNING_YELLOW'
        : 'NORMAL');
  const warnLabel =
    currentWarn === 'SUSPENDED_RED'
      ? 'Đình chỉ đỏ'
      : currentWarn === 'WARNING_YELLOW'
        ? 'Cảnh cáo vàng'
        : 'An toàn';

  let dynamicDanhGia = profile.danhGiaChung;
  if (!dynamicDanhGia) {
    dynamicDanhGia =
      currentWarn === 'WARNING_YELLOW'
        ? `Điểm uy tín ${currentScore} (${warnLabel}). Nhịp sinh học giảm rõ rệt đầu giờ chiều. Đang bị giảm 30% tần suất ca đêm.`
        : currentWarn === 'SUSPENDED_RED'
          ? `Điểm uy tín ${currentScore} (${warnLabel}). Tạm khóa phân công ca mới do điểm uy tín dưới ngưỡng an toàn.`
          : `Điểm uy tín ${currentScore} (${warnLabel}). Nhịp sinh học ổn định, khuyến nghị nghỉ giải lao 15 phút sau mỗi ${Math.round((profile.thoiGianLaiTrungBinhTruocMetMoi || 180) / 60)} giờ lái liên tục.`;
  } else {
    dynamicDanhGia = dynamicDanhGia.replace(
      /Điểm uy tín\s+\d+\s*\([^)]+\)/i,
      `Điểm uy tín ${currentScore} (${warnLabel})`,
    );
    if (currentWarn === 'NORMAL') {
      dynamicDanhGia = dynamicDanhGia.replace(
        /Đang bị giảm 30% tần suất (?:gợi ý )?ca đêm\.?/i,
        'Đủ điều kiện nhận mọi ca chạy.',
      );
    }
  }

  const weakSlots = JSON.parse(profile.khungGioYeuSinhHoc || '[]');
  const freq30d = JSON.parse(profile.tanSuatViPham30Ngay || '{}');

  return {
    ...profile,
    danhGiaChung: dynamicDanhGia,
    danh_gia_chung: dynamicDanhGia,
    khungGioYeuSinhHoc: weakSlots,
    khung_gio_yeu_sinh_hoc: weakSlots,
    thoi_gian_lai_toi_da_khuyen_nghi: profile.thoiGianLaiToiDaKhuyenNghi,
    thoi_gian_lai_trung_binh_truoc_met_moi: profile.thoiGianLaiTrungBinhTruocMetMoi,
    tanSuatViPham30Ngay: freq30d,
    tan_suat_vi_pham_30_ngay: freq30d,
  };
};

export const getDriverCalibrations = async (driverId) => {
  const pDriverId = parseInt(driverId, 10);
  return prisma.driverCalibrationData.findMany({
    where: { driverId: pDriverId },
    orderBy: { thoiGianHieuChuan: 'desc' },
  });
};

export const saveCalibrationData = async ({ driverId, tripId, shiftId, vectorDacTrung, ketQua }) => {
  return prisma.driverCalibrationData.create({
    data: {
      driverId: parseInt(driverId, 10),
      tripId: tripId ? parseInt(tripId, 10) : null,
      shiftId: shiftId ? parseInt(shiftId, 10) : null,
      vectorDacTrung: typeof vectorDacTrung === 'string' ? vectorDacTrung : JSON.stringify(vectorDacTrung || {}),
      ketQua: ketQua || 'SUCCESS',
    },
  });
};
