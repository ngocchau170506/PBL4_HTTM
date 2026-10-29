import * as auditService from './audit.service.js';

export const getAuditLogs = async (req, res) => {
  try {
    const { userId, doiTuongLoai, hanhDong, limit } = req.query;
    const logs = await auditService.getAuditLogs({ userId, doiTuongLoai, hanhDong, limit });
    res.json({ success: true, data: logs, logs });
  } catch (error) {
    res.status(500).json({ success: false, message: error.message });
  }
};
