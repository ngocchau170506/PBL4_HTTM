import express from 'express';
import jwt from 'jsonwebtoken';
import {
	handleIngestTelemetry,
	handleIngestBatchTelemetry,
	handleCreateEdgeViolation,
	handleEdgeHeartbeat,
} from './iot.controller.js';
import { upload } from '../upload/upload.controller.js';

const router = express.Router();

// Middleware xác thực thiết bị IoT (Hỗ trợ cả x-device-token, JWT Bearer device_token và chế độ Dev)
const requireDeviceAuth = (req, res, next) => {
	const deviceToken =
		req.headers['x-device-token'] || req.headers.authorization?.split(' ')[1];
	const expectedToken = process.env.IOT_DEVICE_SECRET;

	// 1. Chấp nhận static token IOT_DEVICE_SECRET
	if (deviceToken && expectedToken && deviceToken === expectedToken) {
		return next();
	}

	// 2. Chấp nhận JWT device token cấp từ /api/v1/auth/device-token
	if (deviceToken) {
		try {
			const secret =
				process.env.ACCESS_JWT_SECRET ||
				'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';
			const decoded = jwt.verify(deviceToken, secret);
			if (decoded && (decoded.role === 'DEVICE' || decoded.device_id)) {
				req.device = decoded;
				return next();
			}
		} catch (err) {
			// Tiếp tục kiểm tra fallback
		}
	}

	// 3. Fallback chế độ Dev nếu chưa cấu hình IOT_DEVICE_SECRET
	if (!expectedToken && process.env.NODE_ENV !== 'production') {
		return next();
	}

	return res.status(401).json({
		success: false,
		message:
			'Thiết bị IoT không được xác thực. Vui lòng cung cấp x-device-token hoặc Bearer device_token hợp lệ.',
	});
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
