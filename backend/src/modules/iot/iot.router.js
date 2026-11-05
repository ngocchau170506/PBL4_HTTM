import express from 'express';
import {
	handleIngestTelemetry,
	handleIngestBatchTelemetry,
	handleCreateEdgeViolation,
	handleEdgeHeartbeat,
} from './iot.controller.js';
import { upload } from '../upload/upload.controller.js';

const router = express.Router();

// Middleware xác thực thiết bị IoT (Hỗ trợ cả x-device-token, Bearer device_token và chế độ Dev)
const requireDeviceAuth = (req, res, next) => {
	const deviceToken =
		req.headers['x-device-token'] || req.headers.authorization?.split(' ')[1];
	const expectedToken = process.env.IOT_DEVICE_SECRET;

	if (!expectedToken) {
		if (process.env.NODE_ENV === 'production') {
			return res.status(403).json({
				success: false,
				message: 'IoT device authentication not configured.',
			});
		}
		return next();
	}

	if (!deviceToken || deviceToken !== expectedToken) {
		return res.status(401).json({
			success: false,
			message:
				'Thiết bị IoT không được xác thực. Vui lòng cung cấp x-device-token hợp lệ.',
		});
	}

	next();
};

// 1. POST /api/v1/edge/telemetry (và /api/v1/iot/telemetry)
router.post('/telemetry', requireDeviceAuth, handleIngestTelemetry);

// 2. POST /api/v1/edge/telemetry/batch (P2 - Gửi bù dữ liệu khi xe đi qua vùng mất sóng)
router.post('/telemetry/batch', requireDeviceAuth, handleIngestBatchTelemetry);

// 3. POST /api/v1/edge/violations (P0 - Hỗ trợ cả Multipart upload video .mp4 qua Multer & JSON)
router.post(
	'/violations',
	requireDeviceAuth,
	upload.single('video'),
	handleCreateEdgeViolation,
);

// 4. POST /api/v1/edge/heartbeat (P1 - Báo nhiệt độ CPU Pi 4, dung lượng thẻ nhớ mỗi 60s)
router.post('/heartbeat', requireDeviceAuth, handleEdgeHeartbeat);

export default router;
