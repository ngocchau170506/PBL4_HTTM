import prisma from './prisma.js';

export const logAuditAction = async ({ userId, hanhDong, doiTuongLoai, doiTuongId, giaTriTruoc, giaTriSau, lyDo }) => {
  try {
    const log = await prisma.auditLog.create({
      data: {
        userId: parseInt(userId, 10),
        hanhDong,
        doiTuongLoai,
        doiTuongId: parseInt(doiTuongId, 10),
        giaTriTruoc: giaTriTruoc ? JSON.stringify(giaTriTruoc) : null,
        giaTriSau: giaTriSau ? JSON.stringify(giaTriSau) : null,
        lyDo: lyDo || null,
      },
    });
    return log;
  } catch (error) {
    console.error('❌ Failed to record Audit Log:', error.message);
    return null;
  }
};
