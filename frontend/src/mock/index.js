// ==============================================================================
// PBL4 FLEET IOT MANAGEMENT - MOCK SERVICE LAYER (SRS v2.0 & API Contract v1.0)
// ==============================================================================
// Lớp Mock Data Service được thiết kế theo mô hình Repository/Service Pattern.
// Khi Backend hoàn thiện, chỉ cần thay thế các hàm này bằng axiosClient tương ứng!

import mockUsers from "./mockUsers.json";
import mockDrivers from "./mockDrivers.json";
import mockVehicles from "./mockVehicles.json";
import mockShifts from "./mockShifts.json";
import mockViolations from "./mockViolations.json";
import mockBiometricProfiles from "./mockBiometricProfiles.json";
import mockScoreLogs from "./mockScoreLogs.json";
import mockAuditLogs from "./mockAuditLogs.json";
import mockRoutes from "./mockRoutes.json";
import mockViolationTypes from "./mockViolationTypes.json";
import mockRestStops from "./mockRestStops.json";
import mockAiConfig from "./mockAiConfig.json";

// Khởi tạo LocalStorage để lưu giữ trạng thái khi thao tác trên giao diện
const STORAGE_KEYS = {
  DRIVERS: "pbl4_mock_drivers",
  VEHICLES: "pbl4_mock_vehicles",
  SHIFTS: "pbl4_mock_shifts",
  VIOLATIONS: "pbl4_mock_violations",
  SCORE_LOGS: "pbl4_mock_score_logs",
  AUDIT_LOGS: "pbl4_mock_audit_logs",
  AI_CONFIG: "pbl4_mock_ai_config",
};

const getStoredData = (key, defaultData) => {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      localStorage.setItem(key, JSON.stringify(defaultData));
      return defaultData;
    }
    return JSON.parse(raw);
  } catch {
    return defaultData;
  }
};

const setStoredData = (key, data) => {
  try {
    localStorage.setItem(key, JSON.stringify(data));
  } catch (err) {
    console.error("Lỗi ghi mock storage:", err);
  }
};

// ==============================================================================
// 1. TÀI XẾ & HỒ SƠ SINH HỌC & ĐIỂM UY TÍN (SRS 3.3, 3.5, FR_2.3, FR_2.4, FR_3.3)
// ==============================================================================

export const getDrivers = () => {
  return getStoredData(STORAGE_KEYS.DRIVERS, mockDrivers);
};

export const getDriverById = (id) => {
  const drivers = getDrivers();
  return drivers.find((d) => d.id === Number(id)) || null;
};

export const getDriverBiometricProfile = (driverId) => {
  return (
    mockBiometricProfiles.find((b) => b.driver_id === Number(driverId)) || null
  );
};

export const getDriverScoreLogs = (driverId) => {
  const allLogs = getStoredData(STORAGE_KEYS.SCORE_LOGS, mockScoreLogs);
  if (!driverId) return allLogs;
  return allLogs.filter((log) => log.driver_id === Number(driverId));
};

export const updateDriverStatus = (driverId, newStatus, reason = "", adminName = "Trần Quản Trị") => {
  const drivers = getDrivers();
  const driver = drivers.find((d) => d.id === Number(driverId));
  if (!driver) throw new Error("Không tìm thấy tài xế");

  const oldStatus = driver.trang_thai_tai_xe;
  driver.trang_thai_tai_xe = newStatus;
  setStoredData(STORAGE_KEYS.DRIVERS, drivers);

  // Ghi Audit Log tự động theo FR_3.6
  createAuditLog({
    user_name: adminName,
    hanh_dong: newStatus === "LOCKED" ? "LOCK_DRIVER" : "UNLOCK_DRIVER",
    doi_tuong_loai: "driver",
    doi_tuong_id: driver.id,
    gia_tri_truoc: { trang_thai_tai_xe: oldStatus },
    gia_tri_sau: { trang_thai_tai_xe: newStatus },
    ly_do: reason,
  });

  return driver;
};

// ==============================================================================
// 2. LỊCH PHÂN CA & SMART SCHEDULING (SRS FR_2.3.2, FR_3.4, Mục 7)
// ==============================================================================

export const getShifts = () => {
  return getStoredData(STORAGE_KEYS.SHIFTS, mockShifts);
};

export const getRoutes = () => mockRoutes;

