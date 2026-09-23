import express from 'express';
import { upload, handleUploadVideo } from './upload.controller.js';
import { requireAuth } from '../../common/auth.middleware.js';

const router = express.Router();

router.use(requireAuth);

router.post('/video', upload.single('video'), handleUploadVideo);

export default router;
