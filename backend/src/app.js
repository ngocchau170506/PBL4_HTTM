import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import cookieParser from 'cookie-parser';
import path from 'path';
import jwt from 'jsonwebtoken';

import authRouter from './modules/auth/auth.router.js';
import assetsRouter from './modules/assets/assets.router.js';
import driversRouter from './modules/drivers/drivers.router.js';
import tripsRouter from './modules/trips/trips.router.js';
import iotRouter from './modules/iot/iot.router.js';
import violationsRouter from './modules/violations/violations.router.js';
import disputesRouter from './modules/disputes/disputes.router.js';
import monitoringRouter from './modules/monitoring/monitoring.router.js';
import configsRouter from './modules/configs/configs.router.js';
import uploadRouter from './modules/upload/upload.router.js';
import shiftsRouter from './modules/shifts/shifts.router.js';
import biometricsRouter from './modules/biometrics/biometrics.router.js';
import auditRouter from './modules/audit/audit.router.js';
import { setupSwagger } from './config/swagger.js';

const app = express();

app.set('trust proxy', 1);

// Health Check
app.get('/health', (req, res) => res.json({ status: 'ok', system: 'PBL4 Fleet IoT Management', uptime: process.uptime() }));
app.get('/api/health', (req, res) => res.json({ status: 'ok', system: 'PBL4 Fleet IoT Management', uptime: process.uptime() }));

// Helmet Security (Disable CSP to allow Swagger UI inline styles & scripts)
app.use(
  helmet({
    crossOriginResourcePolicy: { policy: 'cross-origin' },
    contentSecurityPolicy: false,
  })
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
    ...envOrigins,
  ]),
];

app.use(
  cors({
    origin: (origin, callback) => {
      if (!origin || ALLOWED_ORIGINS.includes(origin)) {
        callback(null, true);
      } else {
        console.warn(`⚠️ CORS blocked origin: ${origin}`);
        callback(new Error('CORS: Origin không được phép.'));
      }
    },
    credentials: true,
  })
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

// Body Parsing
app.use(express.json());
app.use(cookieParser());

// Static File Serving (Uploaded Videos)
app.use('/uploads', (req, res, next) => {
  const token = req.query.token || req.headers.authorization?.split(' ')[1] || req.cookies?.accessToken;
  if (!token) {
    // Trong môi trường dev nếu gọi từ frontend/video player cho phép static access
    return next();
  }
  try {
    const secret = process.env.ACCESS_JWT_SECRET || 'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';
    jwt.verify(token, secret);
    next();
  } catch (err) {
    next();
  }
}, express.static(path.join(process.cwd(), 'uploads')));

// Swagger API Documentation & Testing Interface
setupSwagger(app);

// Routes Registration (/api/...)
app.use('/api/auth', authRouter);
app.use('/api/assets', assetsRouter);
app.use('/api/drivers', driversRouter);
app.use('/api/vehicles', assetsRouter);
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

// Aliases cho /api/v1/... (nếu Frontend gọi axiosClient baseURL = .../api/v1)
const v1Router = express.Router();
v1Router.use('/auth', authRouter);
v1Router.use('/drivers', driversRouter);
v1Router.use('/vehicles', assetsRouter);
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
app.use('/api/v1', v1Router);

// Global Error Handler
app.use((err, req, res, next) => {
  console.error('❌ Global Server Error:', err);
  res.status(err.status || 500).json({
    success: false,
    message: err.message || 'Lỗi hệ thống nội bộ.',
    error: { message: err.message || 'Lỗi hệ thống nội bộ.' },
  });
});

export default app;
