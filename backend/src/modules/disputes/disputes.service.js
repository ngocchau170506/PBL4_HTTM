import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';
import {
  BadRequestException,
  ForbiddenException,
  NotFoundException,
} from '../../common/exceptions/index.js';
import { sanitizeUser } from '../../common/utils/sanitize.js';
import { formatDriver } from '../drivers/drivers.service.js';
import { getAiConfig } from '../configs/configs.service.js';

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
    resolver: d.resolver ? sanitizeUser(d.resolver) : d.resolver,
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

export const getAllDisputes = async ({ status, driverId } = {}) => {
  const where = {};
  if (status) where.trangThai = status;
  if (driverId) where.driverId = parseInt(driverId, 10);

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

export const createDispute = async (
  { violationId, driverId, maLyDo, tenLyDo, noiDungGiaiTrinh },
  currentUser = null,
) => {
  const pViolationId = parseInt(violationId, 10);
  const pDriverId = parseInt(driverId, 10);

  const violation = await prisma.violation.findUnique({
    where: { id: pViolationId },
  });

  if (!violation) {
    throw new NotFoundException('Vi phạm không tồn tại để khiếu nại.');
  }

  if (
    currentUser?.role?.toUpperCase() === 'DRIVER' &&
    Number(violation.driverId) !== Number(pDriverId)
  ) {
    throw new ForbiddenException('Bạn chỉ được gửi khiếu nại cho vi phạm của chính mình.');
  }

  if (!['CONFIRMED', 'MANUALLY_CONFIRMED'].includes(violation.trangThai)) {
    throw new BadRequestException('Chỉ được khiếu nại các vi phạm đã xác nhận trừ điểm (CONFIRMED).');
  }

  if (violation.trangThaiDiem === 'REFUNDED') {
    throw new BadRequestException('Vi phạm này đã được hoàn điểm trước đó.');
  }

  const config = await getAiConfig();
  const disputeWindowHours = Number(config.dispute_window_hours ?? 48);
  const finalizedAt = new Date(
    violation.thoiGianChotViPham || violation.thoiGianViPham || violation.createdAt,
  );
  if (!Number.isNaN(finalizedAt.getTime())) {
    const elapsedHours = (Date.now() - finalizedAt.getTime()) / 3600000;
    if (elapsedHours > disputeWindowHours) {
      throw new BadRequestException(
        `Đã quá thời hạn khiếu nại (${disputeWindowHours} giờ) kể từ khi chốt vi phạm.`,
      );
    }
  }

  const existingDispute = await prisma.dispute.findFirst({
    where: { violationId: pViolationId },
  });
  if (existingDispute) {
    throw new BadRequestException('Vi phạm này đã được gửi khiếu nại trước đó (Mỗi vi phạm chỉ được khiếu nại 1 lần).');
  }

  const dispute = await prisma.dispute.create({
    data: {
      violationId: pViolationId,
      driverId: violation.driverId,
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
    throw new NotFoundException('Không tìm thấy đơn khiếu nại!');
  }

  if (dispute.trangThai !== 'OPEN') {
    throw new BadRequestException(`Đơn khiếu nại #${id} đã được xử lý trước đó (Trạng thái: ${dispute.trangThai})!`);
  }

  // Four-Eyes Principle (SRS 4.1):
  // Người đã duyệt phạt ban đầu không được tự giải quyết khiếu nại (ngoại trừ ADMIN tối cao)
  const originalReviewerId = dispute.violation?.reviewedBy;
  if (
    originalReviewerId &&
    parseInt(originalReviewerId, 10) === parseInt(currentUser.id, 10) &&
    currentUser.role?.toUpperCase() !== 'ADMIN'
  ) {
    throw new ForbiddenException(
      'Vi phạm nguyên tắc Four-Eyes (SRS 4.1): Người đã duyệt phạt ban đầu không được tự giải quyết khiếu nại của chính vi phạm đó!'
    );
  }

  const isApproved = resolvedDecision === 'APPROVED';
  if (isApproved && dispute.violation?.trangThaiDiem === 'REFUNDED') {
    throw new BadRequestException('Vi phạm này đã được hoàn điểm trước đó.');
  }

  const newStatus = isApproved ? 'APPROVED' : 'REJECTED';
  const refundPoints = Number(dispute.violation?.diemTru ?? 0);
  const currentUyTin = dispute.driver.uyTin;
  const newUyTin = isApproved ? Math.min(100, currentUyTin + refundPoints) : currentUyTin;

  let newWarning = 'NORMAL';
  if (newUyTin <= 20) newWarning = 'SUSPENDED_RED';
  else if (newUyTin <= 65) newWarning = 'WARNING_YELLOW';

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
      // 2. Cập nhật vi phạm: trangThaiDiem = REFUNDED
      await tx.violation.update({
        where: { id: dispute.violationId },
        data: { trangThaiDiem: 'REFUNDED' },
      });

      // 3. Hoàn điểm cho tài xế (giữ nguyên ON_TRIP nếu đang chạy xe)
      await tx.driver.update({
        where: { userId: dispute.driverId },
        data: {
          uyTin: newUyTin,
          capCanhBao: newWarning,
          trangThaiTaiXe:
            dispute.driver.trangThaiTaiXe === 'ON_TRIP'
              ? 'ON_TRIP'
              : newUyTin > 20
                ? 'AVAILABLE'
                : dispute.driver.trangThaiTaiXe,
        },
      });

      // 4. Ghi Sổ cái biến động điểm nếu có hoàn > 0 điểm
      if (refundPoints > 0) {
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

  return {
    ...formatDispute(result),
    driver_id: dispute.driverId,
    diem_truoc: currentUyTin,
    diem_sau: newUyTin,
    so_diem_thay_doi: isApproved ? refundPoints : 0,
    cap_canh_bao: newWarning,
    is_approved: isApproved,
  };
};
