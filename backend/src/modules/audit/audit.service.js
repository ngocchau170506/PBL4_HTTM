import prisma from '../../common/prisma.js';
export { logAuditAction } from '../../common/audit.service.js';

export const formatAuditLog = (a) => {
  let gTruoc = null;
  let gSau = null;
  try {
    gTruoc = a.giaTriTruoc ? (typeof a.giaTriTruoc === 'string' ? JSON.parse(a.giaTriTruoc) : a.giaTriTruoc) : null;
  } catch (e) {
    gTruoc = a.giaTriTruoc;
  }
  try {
    gSau = a.giaTriSau ? (typeof a.giaTriSau === 'string' ? JSON.parse(a.giaTriSau) : a.giaTriSau) : null;
  } catch (e) {
    gSau = a.giaTriSau;
  }

  return {
    ...a,
    actor_type: a.actorType || 'USER',
    user_id: a.userId,
    user_name: a.user?.hoTen || a.userName || 'Quản trị viên',
    hanh_dong: a.hanhDong,
    doi_tuong_loai: (a.doiTuongLoai || 'system').toLowerCase(),
    doi_tuong_id: String(a.doiTuongId),
    gia_tri_truoc: gTruoc,
    gia_tri_sau: gSau,
    ly_do: a.lyDo,
    ip_address: a.ipAddress || '127.0.0.1',
    created_at: a.createdAt,
  };
};

export const getAuditLogs = async ({ userId, doiTuongLoai, hanhDong, limit = 100 } = {}) => {
  const where = {};
  if (userId) where.userId = parseInt(userId, 10);
  if (doiTuongLoai) where.doiTuongLoai = doiTuongLoai;
  if (hanhDong) where.hanhDong = hanhDong;

  const logs = await prisma.auditLog.findMany({
    where,
    include: {
      user: true,
    },
    orderBy: { createdAt: 'desc' },
    take: parseInt(limit, 10),
  });

  return logs.map(formatAuditLog);
};
