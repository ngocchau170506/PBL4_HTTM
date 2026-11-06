import http from 'http';
import jwt from 'jsonwebtoken';
import { Server as SocketIOServer } from 'socket.io';
import app from './app.js';
import { setSocketIO } from './modules/iot/iot.service.js';

const PORT = process.env.PORT || 3000;
const JWT_SECRET =
	process.env.ACCESS_JWT_SECRET ||
	'pbl4_access_token_secret_key_2026_super_secure_node_jwt_auth_key';

const server = http.createServer(app);

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
		...envOrigins,
	]),
];

// Khởi tạo Socket.io Server cho Bản đồ trực tuyến & Cảnh báo thời gian thực
const io = new SocketIOServer(server, {
	cors: {
		origin: ALLOWED_ORIGINS,
		methods: ['GET', 'POST', 'PATCH', 'PUT', 'DELETE'],
		credentials: true,
	},
});

setSocketIO(io);

// Middleware đọc JWT (không chặn kết nối nếu dev test không truyền token)
io.use((socket, next) => {
	const token =
		socket.handshake.auth?.token ||
		socket.handshake.headers?.authorization?.split(' ')[1];

	if (token && token !== 'mock-jwt-token-vietnam-2026') {
		try {
			const decoded = jwt.verify(token, JWT_SECRET);
			socket.user = decoded;
		} catch (err) {
			// Vẫn cho phép kết nối trong môi trường dev/demo
			socket.user = null;
		}
	}
	next();
});

io.on('connection', (socket) => {
	// Tự động join vào room đội xe
	const fleetRoom = `fleet_${socket.user?.fleet_id || 1}`;
	socket.join(fleetRoom);

	if (socket.user?.driverId || socket.user?.id) {
		const driverRoom = `driver_${socket.user.driverId || socket.user.id}`;
		socket.join(driverRoom);
	}

	console.log(`🔌 Client Socket Connected: ${socket.id} | Joined: ${fleetRoom}`);

	socket.on('disconnect', () => {
		console.log(`❌ Client Socket Disconnected: ${socket.id}`);
	});
});

server.listen(PORT, () => {
	console.log('==================================================');
	console.log(`🚀 PBL4 FLEET BACKEND SERVER RUNNING ON PORT ${PORT}`);
	console.log(`📡 WebSocket / Socket.io Live Tracking Ready`);
	console.log(`🔗 API Base URL: http://localhost:${PORT}/api/v1`);
	console.log(`📖 Swagger UI:   http://localhost:${PORT}/api/docs`);
	console.log('==================================================');
});
