import prisma from './prisma.js';

export const logAuditAction = async ({
	userId,
	userName,
	actorType = 'USER',
	hanhDong,
	doiTuongLoai,
	doiTuongId,
	giaTriTruoc,
	giaTriSau,
	lyDo,
}) => {
	try {
		const log = await prisma.auditLog.create({
			data: {
				userId: userId ? parseInt(userId, 10) : null,
				userName: userName || undefined,
				actorType,
				hanhDong,
				doiTuongLoai: String(doiTuongLoai || 'SYSTEM'),
				doiTuongId: String(doiTuongId ?? 'UNKNOWN'),
				giaTriTruoc: giaTriTruoc
					? typeof giaTriTruoc === 'string'
						? giaTriTruoc
						: JSON.stringify(giaTriTruoc)
					: null,
				giaTriSau: giaTriSau
					? typeof giaTriSau === 'string'
						? giaTriSau
						: JSON.stringify(giaTriSau)
					: null,
				lyDo: lyDo || null,
			},
		});
		return log;
	} catch (error) {
		console.error('❌ Failed to record Audit Log:', error.message);
		return null;
	}
};
