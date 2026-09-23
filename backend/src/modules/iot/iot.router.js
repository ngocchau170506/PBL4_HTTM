import express from 'express';
import { handleIngestTelemetry } from './iot.controller.js';

const router = express.Router();

// Public / API Key protected endpoint for IoT Hardware & Simulator Telemetry packets
router.post('/telemetry', handleIngestTelemetry);

export default router;
