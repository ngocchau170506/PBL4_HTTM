import prisma from '../../common/prisma.js';
import { sendViolationEmailAlert } from '../../common/email.service.js';
import { logAuditAction } from '../../common/audit.service.js';
import { getAiConfig } from '../configs/configs.service.js';
import { formatDriver } from '../drivers/drivers.service.js';
import {
  VIOLATION_TYPES,
  getViolationTypeById,
  getViolationTypeByCode,
  getAllViolationTypes,
} from '../../common/constants/violationTypes.js';

export { getAllViolationTypes };

export const enrichViolationRecord = (v) => {
  const type = getViolationTypeByCode(v.loaiViPham) || getViolationTypeByCode(v.maLoai);
  const code = v.maLoai || (type ? type.code : v.loaiViPham);
  const name = v.tenLoai || (type ? type.name : v.loaiViPham);
  const icon = type ? type.icon : '⚠️';

  return {
    ...v,
    violationTypeId: type ? type.id : null,
    violation_type_id: type ? type.id : null,
    typeCode: code,
    typeName: name,
    typeIcon: icon,
    ma_loai: code,
    ten_loai: name,
    diem_bi_tru: v.diemTru,
    video_url: v.videoUrl,
    video_duration: v.videoDuration,
    trang_thai_xac_thuc: v.trangThai,
    trang_thai_diem: v.trangThaiDiem || 'APPLIED',
    nguon_quyet_dinh: v.nguonQuyetDinh || 'AI',
    ghi_chu_duyet: v.ghiChuDuyet,
    ai_confidence_score: v.aiConfidenceScore,
    thoi_gian_chot_vi_pham: v.thoiGianChotViPham,
    thoi_gian_vi_pham: v.thoiGianViPham,
    driver_id: v.driverId,
    trip_id: v.tripId,
    driver: v.driver ? formatDriver(v.driver) : null,
    vehicle: v.trip?.vehicle
      ? {
          ...v.trip.vehicle,
          bien_so_xe: v.trip.vehicle.bienSo,
          loai_xe: v.trip.vehicle.loaiXe,
        }
      : null,
  };
};

export const getAllViolations = async ({ driverId, tripId, status } = {}) => {
  const where = {};
  if (driverId) where.driverId = parseInt(driverId, 10);
  if (tripId) where.tripId = parseInt(tripId, 10);
  if (status) where.trangThai = status;

  const violations = await prisma.violation.findMany({
    where,
    include: {
      driver: { include: { user: true } },
      trip: { include: { vehicle: true, route: true } },
      reviewer: true,
    },
    orderBy: { thoiGianViPham: 'desc' },
  });

  return violations.map(enrichViolationRecord);
};

