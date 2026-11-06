import prisma from './prisma.js';

export const logAuditAction = async ({ userId, hanhDong, doiTuongLoai, doiTuongId, giaTriTruoc, giaTriSau, lyDo, ipAddress }) => {
  try {
    const log = await prisma.auditLog.create({
      data: {
        userId: userId ? parseInt(userId, 10) : null,
        hanhDong,
        doiTuongLoai,
        doiTuongId: String(doiTuongId),
        giaTriTruoc: giaTriTruoc ? (typeof giaTriTruoc === 'string' ? giaTriTruoc : JSON.stringify(giaTriTruoc)) : null,
        giaTriSau: giaTriSau ? (typeof giaTriSau === 'string' ? giaTriSau : JSON.stringify(giaTriSau)) : null,
        lyDo: lyDo || null,
        ipAddress: ipAddress || '127.0.0.1',
      },
    });
    return log;
  } catch (error) {
    console.error('❌ Failed to record Audit Log:', error.message);
    return null;
  }
};
