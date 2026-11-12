import { io } from "socket.io-client";
import { API_CONFIG } from "../../mobileConfig";

class MobileSocketService {
  socket = null;

  connect(token, driverId, callbacks = {}) {
    if (this.socket?.connected) return;

    this.socket = io(API_CONFIG.SOCKET_URL, {
      auth: { token },
      transports: ["websocket"],
      reconnection: true,
      reconnectionDelay: 1000,
    });

    this.socket.on("connect", () => {
      console.log("📱 Mobile Socket Connected:", this.socket.id);
    });

    this.socket.on("driver:score_changed", (data) => {
      if (data.driver_id === driverId || !data.driver_id) {
        callbacks.onScoreChanged?.(data);
      }
    });

    this.socket.on("violation:new", (data) => {
      if (data.driver_id === driverId) {
        callbacks.onViolationNew?.(data);
      }
    });

    this.socket.on("violation:updated", () => {
      callbacks.onShiftUpdated?.();
    });
  }

  disconnect() {
    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
    }
  }
}

export const mobileSocket = new MobileSocketService();
export default mobileSocket;