export const createViolationRecord = async ({
  driverId,
  tripId,
  loaiViPham,
  violationTypeId,
  diemTru,
  videoUrl,
  videoDuration,
  aiConfidenceScore,
  moTa,
  clientEventId,
}) => {
  const parsedDriverId = parseInt(driverId, 10);
  const parsedTripId = parseInt(tripId, 10);

  const resolvedType = getViolationTypeById(violationTypeId) || getViolationTypeByCode(loaiViPham);
  const standardCode = resolvedType ? resolvedType.code : (loaiViPham || 'MICROSLEEP');
  const standardName = resolvedType ? resolvedType.name : standardCode;
  const rawPts = parseInt(
    diemTru !== undefined ? diemTru : (resolvedType?.defaultPoints ?? 10),
    10
  );
  const confidence = aiConfidenceScore !== undefined ? parseFloat(aiConfidenceScore) : 0.90;

  // Lấy cấu hình hệ thống động
  const config = await getAiConfig();
  const confirmThreshold = config.auto_confirm_min ?? 0.85;
  const rejectThreshold = config.manual_review_min ?? 0.60;
  const capPerTrip = Number(config.penalty_cap_per_trip ?? 30);
  const capPerDay = Number(config.penalty_cap_per_day ?? 50);

  let initialStatus = 'PENDING_VERIFICATION';
  let decisionSource = 'AI';
  if (confidence >= confirmThreshold) {
    initialStatus = 'CONFIRMED';
    decisionSource = 'AI';
  } else if (confidence < rejectThreshold) {
    initialStatus = 'REJECTED';
    decisionSource = 'AI';
  } else {
    initialStatus = 'PENDING_MANUAL_REVIEW';
    decisionSource = null;
  }

  // Rest stop guidance
  const tripData = await prisma.trip.findUnique({
    where: { id: parsedTripId },
    include: { route: true, vehicle: true, driver: { include: { user: true } } },
  });

  let restStopGuidance = null;
  if (tripData?.route?.danhSachTramDungChan) {
    try {
      const restStops = JSON.parse(tripData.route.danhSachTramDungChan);
      if (restStops && restStops.length > 0) {
        const nextStop = restStops[0];
        restStopGuidance = {
          tenTram: nextStop.ten,
          kmConLai: 4.2,
          phutConLai: 5,
          thongBaoGiongNoi: `Cảnh báo an toàn! Còn khoảng 4.2 km nữa sẽ đến ${nextStop.ten}. Vui lòng giảm tốc độ và dừng nghỉ!`,
        };
      }
    } catch (e) {
      console.error('Error parsing rest stops:', e);
    }
  }

  if (!restStopGuidance) {
    restStopGuidance = {
      tenTram: 'Trạm Dừng Nghỉ Nam Hải Vân (Km 22)',
      kmConLai: 4.2,
      phutConLai: 5,
      thongBaoGiongNoi: 'Cảnh báo an toàn! Còn 4.2 km nữa sẽ đến Trạm Dừng Nghỉ Nam Hải Vân.',
    };
  }

  // Dùng prisma.$transaction đảm bảo tính toàn vẹn kèm CAPPING
  const result = await prisma.$transaction(async (tx) => {
    let actualDeducted = rawPts;
    let pointStatus = initialStatus === 'CONFIRMED' ? 'APPLIED' : 'NOT_APPLIED';

    if (initialStatus === 'CONFIRMED' && rawPts > 0) {
      // Tính toán áp trần (Penalty Capping)
      const startOfDay = new Date();
      startOfDay.setHours(0, 0, 0, 0);

      const [tripLogs, dayLogs] = await Promise.all([
        tx.driverScoreLog.findMany({
          where: { driverId: parsedDriverId, tripId: parsedTripId, loaiThayDoi: 'TRU_DIEM' },
        }),
        tx.driverScoreLog.findMany({
          where: { driverId: parsedDriverId, loaiThayDoi: 'TRU_DIEM', createdAt: { gte: startOfDay } },
        }),
      ]);

      const deductedInTrip = tripLogs.reduce((sum, l) => sum + Math.abs(l.soDiemThayDoi), 0);
      const deductedInDay = dayLogs.reduce((sum, l) => sum + Math.abs(l.soDiemThayDoi), 0);

      const remainingTripQuota = Math.max(0, capPerTrip - deductedInTrip);
      const remainingDayQuota = Math.max(0, capPerDay - deductedInDay);
      const allowedQuota = Math.min(remainingTripQuota, remainingDayQuota);

      if (rawPts > allowedQuota) {
        actualDeducted = allowedQuota;
        pointStatus = 'CAPPED';
      } else {
        actualDeducted = rawPts;
        pointStatus = 'APPLIED';
      }
    }

    const violation = await tx.violation.create({
      data: {
        clientEventId: clientEventId || null,
        driverId: parsedDriverId,
        tripId: parsedTripId,
        loaiViPham: standardCode,
        maLoai: standardCode,
        tenLoai: standardName,
        diemTru: actualDeducted,
        videoUrl: videoUrl || null,
        videoDuration: videoDuration ? parseInt(videoDuration, 10) : 10,
        trangThai: initialStatus,
        trangThaiDiem: pointStatus,
        nguonQuyetDinh: decisionSource,
        aiConfidenceScore: confidence,
        tramDungChanGoiY: JSON.stringify(restStopGuidance),
        thoiGianBatDauTheoDoi: new Date(Date.now() - 30 * 1000),
        thoiGianChotViPham: new Date(),
        thoiGianViPham: new Date(),
        moTa: moTa || `YOLO26 phát hiện ${standardName} (AI Confidence: ${(confidence * 100).toFixed(0)}%).`,
      },
      include: {
        driver: { include: { user: true } },
        trip: { include: { vehicle: true, route: true } },
      },
    });

    let updatedDriver = violation.driver;
    if (initialStatus === 'CONFIRMED' && actualDeducted > 0) {
      const currentUyTin = violation.driver.uyTin;
      const newUyTin = Math.max(0, currentUyTin - actualDeducted);

      let newWarning = 'NORMAL';
      if (newUyTin <= 20) newWarning = 'SUSPENDED_RED';
      else if (newUyTin <= 65) newWarning = 'WARNING_YELLOW';

      updatedDriver = await tx.driver.update({
        where: { userId: parsedDriverId },
        data: {
          uyTin: newUyTin,
          capCanhBao: newWarning,
          trangThaiTaiXe: newUyTin <= 20 ? 'OFF_DUTY' : violation.driver.trangThaiTaiXe,
        },
        include: { user: true },
      });

      await tx.driverScoreLog.create({
        data: {
          driverId: parsedDriverId,
          loaiThayDoi: 'TRU_DIEM',
          soDiemThayDoi: -actualDeducted,
          diemTruoc: currentUyTin,
          diemSau: newUyTin,
          lyDo: pointStatus === 'CAPPED'
            ? `[Áp trần CAPPED] Vi phạm ${standardCode}: Trừ ${actualDeducted}/${rawPts} điểm`
            : `Tự động trừ điểm: ${standardName}`,
          violationId: violation.id,
          tripId: parsedTripId,
        },
      });
    }

    return { violation: enrichViolationRecord(violation), updatedDriver };
  });

  if (initialStatus === 'CONFIRMED') {
    sendViolationEmailAlert({
      driverName: result.violation.driver?.user?.hoTen || 'Tài xế',
      driverEmail: (result.violation.driver?.user?.username || 'driver') + '@gmail.com',
      bienSo: result.violation.trip?.vehicle?.bienSo || 'Xe IoT',
      loaiViPham: result.violation.ten_loai || standardCode,
      diemTru: result.violation.diemTru,
      uyTinConLai: result.updatedDriver.uyTin,
      tocDo: 'N/A',
      videoUrl: result.violation.videoUrl,
      tramDungChanGoiY: restStopGuidance,
    }).catch((e) => console.error('Error sending violation email:', e));
  }

  return result;
};

