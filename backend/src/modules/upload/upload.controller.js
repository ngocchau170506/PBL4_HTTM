import multer from 'multer';
import path from 'path';
import fs from 'fs';

const uploadDir = path.join(process.cwd(), 'uploads', 'videos');
if (!fs.existsSync(uploadDir)) {
  fs.mkdirSync(uploadDir, { recursive: true });
}

const storage = multer.diskStorage({
  destination: (req, file, cb) => {
    cb(null, uploadDir);
  },
  filename: (req, file, cb) => {
    const uniqueSuffix = Date.now() + '-' + Math.round(Math.random() * 1e9);
    const ext = path.extname(file.originalname) || '.mp4';
    cb(null, `evidence-${uniqueSuffix}${ext}`);
  },
});

export const upload = multer({
  storage,
  limits: { fileSize: 50 * 1024 * 1024 }, // 50MB max video size
  fileFilter: (req, file, cb) => {
    if (file.mimetype.startsWith('video/') || file.originalname.match(/\.(mp4|webm|avi|mov)$/i)) {
      cb(null, true);
    } else {
      cb(new Error('Chỉ chấp nhận file video (.mp4, .webm, .avi, .mov).'), false);
    }
  },
});

export const handleUploadVideo = (req, res) => {
  if (!req.file) {
    return res.status(400).json({ message: 'Vui lòng chọn file video để tải lên.' });
  }

  const backendUrl = process.env.BACKEND_URL || 'http://localhost:3000';
  const videoUrl = `${backendUrl}/uploads/videos/${req.file.filename}`;

  return res.status(201).json({
    message: 'Upload video bằng chứng thành công.',
    filename: req.file.filename,
    videoUrl,
    sizeBytes: req.file.size,
  });
};
