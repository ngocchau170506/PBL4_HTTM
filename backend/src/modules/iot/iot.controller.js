import * as iotService from './iot.service.js';

export const handleIngestTelemetry = async (req, res) => {
	try {
		const result = await iotService.processIotDataPacket(req.body);
		return res.status(201).json({
			success: true,
			message: 'Ghi nhận telemetry thành công.',
			data: result.logEntry,
			...result,
		});
	} catch (error) {
		return res.status(400).json({
			success: false,
			message: error.message,
		});
	}
};

export const handleIngestBatchTelemetry = async (req, res) => {
	try {
		const packets = Array.isArray(req.body) ? req.body : req.body.records || [];
		const results = [];
		for (const pkt of packets) {
			const r = await iotService.processIotDataPacket(pkt);
			results.push(r.logEntry);
		}
		return res.status(201).json({
			success: true,
			message: `Đồng bộ bù ${results.length} bản ghi telemetry thành công.`,
			data: { synced_count: results.length },
		});
	} catch (error) {
		return res.status(400).json({
			success: false,
			message: error.message,
		});
	}
};

export const handleCreateEdgeViolation = async (req, res) => {
	try {
		// Hỗ trợ trường hợp Pi 4 gửi multipart/form-data có trường 'payload' dạng JSON string
		let bodyData = { ...req.body };
		if (typeof req.body.payload === 'string') {
			try {
				bodyData = { ...bodyData, ...JSON.parse(req.body.payload) };
			} catch (e) {
				// Bỏ qua nếu không phải JSON
			}
		}

		const idempotencyKey =
			req.headers['idempotency-key'] ||
			bodyData.client_event_id ||
			bodyData.clientEventId;

		let uploadedVideoUrl = bodyData.video_url || bodyData.videoUrl || null;
		if (req.file) {
			const backendUrl =
				process.env.BACKEND_URL || `http://localhost:${process.env.PORT || 3000}`;
			uploadedVideoUrl = `${backendUrl}/uploads/videos/${req.file.filename}`;
		}

		const result = await iotService.processEdgeViolation({
			...bodyData,
			clientEventId: idempotencyKey,
			videoUrl: uploadedVideoUrl,
		});

		return res.status(result.isDuplicate ? 200 : 201).json({
			success: true,
			message: result.isDuplicate
				? 'Sự kiện đã được xử lý trước đó (Idempotent).'
				: 'Ghi nhận vi phạm từ Edge thành công.',
			data: result.violation,
		});
	} catch (error) {
		return res.status(400).json({
			success: false,
			message: error.message,
		});
	}
};

export const handleEdgeHeartbeat = async (req, res) => {
	try {
		const device = await iotService.processDeviceHeartbeat(req.body);
		return res.json({
			success: true,
			message: 'Cập nhật trạng thái sức khỏe thiết bị thành công.',
			data: device,
		});
	} catch (error) {
		return res.status(400).json({
			success: false,
			message: error.message,
		});
	}
};