export const reviewViolationManual = async (
  violationId,
  { action, decision, lyDo, notes, ghi_chu_duyet, trang_thai_xac_thuc, reviewerId }
) => {
  const id = parseInt(violationId, 10);
  const pReviewerId = parseInt(reviewerId, 10);

  const rawDecision = (action || decision || trang_thai_xac_thuc || '').toUpperCase();
  const noteText = lyDo || notes || ghi_chu_duyet || '';

  const isConfirm = rawDecision.includes('CONFIRM');

  const violation = await prisma.violation.findUnique({
    where: { id },
    include: {
      driver: { include: { user: true } },
      trip: { include: { vehicle: true } },
    },
  });

  if (!violation) throw new Error('Bản ghi vi phạm không tồn tại.');

  const oldStatus = violation.trangThai;
  const newStatus = isConfirm ? 'CONFIRMED' : 'REJECTED';

  const config = await getAiConfig();
  const capPerTrip = Number(config.penalty_cap_per_trip ?? 30);
  const capPerDay = Number(config.penalty_cap_per_day ?? 50);

  const { updatedViolation, updatedDriver } = await prisma.$transaction(async (tx) => {
    let currentDriver = await tx.driver.findUnique({
      where: { userId: violation.driverId },
      include: { user: true },
    });

    let actualDeducted = 0;
    let pointStatus = violation.trangThaiDiem || 'APPLIED';

    if (isConfirm) {
      if (violation.trangThai !== 'CONFIRMED') {
        const rawPts = violation.diemTru || 10;
        const startOfDay = new Date();
        startOfDay.setHours(0, 0, 0, 0);

        const [tripLogs, dayLogs] = await Promise.all([
          tx.driverScoreLog.findMany({
            where: { driverId: violation.driverId, tripId: violation.tripId, loaiThayDoi: 'TRU_DIEM' },
          }),
          tx.driverScoreLog.findMany({
            where: { driverId: violation.driverId, loaiThayDoi: 'TRU_DIEM', createdAt: { gte: startOfDay } },
          }),
        ]);

        const deductedInTrip = tripLogs.reduce((sum, l) => sum + Math.abs(l.soDiemThayDoi), 0);
        const deductedInDay = dayLogs.reduce((sum, l) => sum + Math.abs(l.soDiemThayDoi), 0);

        const remainingTripQuota = Math.max(0, capPerTrip - deductedInTrip);
        const remainingDayQuota = Math.max(0, capPerDay - deductedInDay);
        const allowedQuota = Math.min(remainingTripQuota, remainingDayQuota);

        let isCapped = false;
        if (rawPts > allowedQuota) {
          actualDeducted = allowedQuota;
          isCapped = true;
          pointStatus = 'CAPPED';
        } else {
          actualDeducted = rawPts;
          pointStatus = 'APPLIED';
        }

        const currentUyTin = currentDriver.uyTin;
        const newUyTin = Math.max(0, currentUyTin - actualDeducted);
        let newWarning = 'NORMAL';
        if (newUyTin <= 20) newWarning = 'SUSPENDED_RED';
        else if (newUyTin <= 65) newWarning = 'WARNING_YELLOW';

        currentDriver = await tx.driver.update({
          where: { userId: violation.driverId },
          data: {
            uyTin: newUyTin,
            capCanhBao: newWarning,
            trangThaiTaiXe: newUyTin <= 20 ? 'OFF_DUTY' : currentDriver.trangThaiTaiXe,
          },
          include: { user: true },
        });

        await tx.driverScoreLog.create({
          data: {
            driverId: violation.driverId,
            loaiThayDoi: 'TRU_DIEM',
            soDiemThayDoi: -actualDeducted,
            diemTruoc: currentUyTin,
            diemSau: newUyTin,
            lyDo: isCapped
              ? `[Áp trần CAPPED] Vi phạm ${violation.maLoai || violation.loaiViPham}: Trừ ${actualDeducted}/${rawPts}đ`
              : `Thẩm định vi phạm thủ công: ${violation.tenLoai || violation.loaiViPham}. ${noteText}`,
            violationId: violation.id,
            tripId: violation.tripId,
          },
        });
      }
    } else {
      // Nếu REJECT: Bác bỏ vi phạm (hoàn lại điểm nếu trước đó đã bị auto-confirm trừ điểm)
      if (violation.trangThai === 'CONFIRMED' && violation.diemTru > 0) {
        const currentUyTin = currentDriver.uyTin;
        const refundedUyTin = Math.min(100, currentUyTin + violation.diemTru);
        let newWarning = 'NORMAL';
        if (refundedUyTin <= 20) newWarning = 'SUSPENDED_RED';
        else if (refundedUyTin <= 65) newWarning = 'WARNING_YELLOW';

        currentDriver = await tx.driver.update({
          where: { userId: violation.driverId },
          data: {
            uyTin: refundedUyTin,
            capCanhBao: newWarning,
          },
          include: { user: true },
        });

        await tx.driverScoreLog.create({
          data: {
            driverId: violation.driverId,
            loaiThayDoi: 'CONG_DIEM',
            soDiemThayDoi: violation.diemTru,
            diemTruoc: currentUyTin,
            diemSau: refundedUyTin,
            lyDo: `Hoàn điểm do Điều phối viên bác bỏ vi phạm (False Alarm): ${violation.loaiViPham}. ${noteText}`,
            violationId: violation.id,
            tripId: violation.tripId,
          },
        });
      }
      pointStatus = 'NOT_APPLIED';
    }

    const updatedV = await tx.violation.update({
      where: { id },
      data: {
        trangThai: newStatus,
        trangThaiDiem: pointStatus,
        nguonQuyetDinh: 'DISPATCHER',
        ghiChuDuyet: noteText,
        reviewedBy: pReviewerId,
        reviewedAt: new Date(),
        moTa: (violation.moTa || '') + ` | Thẩm định bởi Quản lý: ${newStatus}. ${noteText}`,
      },
      include: {
        driver: { include: { user: true } },
        trip: { include: { vehicle: true } },
        reviewer: true,
      },
    });

    return { updatedViolation: updatedV, updatedDriver: currentDriver };
  });

  logAuditAction({
    userId: pReviewerId,
    hanhDong: isConfirm ? 'CONFIRM_VIOLATION' : 'REJECT_VIOLATION',
    doiTuongLoai: 'VIOLATION',
    doiTuongId: String(id),
    giaTriTruoc: { trangThai: oldStatus },
    giaTriSau: { trangThai: newStatus, ghiChuDuyet: noteText },
    lyDo: noteText || `Thẩm định: ${newStatus}`,
  });

  return { violation: enrichViolationRecord(updatedViolation), updatedDriver };
};

export const getDriverRankings = async () => {
  return prisma.driver.findMany({
    include: {
      user: true,
      violations: {
        where: { trangThai: { in: ['CONFIRMED', 'MANUALLY_CONFIRMED'] } },
        orderBy: { thoiGianViPham: 'desc' },
      },
      scoreLogs: {
        orderBy: { createdAt: 'desc' },
        take: 10,
      },
    },
    orderBy: { uyTin: 'desc' },
  });
};
