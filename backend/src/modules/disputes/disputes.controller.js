import * as disputeService from './disputes.service.js';
import { getSocketIO } from '../iot/iot.service.js';

export const handleGetDisputes = async (req, res, next) => {
	try {
		const disputes = await disputeService.getAllDisputes(req.query);
		res.json({ success: true, data: disputes });
	} catch (error) {
		next(error);
	}
};

export const handleCreateDispute = async (req, res) => {
	try {
		const dispute = await disputeService.createDispute({
			violationId: req.body.violationId || req.body.violation_id,
			driverId:
				req.body.driverId ||
				req.body.driver_id ||
				req.user.driver?.userId ||
				req.user.id,
			maLyDo: req.body.maLyDo || req.body.ma_ly_do,
			tenLyDo: req.body.tenLyDo || req.body.ten_ly_do,
			noiDungGiaiTrinh: req.body.noiDungGiaiTrinh || req.body.noi_dung_giai_trinh,
		});

		// Bắn Socket.io báo ngay cho Web Dashboard tự động gọi syncFleetData()!
		const io = getSocketIO();
		if (io) {
			io.emit('violation:updated', {
				type: 'NEW_DISPUTE',
				dispute_id: dispute.id,
				violation_id: dispute.violation_id,
				driver_id: dispute.driver_id,
				dispute,
			});
		}

		res.status(201).json({ success: true, data: dispute });
	} catch (error) {
		res.status(400).json({ success: false, message: error.message });
	}
};

export const handleResolveDispute = async (req, res) => {
	try {
		const resolved = await disputeService.resolveDispute(
			req.params.id,
			req.body,
			req.user,
		);

		const io = getSocketIO();
		if (io) {
			io.emit('violation:updated', resolved.violation || { violation_id: resolved.violation_id });
			io.emit('driver:score_changed', {
				driver_id: resolved.driver_id,
				diem_sau: resolved.diem_sau,
				diem_truoc: resolved.diem_truoc,
				so_diem_thay_doi: resolved.so_diem_thay_doi,
				cap_canh_bao: resolved.cap_canh_bao,
				ly_do: `Giải quyết khiếu nại #${resolved.id}: ${resolved.trang_thai} (${resolved.is_approved ? `+${resolved.so_diem_thay_doi}đ` : 'Không hoàn điểm'})`,
			});
		}

		res.json({ success: true, data: resolved });
	} catch (error) {
		res.status(400).json({ success: false, message: error.message });
	}
};
