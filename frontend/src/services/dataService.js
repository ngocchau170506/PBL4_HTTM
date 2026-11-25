import axiosClient from "../api/axiosClient";

export const dataService = {
  // 1. XÁC THỰC & ĐĂNG NHẬP (AUTH)
  async login(username, password) {
    return await axiosClient.post("/auth/login", { username, password });
  },

  async getCurrentUser() {
    return await axiosClient.get("/auth/me");
  },

  // 2. TÀI XẾ, HỒ SƠ SINH HỌC & ĐIỂM UY TÍN
  async getDrivers() {
    return await axiosClient.get("/drivers");
  },

  async updateDriverStatus(driverId, newStatus, reason) {
    return await axiosClient.patch(`/drivers/${driverId}/status`, {
      cap_canh_bao: newStatus,
      ly_do: reason,
    });
  },

  async getDriverScoreLogs(driverId = null) {
    return await axiosClient.get(`/drivers/${driverId}/score-logs`);
  },

  async getDriverBiometrics(driverId = null) {
    return await axiosClient.get(`/drivers/${driverId}/biometrics`);
  },

  async createDriver(driverData) {
    return await axiosClient.post("/drivers", driverData);
  },

  // 3. ĐỘI XE & THIẾT BỊ IOT EDGE
  async getVehicles() {
    return await axiosClient.get("/vehicles");
  },

  async createVehicle(vehicleData) {
    return await axiosClient.post("/vehicles", vehicleData);
  },

  async updateVehicleStatus(vehicleId, newStatus) {
    return await axiosClient.patch(`/vehicles/${vehicleId}/status`, {
      trang_thai: newStatus,
    });
  },

  // 4. TUYẾN ĐƯỜNG, CA CHẠY & CHUYẾN ĐI
  async getRoutes() {
    return await axiosClient.get("/routes");
  },

  async getShifts() {
    return await axiosClient.get("/shifts");
  },

  async createShift(shiftData) {
    return await axiosClient.post("/shifts", shiftData);
  },

  async updateShift(shiftId, updatedData) {
    return await axiosClient.patch(`/shifts/${shiftId}`, updatedData);
  },

  async deleteShift(shiftId, reason) {
    return await axiosClient.delete(`/shifts/${shiftId}`, {
      data: { ly_do: reason },
    });
  },

  async getTrips() {
    return await axiosClient.get("/trips");
  },

  // 5. THẨM ĐỊNH VI PHẠM AI
  async getViolations(params = {}) {
    return await axiosClient.get("/violations", { params });
  },

  async reviewViolation({ violationId, decision, notes }) {
    return await axiosClient.patch(`/violations/${violationId}/review`, {
      trang_thai_xac_thuc: decision,
      ghi_chu_duyet: notes,
    });
  },

  async getDisputes() {
    return await axiosClient.get("/disputes");
  },

  async resolveDispute(payload) {
    return await axiosClient.patch(`/disputes/${payload.disputeId}/resolve`, {
      trang_thai: payload.decision,
      phan_hoi: payload.responseNote,
    });
  },

  // 6. GIÁM SÁT REALTIME MAP & THỐNG KÊ DASHBOARD
  async getPriorityFleet() {
    return await axiosClient.get("/monitoring/priority-fleet");
  },

  async getMonitoringStats() {
    return await axiosClient.get("/monitoring/stats");
  },

  async getNearbyRestStops(params) {
    return await axiosClient.get("/monitoring/nearby-rest-stops", { params });
  },

  async dispatchVehicleToRestStop(payload) {
    return await axiosClient.post("/monitoring/dispatch-rest-stop", payload);
  },

  // 7. QUẢN TRỊ HỆ THỐNG (ADMIN: CẤU HÌNH AI & AUDIT LOGS)
  async getAuditLogs() {
    return await axiosClient.get("/audit-logs");
  },

  async getAiConfig() {
    return await axiosClient.get("/configs");
  },

  async updateAiConfig(newConfig) {
    return await axiosClient.put("/configs", newConfig);
  },
};

export default dataService;
