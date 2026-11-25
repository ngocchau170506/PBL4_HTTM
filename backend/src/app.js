import cookieParser from 'cookie-parser';
import cors from 'cors';
import express from 'express';
import rateLimit from 'express-rate-limit';
import helmet from 'helmet';
import jwt from 'jsonwebtoken';
import path from 'path';

import { setupSwagger } from './config/swagger.js';
import {
	errorHandlerMiddleware,
	notFoundMiddleware,
	requestLoggerMiddleware,
} from './common/middleware/index.js';
import logger from './common/utils/logger.js';
import assetsRouter from './modules/assets/assets.router.js';
import auditRouter from './modules/audit/audit.router.js';
import authRouter from './modules/auth/auth.router.js';
import biometricsRouter from './modules/biometrics/biometrics.router.js';
import configsRouter from './modules/configs/configs.router.js';
import disputesRouter from './modules/disputes/disputes.router.js';
import driversRouter from './modules/drivers/drivers.router.js';
import iotRouter from './modules/iot/iot.router.js';
import monitoringRouter from './modules/monitoring/monitoring.router.js';
import shiftsRouter from './modules/shifts/shifts.router.js';
import tripsRouter from './modules/trips/trips.router.js';
import uploadRouter from './modules/upload/upload.router.js';
import violationsRouter from './modules/violations/violations.router.js';

const app = express();

app.set('trust proxy', 1);

// Health Check
app.get('/health', (req, res) =>
	res.json({
		status: 'ok',
		system: 'PBL4 Fleet IoT Management',
		uptime: process.uptime(),
	}),
);
app.get('/api/health', (req, res) =>
	res.json({
		status: 'ok',
		system: 'PBL4 Fleet IoT Management',
		uptime: process.uptime(),
	}),
);

// Helmet Security (Disable CSP to allow Swagger UI inline styles & scripts)
app.use(
	helmet({
		crossOriginResourcePolicy: { policy: 'cross-origin' },
		contentSecurityPolicy: false,
	}),
);

// CORS Configuration
const PORT = process.env.PORT || 3000;
const envOrigins = (process.env.CORS_ORIGINS || '')
	.split(',')
	.map((o) => o.trim())
	.filter(Boolean);

const ALLOWED_ORIGINS = [
	...new Set([
		'http://localhost:5173',
		'http://localhost:5174',
		'http://127.0.0.1:5173',
		'http://127.0.0.1:5174',
		`http://localhost:${PORT}`,
		`http://127.0.0.1:${PORT}`,
		'http://localhost:5000',
		'http://127.0.0.1:5000',
		'http://localhost:3000',
		'http://127.0.0.1:3000',
		'http://localhost',
		'http://127.0.0.1',
		...envOrigins,
	]),
];

const PRIVATE_LAN_ORIGIN_RE =
	/^https?:\/\/(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$/i;

export const isAllowedOrigin = (origin) => {
	if (!origin) return true;
	if (ALLOWED_ORIGINS.includes('*') || ALLOWED_ORIGINS.includes(origin)) {
		return true;
	}
	if (
		process.env.ALLOW_PRIVATE_LAN_CORS !== 'false' &&
		PRIVATE_LAN_ORIGIN_RE.test(origin)
	) {
		return true;
	}
	return false;
};

app.use(
	cors({
		origin: (origin, callback) => {
			if (isAllowedOrigin(origin)) {
				callback(null, true);
			} else {
				logger.warn(`CORS blocked origin: ${origin}`);
				callback(new Error('CORS: Origin không được phép.'));
			}
		},
		credentials: true,
	}),
);

// Rate Limiter
const globalLimiter = rateLimit({
	windowMs: 15 * 60 * 1000,
	max: 3000,
	standardHeaders: true,
	legacyHeaders: false,
	message: { message: 'Quá nhiều yêu cầu, vui lòng thử lại sau.' },
});

app.use(globalLimiter);

// Body Parsing & HTTP Request Logging
app.use(express.json());
app.use(cookieParser());
app.use(requestLoggerMiddleware);

// Static File Serving (Uploaded Videos)
app.use(
	'/uploads',
	(req, res, next) => {
		const token =
			req.query.token ||
			req.headers.authorization?.split(' ')[1] ||
			req.cookies?.accessToken;
		if (!token) {
			// Trong môi trường dev nếu gọi từ frontend/video player cho phép static access
			return next();
		}
		try {
			const secret =
				process.env.ACCESS_JWT_SECRET ||
				'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';
			jwt.verify(token, secret);
			next();
		} catch (err) {
			next();
		}
	},
	express.static(path.join(process.cwd(), 'uploads')),
);

// Swagger API Documentation & Testing Interface
setupSwagger(app);

// Routes Registration (/api/...)
app.use('/api/auth', authRouter);
app.use('/api/assets', assetsRouter);
app.use('/api/drivers', driversRouter);
app.use('/api/vehicles', assetsRouter);
app.use('/api/devices', assetsRouter);
app.use('/api/routes', assetsRouter);
app.use('/api/trips', tripsRouter);
app.use('/api/iot', iotRouter);
app.use('/api/violations', violationsRouter);
app.use('/api/disputes', disputesRouter);
app.use('/api/monitoring', monitoringRouter);
app.use('/api/configs', configsRouter);
app.use('/api/audit', auditRouter);
app.use('/api/audit-logs', auditRouter);
app.use('/api/shifts', shiftsRouter);
app.use('/api/biometrics', biometricsRouter);
app.use('/api/upload', uploadRouter);
app.use('/api/edge', iotRouter);

// Aliases cho /api/v1/... (nếu Frontend gọi axiosClient baseURL = .../api/v1)
const v1Router = express.Router();
v1Router.use('/auth', authRouter);
v1Router.use('/drivers', driversRouter);
v1Router.use('/vehicles', assetsRouter);
v1Router.use('/devices', assetsRouter);
v1Router.use('/routes', assetsRouter);
v1Router.use('/assets', assetsRouter);
v1Router.use('/trips', tripsRouter);
v1Router.use('/iot', iotRouter);
v1Router.use('/violations', violationsRouter);
v1Router.use('/disputes', disputesRouter);
v1Router.use('/monitoring', monitoringRouter);
v1Router.use('/configs', configsRouter);
v1Router.use('/audit', auditRouter);
v1Router.use('/audit-logs', auditRouter);
v1Router.use('/shifts', shiftsRouter);
v1Router.use('/biometrics', biometricsRouter);
v1Router.use('/upload', uploadRouter);
v1Router.use('/edge', iotRouter);
app.use('/api/v1', v1Router);

// 404 Not Found & Centralized Error Handler Middleware
app.use(notFoundMiddleware);
app.use(errorHandlerMiddleware);

export default app;
