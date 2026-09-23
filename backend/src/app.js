import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import cookieParser from 'cookie-parser';
import path from 'path';

import authRouter from './modules/auth/auth.router.js';
import assetsRouter from './modules/assets/assets.router.js';
import tripsRouter from './modules/trips/trips.router.js';
import iotRouter from './modules/iot/iot.router.js';
import violationsRouter from './modules/violations/violations.router.js';
import uploadRouter from './modules/upload/upload.router.js';

const app = express();

app.set('trust proxy', 1);

// Health Check
app.get('/health', (req, res) => res.json({ status: 'ok', system: 'PBL4 Fleet IoT Management', uptime: process.uptime() }));
app.get('/api/health', (req, res) => res.json({ status: 'ok', system: 'PBL4 Fleet IoT Management', uptime: process.uptime() }));

// Helmet Security
app.use(helmet({
  crossOriginResourcePolicy: { policy: "cross-origin" }
}));

// CORS Configuration
const ALLOWED_ORIGINS = (process.env.CORS_ORIGINS || 'http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173')
  .split(',')
  .map((o) => o.trim());

app.use(
  cors({
    origin: (origin, callback) => {
      if (!origin || ALLOWED_ORIGINS.includes(origin)) {
        callback(null, true);
      } else {
        callback(null, true); // Permissive for local dev & mobile apps
      }
    },
    credentials: true,
  })
);

// Rate Limiter
const globalLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 2000,
  standardHeaders: true,
  legacyHeaders: false,
  message: { message: 'Quá nhiều yêu cầu, vui lòng thử lại sau.' },
});

app.use(globalLimiter);

// Body Parsing
app.use(express.json());
app.use(cookieParser());

// Static File Serving (Uploaded Videos)
app.use('/uploads', express.static(path.join(process.cwd(), 'uploads')));

// Routes Registration
app.use('/api/auth', authRouter);
app.use('/api/assets', assetsRouter);
app.use('/api/trips', tripsRouter);
app.use('/api/iot', iotRouter);
app.use('/api/violations', violationsRouter);
app.use('/api/upload', uploadRouter);

// Global Error Handler
app.use((err, req, res, next) => {
  console.error('❌ Global Server Error:', err);
  res.status(err.status || 500).json({
    message: err.message || 'Lỗi hệ thống nội bộ.',
  });
});

export default app;
