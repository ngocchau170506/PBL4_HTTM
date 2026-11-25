import { io } from "socket.io-client";
import driverDataService from "./driverDataService";

class MobileSocketService {
  socket = null;

  connect(token, driverId, callbacks = {}) {
    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
    }

    this.socket = io(driverDataService.getSocketUrl(), {
      auth: { token },
      transports: ["websocket", "polling"],
      reconnection: true,
      reconnectionDelay: 1000,
    });

    this.socket.on("connect", () => {
      console.log("📱 Mobile Socket Connected:", this.socket.id);
    });

    this.socket.on("telemetry:update", (data) => {
      callbacks.onTelemetryUpdate?.(data);
    });

    this.socket.on("driver:score_changed", (data) => {
      if (
        !data?.driver_id ||
        Number(data.driver_id) === Number(driverId)
      ) {
        callbacks.onScoreChanged?.(data);
      }
    });

    this.socket.on("violation:new", (data) => {
      if (Number(data?.driver_id) === Number(driverId)) {
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
