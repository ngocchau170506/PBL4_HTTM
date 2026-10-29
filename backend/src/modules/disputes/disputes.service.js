import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';
import { formatDriver } from '../drivers/drivers.service.js';

export const formatDispute = (d) => {
  return {
    ...d,
    violation_id: d.violationId,
    driver_id: d.driverId,
    ma_ly_do: d.maLyDo,
    ten_ly_do: d.tenLyDo || (d.maLyDo === 'FALSE_DETECTION' ? 'AI nhận diện nhầm hành vi' : d.maLyDo === 'SENSOR_ERROR' ? 'Nhiễu cảm biến môi trường' : 'Lý do khác'),
    noi_dung_giai_trinh: d.noiDungGiaiTrinh,
    trang_thai: d.trangThai,
    khieu_nai_luc: d.khieuNaiLuc,
    resolved_by: d.resolvedBy,
    resolved_by_name: d.resolver?.hoTen || null,
    resolved_at: d.resolvedAt,
    phan_hoi: d.phanHoi,
    driver: d.driver ? formatDriver(d.driver) : null,
    violation: d.violation
      ? {
          ...d.violation,
          diem_bi_tru: d.violation.diemTru,
          video_url: d.violation.videoUrl,
          trang_thai_xac_thuc: d.violation.trangThai,
          trang_thai_diem: d.violation.trangThaiDiem,
          reviewed_by: d.violation.reviewedBy,
        }
      : null,
  };
};

export const getAllDisputes = async ({ status } = {}) => {
  const where = {};
  if (status) where.trangThai = status;

  const disputes = await prisma.dispute.findMany({
    where,
    include: {
      driver: { include: { user: true } },
      violation: true,
      resolver: true,
    },
    orderBy: { khieuNaiLuc: 'desc' },
  });

  return disputes.map(formatDispute);
};

export const createDispute = async ({ violationId, driverId, maLyDo, tenLyDo, noiDungGiaiTrinh }) => {
  const pViolationId = parseInt(violationId, 10);
  const pDriverId = parseInt(driverId, 10);

  const violation = await prisma.violation.findUnique({
    where: { id: pViolationId },
  });

  if (!violation) {
    throw new Error('Vi phạm không tồn tại để khiếu nại.');
  }

  const dispute = await prisma.dispute.create({
    data: {
      violationId: pViolationId,
      driverId: pDriverId,
      maLyDo: maLyDo || 'FALSE_DETECTION',
      tenLyDo: tenLyDo || null,
      noiDungGiaiTrinh: noiDungGiaiTrinh || '',
      trangThai: 'OPEN',
    },
    include: {
      driver: { include: { user: true } },
      violation: true,
      resolver: true,
    },
  });

  return formatDispute(dispute);
};

export const resolveDispute = async (disputeId, { decision, trang_thai, phan_hoi, responseNote }, currentUser) => {
  const id = parseInt(disputeId, 10);
  const resolvedDecision = (decision || trang_thai || '').toUpperCase();
  const note = phan_hoi || responseNote || '';

  const dispute = await prisma.dispute.findUnique({
    where: { id },
    include: {
      violation: true,
      driver: { include: { user: true } },
    },
  });

  if (!dispute) {
    throw new Error('Không tìm thấy đơn khiếu nại!');
  }

  // Four-Eyes Principle (SRS 4.1):
  // Người đã duyệt phạt ban đầu không được tự giải quyết khiếu nại (ngoại trừ ADMIN tối cao)
  const originalReviewerId = dispute.violation?.reviewedBy;
  if (
    originalReviewerId &&
    parseInt(originalReviewerId, 10) === parseInt(currentUser.id, 10) &&
    currentUser.role?.toUpperCase() !== 'ADMIN'
  ) {
    throw new Error(
      'Vi phạm nguyên tắc Four-Eyes (SRS 4.1): Người đã duyệt phạt ban đầu không được tự giải quyết khiếu nại của chính vi phạm đó!'
    );
  }

  const isApproved = resolvedDecision === 'APPROVED';
  const newStatus = isApproved ? 'APPROVED' : 'REJECTED';

  const result = await prisma.$transaction(async (tx) => {
    // 1. Cập nhật đơn khiếu nại
    const updatedDispute = await tx.dispute.update({
      where: { id },
      data: {
        trangThai: newStatus,
        resolvedBy: currentUser.id,
        resolvedAt: new Date(),
        phanHoi: note,
      },
      include: {
        driver: { include: { user: true } },
        violation: true,
        resolver: true,
      },
    });

    if (isApproved) {
      const refundPoints = dispute.violation?.diemTru || 5;

      // 2. Cập nhật vi phạm: trangThaiDiem = REFUNDED
      await tx.violation.update({
        where: { id: dispute.violationId },
        data: { trangThaiDiem: 'REFUNDED' },
      });

      // 3. Hoàn điểm cho tài xế
      const currentUyTin = dispute.driver.uyTin;
      const newUyTin = Math.min(100, currentUyTin + refundPoints);
      let newWarning = 'NORMAL';
      if (newUyTin <= 20) newWarning = 'SUSPENDED_RED';
      else if (newUyTin <= 65) newWarning = 'WARNING_YELLOW';

      await tx.driver.update({
        where: { userId: dispute.driverId },
        data: {
          uyTin: newUyTin,
          capCanhBao: newWarning,
          trangThaiTaiXe: newUyTin > 20 ? 'AVAILABLE' : dispute.driver.trangThaiTaiXe,
        },
      });

      // 4. Ghi Sổ cái biến động điểm
      await tx.driverScoreLog.create({
        data: {
          driverId: dispute.driverId,
          loaiThayDoi: 'HOAN_DIEM_KHIEU_NAI',
          soDiemThayDoi: refundPoints,
          diemTruoc: currentUyTin,
          diemSau: newUyTin,
          lyDo: `Hoàn điểm khiếu nại #${dispute.id} (Vi phạm #${dispute.violationId}): ${note}`,
          violationId: dispute.violationId,
          tripId: dispute.violation.tripId,
        },
      });
    }

    return updatedDispute;
  });

  // Log Audit Action
  logAuditAction({
    userId: currentUser.id,
    hanhDong: isApproved ? 'APPROVE_DISPUTE_REFUND' : 'REJECT_DISPUTE',
    doiTuongLoai: 'DISPUTE',
    doiTuongId: String(id),
    giaTriTruoc: { trangThai: dispute.trangThai },
    giaTriSau: { trangThai: newStatus, phanHoi: note },
    lyDo: note || (isApproved ? 'Chấp thuận khiếu nại và hoàn điểm' : 'Bác bỏ khiếu nại'),
  });

  return formatDispute(result);
};