export const validateShiftConstraints = ({ driverId, startTime, endTime }) => {
  const warnings = [];
  const errors = [];
  const biometric = getDriverBiometricProfile(driverId);
  const driver = getDriverById(driverId);

  if (!driver) {
    errors.push("Tài xế không tồn tại.");
    return { valid: false, warnings, errors };
  }

  if (driver.trang_thai_tai_xe === "LOCKED") {
    errors.push("Tài xế đang bị đình chỉ (LOCKED) do điểm uy tín <= 20. Không được xếp ca!");
    return { valid: false, warnings, errors };
  }

  // 1. Kiểm tra thời lượng ca chạy
  const start = new Date(startTime);
  const end = new Date(endTime);
  const durationHours = (end - start) / (1000 * 60 * 60);

  if (biometric && biometric.thoi_gian_lai_toi_da_khuyen_nghi) {
    const maxHours = biometric.thoi_gian_lai_toi_da_khuyen_nghi / 60;
    if (durationHours > maxHours) {
      warnings.push({
        muc: "CANH_BAO",
        ly_do: `Thời lượng ca (${durationHours.toFixed(1)}h) vượt mức khuyến nghị sinh học của tài xế (${maxHours.toFixed(1)}h). Cần lý do ghi đè (override).`,
      });
    }
  }

  // 2. Kiểm tra khung giờ yếu sinh học
  const startHour = start.getHours();
  if (biometric && biometric.khung_gio_yeu_sinh_hoc) {
    const isWeakHour = biometric.khung_gio_yeu_sinh_hoc.some((slot) => {
      if (slot.includes("13:00") && startHour >= 12 && startHour <= 15) return true;
      if (slot.includes("00:00") || slot.includes("02:00")) {
        if (startHour >= 0 && startHour <= 4) return true;
      }
      return false;
    });

    if (isWeakHour) {
      warnings.push({
        muc: "LUU_Y",
        ly_do: `Ca phân công giao thoa với khung giờ yếu sinh học (${biometric.khung_gio_yeu_sinh_hoc.join(", ")}) của tài xế.`,
      });
    }
  }

  // 3. Kiểm tra khoảng nghỉ giữa 2 ca tối thiểu (10 tiếng theo SRS 422 BUSINESS_RULE_VIOLATION)
  const allShifts = getShifts();
  const driverShifts = allShifts.filter((s) => s.driver.id === Number(driverId));
  for (const s of driverShifts) {
    if (s.thoi_gian_ket_thuc_du_kien) {
      const prevEnd = new Date(s.thoi_gian_ket_thuc_du_kien);
      const diffHours = Math.abs((start - prevEnd) / (1000 * 60 * 60));
      if (diffHours < 10 && prevEnd < start) {
        errors.push(`Vi phạm an toàn: Khoảng nghỉ giữa 2 ca (${diffHours.toFixed(1)}h) chưa bảo đảm tối thiểu 10 giờ.`);
      }
    }
  }

  return {
    valid: errors.length === 0,
    warnings,
    errors,
  };
};

export const createShift = (shiftData, dispatcherName = "Lê Điều Phối") => {
  const shifts = getShifts();
  const newShift = {
    id: Date.now(),
    ...shiftData,
    trang_thai: "SCHEDULED",
    created_at: new Date().toISOString(),
  };

  shifts.unshift(newShift);
  setStoredData(STORAGE_KEYS.SHIFTS, shifts);

  createAuditLog({
    user_name: dispatcherName,
    hanh_dong: "CREATE_SHIFT",
    doi_tuong_loai: "shift",
    doi_tuong_id: newShift.id,
    gia_tri_truoc: null,
    gia_tri_sau: newShift,
    ly_do: "Lập lịch phân ca điều phối mới",
  });

  return newShift;
};

// ==============================================================================
// 3. VI PHẠM & VÒNG ĐỜI XÁC THỰC 2 PHA (SRS 3.2, 4.1, 4.2)
// ==============================================================================

export const getViolations = () => {
  return getStoredData(STORAGE_KEYS.VIOLATIONS, mockViolations);
};

export const getViolationTypes = () => mockViolationTypes;

