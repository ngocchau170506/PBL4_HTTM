import * as auditService from './audit.service.js';
import { asyncHandler } from '../../common/middleware/index.js';

export const getAuditLogs = asyncHandler(async (req, res) => {
	const { userId, doiTuongLoai, hanhDong, limit } = req.query;
	const logs = await auditService.getAuditLogs({
		userId,
		doiTuongLoai,
		hanhDong,
		limit,
	});
	res.json({ success: true, data: logs, logs });
});
