import multer from 'multer';

// Dùng memory storage để xử lý bằng sharp trước khi up lên Supabase
const storage = multer.memoryStorage();

export const uploadMiddleware = multer({
	storage,
	limits: {
		fileSize: 10 * 1024 * 1024, // 10MB limit
	},
	fileFilter: (req, file, cb) => {
		const allowedMimeTypes = ['image/jpeg', 'image/png', 'image/webp'];
		if (allowedMimeTypes.includes(file.mimetype)) {
			cb(null, true);
		} else {
			cb(new Error('Chỉ chấp nhận file ảnh định dạng JPEG, PNG, WEBP'), false);
		}
	},
});
