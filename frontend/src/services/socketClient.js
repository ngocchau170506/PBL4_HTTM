import { io } from "socket.io-client";
import toast from "react-hot-toast";

export const SOCKET_EVENTS = {
  TELEMETRY_UPDATE: "telemetry:update",
  VIOLATION_NEW: "violation:new",
  VIOLATION_UPDATED: "violation:updated",
  SCORE_CHANGED: "driver:score_changed",
  DEVICE_HEALTH: "device:health",
};

const SOCKET_URL = import.meta.env.VITE_SOCKET_URL || "http://localhost:3000";
const USE_MOCK_SOCKET = false;

class SocketService {
  socket = null;
  mockInterval = null;
  isConnected = false;
  storeActionsRef = null;

  connect(token, storeActions) {
    // Luôn cập nhật tham chiếu storeActions mới nhất
    this.storeActionsRef = storeActions;

    // Nếu đã khởi tạo instance socket rồi thì KHÔNG tạo chồng kết nối mới
    if (this.socket) {
      if (this.socket.connected) {
        storeActions.setSocketStatus("CONNECTED");
      }
      return;
    }

    if (!USE_MOCK_SOCKET) {
      this.socket = io(SOCKET_URL, {
        auth: { token },
        transports: ["websocket", "polling"], // Ưu tiên websocket trực tiếp để không bị rơi kết nối polling
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionAttempts: Infinity,
      });

      this.socket.on("connect", () => {
        this.isConnected = true;
        this.storeActionsRef?.setSocketStatus("CONNECTED");
      });

      this.socket.on("disconnect", () => {
        this.isConnected = false;
        this.storeActionsRef?.setSocketStatus("DISCONNECTED");
      });

      this.socket.on(SOCKET_EVENTS.TELEMETRY_UPDATE, (payload) => {
        this.storeActionsRef?.handleTelemetrySocket(payload);
      });

      this.socket.on(SOCKET_EVENTS.VIOLATION_NEW, (payload) => {
        this.storeActionsRef?.handleNewViolationSocket(payload);
        toast.error(
          `🚨 SOCKET ALERT: Xe ${payload.bien_so_xe} (${payload.driver_name}) - ${payload.ten_loai}!`,
          { duration: 5000 },
        );
      });

      this.socket.on(SOCKET_EVENTS.VIOLATION_UPDATED, (payload) => {
        this.storeActionsRef?.syncFleetData();
        if (payload?.type === "NEW_DISPUTE") {
          toast(
            `⚖️ Tài xế vừa gửi đơn khiếu nại mới (#KN-${payload.dispute_id})!`,
            { icon: "📩", duration: 4000 },
          );
        }
      });

      this.socket.on(SOCKET_EVENTS.SCORE_CHANGED, () => {
        this.storeActionsRef?.syncFleetData();
      });

      this.socket.on(SOCKET_EVENTS.DEVICE_HEALTH, (payload) => {
        this.storeActionsRef?.handleDeviceHealthSocket(payload);
      });
    } else {
      this.isConnected = true;
      storeActions.setSocketStatus("MOCK_LIVE");

      this.mockInterval = setInterval(() => {
        this.storeActionsRef?.tickRealtimeTelemetry();
      }, 2000);
    }
  }

  emitSimulatedViolation(storeActions) {
    const sampleEvents = [
      {
        id: Date.now(),
        violation_id: Date.now(),
        trip_id: 2,
        driver_id: 4,
        bien_so_xe: "43H-567.89",
        driver_name: "Phạm Hoàng Nam",
        ma_loai: "MICROSLEEP",
        ten_loai: "Ngủ gật (Microsleep)",
        muc_do_nghiem_trong: "CRITICAL",
        diem_bi_tru: 10,
        ai_confidence_score: 0.81,
        trang_thai_xac_thuc: "PENDING_MANUAL_REVIEW",
        video_url:
          "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4",
        thoi_gian_chot_vi_pham: new Date().toISOString(),
        mo_ta:
          "[Socket.io Realtime] Pi 4 phát hiện mắt nhắm 2.1s trên cao tốc CT01. Điểm EAR = 0.13.",
      },
      {
        id: Date.now(),
        violation_id: Date.now(),
        trip_id: 1,
        driver_id: 3,
        bien_so_xe: "43C-123.45",
        driver_name: "Lê Minh Tuấn",
        ma_loai: "PHONE_DISTRACTION",
        ten_loai: "Dùng điện thoại khi lái xe",
        muc_do_nghiem_trong: "MEDIUM",
        diem_bi_tru: 5,
        ai_confidence_score: 0.78,
        trang_thai_xac_thuc: "PENDING_MANUAL_REVIEW",
        video_url:
          "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerEscapes.mp4",
        thoi_gian_chot_vi_pham: new Date().toISOString(),
        mo_ta:
          "[Socket.io Realtime] YOLO26 phát hiện hành vi sử dụng điện thoại liên tục 3.8 giây.",
      },
    ];

    const randomEvent =
      sampleEvents[Math.floor(Math.random() * sampleEvents.length)];
    storeActions.handleNewViolationSocket(randomEvent);

    toast.error(
      `🚨 [Socket.io: violation:new] Xe ${randomEvent.bien_so_xe} (${randomEvent.driver_name}) vừa phát sinh lỗi ${randomEvent.ten_loai}!`,
      { duration: 5000 },
    );
  }

  disconnect() {
    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
    }
    if (this.mockInterval) {
      clearInterval(this.mockInterval);
      this.mockInterval = null;
    }
    this.isConnected = false;
  }
}

export const socketClient = new SocketService();
export default socketClient;
