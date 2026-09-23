import http from 'http';
import { Server as SocketIOServer } from 'socket.io';
import app from './app.js';
import { setSocketIO } from './modules/iot/iot.service.js';

const PORT = process.env.PORT || 3000;
const server = http.createServer(app);

// Initialize Socket.io Server for Real-time Telemetry & Live Map Updates
const io = new SocketIOServer(server, {
  cors: {
    origin: '*',
    methods: ['GET', 'POST'],
  },
});

setSocketIO(io);

io.on('connection', (socket) => {
  console.log(`🔌 Client Socket Connected: ${socket.id}`);

  socket.on('disconnect', () => {
    console.log(`❌ Client Socket Disconnected: ${socket.id}`);
  });
});

server.listen(PORT, () => {
  console.log('==================================================');
  console.log(`🚀 PBL4 FLEET BACKEND SERVER RUNNING ON PORT ${PORT}`);
  console.log(`📡 WebSocket / Socket.io Live Tracking Ready`);
  console.log(`🔗 API Base URL: http://localhost:${PORT}/api`);
  console.log('==================================================');
});
