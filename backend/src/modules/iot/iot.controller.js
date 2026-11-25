import * as iotService from './iot.service.js';
import { asyncHandler } from '../../common/middleware/index.js';

export const handleIngestTelemetry = asyncHandler(async (req, res) => {
	const result = await iotService.processIotDataPacket(req.body);
	return res.status(201).json({
		success: true,
		message: 'Ghi nhận telemetry thành công.',
		data: result.logEntry,
		...result,
	});
});

export const handleIngestBatchTelemetry = asyncHandler(async (req, res) => {
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
});

export const handleCreateEdgeViolation = asyncHandler(async (req, res) => {
	// Hỗ trợ trường hợp Pi 4 gửi multipart/form-data có trường 'payload' dạng JSON string
	let bodyData = { ...req.body };
	if (typeof req.body.payload === 'string') {
		try {
			bodyData = { ...bodyData, ...JSON.parse(req.body.payload) };
		} catch {
			// Bỏ qua nếu payload không phải chuỗi JSON hợp lệ
		}
	}

	const idempotencyKey =
		req.headers['idempotency-key'] ||
		bodyData.client_event_id ||
		bodyData.clientEventId;

	let uploadedVideoUrl = bodyData.video_url || bodyData.videoUrl || null;
	if (req.file) {
		const hasExplicitBackendUrl = process.env.BACKEND_URL !== undefined;
		const backendUrl = hasExplicitBackendUrl
			? process.env.BACKEND_URL.replace(/\/$/, '')
			: `http://localhost:${process.env.PORT || 3000}`;
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
});

export const handleEdgeHeartbeat = asyncHandler(async (req, res) => {
	const device = await iotService.processDeviceHeartbeat(req.body);
	return res.json({
		success: true,
		message: 'Cập nhật trạng thái sức khỏe thiết bị thành công.',
		data: device,
	});
});
