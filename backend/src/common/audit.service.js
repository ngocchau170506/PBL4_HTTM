import prisma from './prisma.js';
import logger from './utils/logger.js';

const serializeAuditValue = (value) => {
	if (!value) return null;
	if (typeof value === 'string') return value;
	return JSON.stringify(value, (key, val) =>
		key === 'passwordHash' ? undefined : val,
	);
};

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
				giaTriTruoc: serializeAuditValue(giaTriTruoc),
				giaTriSau: serializeAuditValue(giaTriSau),
				lyDo: lyDo || null,
			},
		});
		return log;
	} catch (error) {
		logger.error('Failed to record Audit Log:', error.message);
		return null;
	}
};