export const reviewViolation = ({
  violationId,
  decision, // "CONFIRMED" hoặc "REJECTED"
  notes = "",
  reviewerName = "Phạm Điều Phối",
  reviewerId = 45,
}) => {
  const violations = getViolations();
  const violation = violations.find((v) => v.id === Number(violationId));
  if (!violation) throw new Error("Không tìm thấy sự kiện vi phạm.");

  const oldStatus = violation.trang_thai;
  const newStatus = decision === "CONFIRMED" ? "MANUALLY_CONFIRMED" : "MANUALLY_REJECTED";

  violation.trang_thai = newStatus;
  violation.reviewed_by = reviewerId;
  violation.reviewed_at = new Date().toISOString();
  setStoredData(STORAGE_KEYS.VIOLATIONS, violations);

  // Nếu xác nhận vi phạm -> Tự động trừ điểm uy tín theo SRS 3.3
  if (decision === "CONFIRMED") {
    const drivers = getDrivers();
    const driver = drivers.find((d) => d.id === violation.driver_id);
    if (driver) {
      const diemTruoc = driver.uy_tin;
      const diemTru = violation.diem_tru || 5;
      const diemSau = Math.max(0, diemTruoc - diemTru);

      driver.uy_tin = diemSau;
      if (diemSau <= 20) {
        driver.cap_canh_bao = "BAO_DONG_DO";
        driver.trang_thai_tai_xe = "LOCKED";
      } else if (diemSau <= 65) {
        driver.cap_canh_bao = "CANH_CAO_VANG";
        driver.trang_thai_tai_xe = "RESTRICTED";
      }
      setStoredData(STORAGE_KEYS.DRIVERS, drivers);

      // Thêm log điểm
      const scoreLogs = getStoredData(STORAGE_KEYS.SCORE_LOGS, mockScoreLogs);
      scoreLogs.unshift({
        id: Date.now(),
        driver_id: driver.id,
        driver_name: driver.ho_ten,
        loai_thay_doi: "TRU_DIEM",
        so_diem_thay_doi: -diemTru,
        diem_truoc: diemTruoc,
        diem_sau: diemSau,
        ly_do: `Xác nhận vi phạm: ${violation.mo_ta}`,
        violation_id: violation.id,
        trip_id: violation.trip_id,
        created_at: new Date().toISOString(),
      });
      setStoredData(STORAGE_KEYS.SCORE_LOGS, scoreLogs);
    }
  }

  // Ghi Audit Log theo FR_3.6
  createAuditLog({
    user_name: reviewerName,
    hanh_dong: decision === "CONFIRMED" ? "CONFIRM_VIOLATION" : "REJECT_VIOLATION",
    doi_tuong_loai: "violation",
    doi_tuong_id: violation.id,
    gia_tri_truoc: { trang_thai: oldStatus },
    gia_tri_sau: { trang_thai: newStatus },
    ly_do: notes || (decision === "CONFIRMED" ? "Duyệt vi phạm thủ công" : "Bác bỏ do nhận diện nhầm"),
  });

  return violation;
};

// ==============================================================================
// 4. BÁO CÁO THỜI GIAN THỰC & TRẠM DỪNG NGHỈ (SRS FR_2.2, FR_3.1)
// ==============================================================================

export const getFleetStatus = () => {
  return getStoredData(STORAGE_KEYS.VEHICLES, mockVehicles);
};

export const getNearestRestStop = (tripId) => {
  const found = mockRestStops.find((r) => r.trip_id === Number(tripId));
  if (found) return found.tram_nghi;
  return mockRestStops[0]?.tram_nghi || null;
};

// ==============================================================================
// 5. QUẢN TRỊ & NHẬT KÝ KIỂM TOÁN (SRS FR_2.1.1, FR_3.6, Mục 11)
// ==============================================================================

export const getAuditLogs = () => {
  return getStoredData(STORAGE_KEYS.AUDIT_LOGS, mockAuditLogs);
};

export const createAuditLog = ({
  user_name = "Điều Phối Viên",
  user_id = 45,
  hanh_dong,
  doi_tuong_loai,
  doi_tuong_id,
  gia_tri_truoc = null,
  gia_tri_sau = null,
  ly_do = "",
}) => {
  const logs = getStoredData(STORAGE_KEYS.AUDIT_LOGS, mockAuditLogs);
  const newLog = {
    id: Date.now(),
    user_id,
    user_name,
    hanh_dong,
    doi_tuong_loai,
    doi_tuong_id,
    gia_tri_truoc,
    gia_tri_sau,
    ly_do,
    created_at: new Date().toISOString(),
  };
  logs.unshift(newLog);
  setStoredData(STORAGE_KEYS.AUDIT_LOGS, logs);
  return newLog;
};

export const getAiConfig = () => {
  return getStoredData(STORAGE_KEYS.AI_CONFIG, mockAiConfig);
};

export const updateAiConfig = (newConfig, adminName = "Trần Quản Trị") => {
  const oldConfig = getAiConfig();
  const updated = {
    ...oldConfig,
    ...newConfig,
    updated_at: new Date().toISOString(),
    updated_by: adminName,
  };
  setStoredData(STORAGE_KEYS.AI_CONFIG, updated);

  createAuditLog({
    user_name: adminName,
    hanh_dong: "UPDATE_AI_THRESHOLD",
    doi_tuong_loai: "config",
    doi_tuong_id: 1,
    gia_tri_truoc: oldConfig,
    gia_tri_sau: updated,
    ly_do: "Điều chỉnh ngưỡng nhận diện AI theo chính sách an toàn mới",
  });

  return updated;
};

// Export toàn bộ mock raw nếu UI cần import trực tiếp
export {
  mockUsers,
  mockDrivers,
  mockVehicles,
  mockShifts,
  mockViolations,
  mockBiometricProfiles,
  mockScoreLogs,
  mockAuditLogs,
  mockRoutes,
  mockViolationTypes,
  mockRestStops,
  mockAiConfig,
};
