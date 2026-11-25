// ============================================================================
// CẤU HÌNH KẾT NỐI BACKEND CHO MOBILE APP (REACT NATIVE / WEB MOBILE PREVIEW)
// ============================================================================
import AsyncStorage from "@react-native-async-storage/async-storage";
import { API_CONFIG } from "../../mobileConfig";

const STORAGE_KEYS = {
  SERVER_URL: "@fleetguard_server_url",
  TOKEN: "@fleetguard_auth_token",
  USER: "@fleetguard_user",
};

const normalizeServerUrl = (raw) => {
  const trimmed = String(raw || "").trim();
  if (!trimmed) return API_CONFIG.SOCKET_URL;
  let normalized = /^https?:\/\//i.test(trimmed)
    ? trimmed
    : `http://${trimmed}`;
  normalized = normalized.replace(/\/+$/, "").replace(/\/api\/v1$/i, "");
  return normalized;
};

const createEmptyDriverState = () => ({
  user: null,
  biometricProfile: null,
  currentShift: null,
  scoreLogs: [],
  violations: [],
});

let currentServerUrl = normalizeServerUrl(API_CONFIG.SOCKET_URL);
let authToken = null;
let driverState = createEmptyDriverState();

// Helper gọi API chuẩn với timeout AbortController
async function apiRequest(endpoint, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    ...(options.headers || {}),
  };

  const controller = new AbortController();
  const timeoutMs = API_CONFIG.TIMEOUT_MS || 15000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${currentServerUrl}/api/v1${endpoint}`, {
      ...options,
      headers,
      signal: controller.signal,
    });

    const contentType = response.headers.get("content-type") || "";
    const json = contentType.includes("application/json")
      ? await response.json()
      : { success: response.ok, message: await response.text() };

    if (!response.ok || json.success === false) {
      throw new Error(
        json.message || json.error?.message || "Lỗi kết nối tới máy chủ",
      );
    }
    return json.data !== undefined ? json.data : json;
  } catch (err) {
    if (err?.name === "AbortError") {
      throw new Error(
        `Kết nối máy chủ quá thời gian (${Math.round(timeoutMs / 1000)}s). Vui lòng kiểm tra lại địa chỉ Server hoặc mạng LAN.`,
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

const formatDateTimeVN = (isoStr) => {
  if (!isoStr) return "Chưa xác định";
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return String(isoStr);
    const datePart = d.toLocaleDateString("vi-VN");
    const timePart = d.toLocaleTimeString("vi-VN", {
      hour: "2-digit",
      minute: "2-digit",
    });
    return `${datePart} • ${timePart}`;
  } catch {
    return String(isoStr);
  }
};

export const driverDataService = {
  async initSession() {
    try {
      const [savedUrl, savedToken, savedUserRaw] = await Promise.all([
        AsyncStorage.getItem(STORAGE_KEYS.SERVER_URL),
        AsyncStorage.getItem(STORAGE_KEYS.TOKEN),
        AsyncStorage.getItem(STORAGE_KEYS.USER),
      ]);
      if (savedUrl) {
        currentServerUrl = normalizeServerUrl(savedUrl);
      }
      if (savedToken) {
        authToken = savedToken;
      }
      if (savedUserRaw) {
        driverState.user = JSON.parse(savedUserRaw);
      }
      return {
        hasSession: Boolean(savedToken && driverState.user),
        serverUrl: currentServerUrl,
        user: savedToken && driverState.user ? { ...driverState.user } : null,
      };
    } catch {
      return {
        hasSession: false,
        serverUrl: currentServerUrl,
        user: null,
      };
    }
  },

  getServerUrl() {
    return currentServerUrl;
  },

  getSocketUrl() {
    return currentServerUrl;
  },

  async setServerUrl(rawUrl) {
    currentServerUrl = normalizeServerUrl(rawUrl);
    await AsyncStorage.setItem(STORAGE_KEYS.SERVER_URL, currentServerUrl).catch(
      () => {},
    );
    return currentServerUrl;
  },

  resolveMediaUrl(rawUrl) {
    if (!rawUrl || typeof rawUrl !== "string") return null;
    const trimmed = rawUrl.trim();
    if (!trimmed) return null;
    const localBackendPrefix =
      /^https?:\/\/(localhost|127\.0\.0\.1):3000(\/uploads\/.+)$/i;
    const match = trimmed.match(localBackendPrefix);
    if (match) {
      return `${currentServerUrl}${match[2]}`;
    }
    if (/^https?:\/\//i.test(trimmed)) return trimmed;
    if (trimmed.startsWith("/")) return `${currentServerUrl}${trimmed}`;
    return `${currentServerUrl}/${trimmed}`;
  },

  async logout() {
    authToken = null;
    driverState = createEmptyDriverState();
    await Promise.all([
      AsyncStorage.removeItem(STORAGE_KEYS.TOKEN),
      AsyncStorage.removeItem(STORAGE_KEYS.USER),
    ]).catch(() => {});
  },

  // 1. ĐĂNG NHẬP TÀI XẾ
  async login(username, password, consentChecked) {
    if (!consentChecked) {
      throw new Error(
        "Vui lòng đồng ý chính sách bảo vệ dữ liệu sinh trắc học (NĐ 13/2023/NĐ-CP)!",
      );
    }
    if (!username.trim() || !password.trim()) {
      throw new Error("Vui lòng nhập đầy đủ tài khoản và mật khẩu!");
    }

    const res = await apiRequest("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: username.trim(), password }),
    });

    authToken = res.access_token || res.token;
    const u = res.user || {};
    const drv = u.driver || {};

    driverState = createEmptyDriverState();
    driverState.user = {
      id: u.id,
      driver_id: drv.userId || u.id,
      username: u.username,
      ho_ten: u.ho_ten || u.hoTen || "Tài xế",
      so_dien_thoai: u.so_dien_thoai || u.sdt || "",
      hang_bang_lai: drv.hangBangLai ? `Hạng ${drv.hangBangLai}` : "Chưa cập nhật",
      so_bang_lai: drv.bangLai || "Chưa cập nhật",
      diem_uy_tin: drv.uyTin ?? 100,
      cap_canh_bao: drv.capCanhBao || "NORMAL",
      privacy_consent_at: new Date().toLocaleDateString("vi-VN"),
      privacy_consent_version: "v1.2-ND13/2023",
    };

    if (authToken) {
      await Promise.all([
        AsyncStorage.setItem(STORAGE_KEYS.TOKEN, authToken),
        AsyncStorage.setItem(
          STORAGE_KEYS.USER,
          JSON.stringify(driverState.user),
        ),
      ]).catch(() => {});
    }

    return { ...driverState.user };
  },

  // 2. LẤY TOÀN BỘ DỮ LIỆU DASHBOARD CHO APP TÀI XẾ
  async getDriverDashboard() {
    if (!authToken || !driverState.user) {
      return {
        user: driverState.user ? { ...driverState.user } : null,
        biometricProfile: driverState.biometricProfile
          ? { ...driverState.biometricProfile }
          : null,
        currentShift: null,
        scoreLogs: [],
        violations: [],
      };
    }

    try {
      const driverId = driverState.user.driver_id || driverState.user.id;
      const [me, bio, shifts, activeTrip, scoreLogs, violations, disputes] =
        await Promise.all([
          apiRequest("/auth/me").catch(() => null),
          apiRequest(`/drivers/${driverId}/biometrics`).catch(() => null),
          apiRequest(`/shifts?driverId=${driverId}`).catch(() => []),
          apiRequest("/trips/my-active").catch(() => null),
          apiRequest(`/drivers/${driverId}/score-logs`).catch(() => []),
          apiRequest(`/violations?driverId=${driverId}`).catch(() => []),
          apiRequest("/disputes").catch(() => []),
        ]);

      if (me) {
        const drv = me.driver || {};
        driverState.user = {
          ...driverState.user,
          id: me.id,
          driver_id: drv.userId || me.id,
          username: me.username,
          ho_ten: me.ho_ten || me.hoTen || driverState.user.ho_ten,
          so_dien_thoai: me.so_dien_thoai || me.sdt || "",
          hang_bang_lai: drv.hangBangLai
            ? `Hạng ${drv.hangBangLai}`
            : driverState.user.hang_bang_lai || "Chưa cập nhật",
          so_bang_lai:
            drv.bangLai || driverState.user.so_bang_lai || "Chưa cập nhật",
          diem_uy_tin: drv.uyTin ?? driverState.user.diem_uy_tin ?? 100,
          cap_canh_bao:
            drv.capCanhBao || driverState.user.cap_canh_bao || "NORMAL",
        };
      }

      if (bio) {
        const currentScore = driverState.user.diem_uy_tin ?? 100;
        const maxDriveMin = bio.thoiGianLaiToiDaKhuyenNghi || 240;
        const avgDriveMin = bio.thoiGianLaiTrungBinhTruocMetMoi || 180;
        driverState.biometricProfile = {
          khung_gio_yeu_sinh_hoc: bio.khungGioYeuSinhHoc || [
            "13:00 - 15:00",
            "01:00 - 04:00",
          ],
          thoi_gian_lai_toi_da_khuyen_nghi_phut: maxDriveMin,
          thoi_gian_lai_trung_binh_truoc_met_moi_phut: avgDriveMin,
          ear_baseline: 0.285,
          danh_gia:
            bio.danh_gia_chung ||
            bio.danhGiaChung ||
            `Điểm uy tín ${currentScore}/100. Hệ thống khuyến nghị nghỉ giải lao 15 phút sau mỗi ${Math.round(avgDriveMin / 60)} giờ lái liên tục.`,
        };
      }

      const shiftList = Array.isArray(shifts) ? shifts : [];
      const latestShift =
        shiftList.find((s) =>
          ["SCHEDULED", "ACCEPTED"].includes(s.trang_thai || s.trangThai),
        ) ||
        shiftList.find(
          (s) => (s.trang_thai || s.trangThai) === "IN_PROGRESS",
        ) ||
        shiftList[0] ||
        null;

      const matchedTrip =
        latestShift &&
        activeTrip &&
        Number(activeTrip.shift_id || activeTrip.shiftId) ===
          Number(latestShift.id)
          ? activeTrip
          : latestShift?.trip || (!latestShift ? activeTrip : null);

      if (latestShift || matchedTrip) {
        const veh = latestShift?.vehicle || matchedTrip?.vehicle || {};
        const rt = latestShift?.route || matchedTrip?.route || {};
        const rawWarnings = latestShift?.canh_bao_khi_tao;
        let warnings = "";
        if (Array.isArray(rawWarnings)) {
          warnings = rawWarnings
            .map((w) =>
              typeof w === "string" ? w : w?.message || w?.reason || "",
            )
            .filter(Boolean)
            .join(" • ");
        } else if (typeof rawWarnings === "string") {
          warnings = rawWarnings;
        }

        let restStopDisplay = "Theo lộ trình điều phối";
        const rawRestStops =
          rt.danh_sach_tram_dung_chan || rt.danhSachTramDungChan;
        if (rawRestStops) {
          try {
            const parsedStops =
              typeof rawRestStops === "string"
                ? JSON.parse(rawRestStops)
                : rawRestStops;
            if (
              Array.isArray(parsedStops) &&
              parsedStops.length > 0 &&
              parsedStops[0]?.ten
            ) {
              restStopDisplay = `${parsedStops[0].ten}${parsedStops[0].kmMarker ? ` (Km ${parsedStops[0].kmMarker})` : ""}`;
            }
          } catch {
            // ignore parse error
          }
        }

        driverState.currentShift = {
          id:
            latestShift?.id ||
            matchedTrip?.shift_id ||
            matchedTrip?.shiftId ||
            null,
          trip_id: matchedTrip?.id || null,
          bien_so_xe: veh.bien_so_xe || veh.bienSo || "Chưa gán xe",
          loai_xe: veh.loai_xe || veh.loaiXe || "Phương tiện vận tải",
          ma_thiet_bi: `${veh.iot_device?.ma_thiet_bi || veh.iotDevice?.maThietBi || "IOT-EDGE"} (Pi 4 + ESP32)`,
          ten_tuyen:
            rt.ten_tuyen || rt.tenTuyen || "Chưa xác định tuyến đường",
          khoang_cach_km: rt.khoang_cach_km || rt.khoangCachKm || 0,
          thoi_gian_bat_dau: formatDateTimeVN(
            latestShift?.thoi_gian_bat_dau_du_kien ||
              latestShift?.thoiGianBatDauDuKien ||
              matchedTrip?.thoi_gian_bat_dau ||
              matchedTrip?.thoiGianBatDau,
          ),
          thoi_gian_ket_thuc: formatDateTimeVN(
            latestShift?.thoi_gian_ket_thuc_du_kien ||
              latestShift?.thoiGianKetThucDuKien ||
              matchedTrip?.thoi_gian_ket_thuc ||
              matchedTrip?.thoiGianKetThuc,
          ),
          tai_xe_chinh: driverState.user.ho_ten,
          tai_xe_phu: latestShift?.tai_xe_phu || null,
          active_driver_name:
            matchedTrip?.driver?.user?.ho_ten ||
            matchedTrip?.driver?.user?.hoTen ||
            driverState.user.ho_ten,
          shift_status:
            latestShift?.trang_thai || latestShift?.trangThai || "ACCEPTED",
          trip_status:
            matchedTrip?.trang_thai === "IN_PROGRESS" ||
            matchedTrip?.trangThai === "IN_PROGRESS"
              ? "IN_PROGRESS"
              : matchedTrip?.trang_thai === "RESTING" ||
                  matchedTrip?.trangThai === "RESTING"
                ? "RESTING"
                : matchedTrip?.trang_thai === "COMPLETED" ||
                    matchedTrip?.trangThai === "COMPLETED"
                  ? "COMPLETED"
                  : "NOT_STARTED",
          calibration_mode: null,
          canh_bao_sinh_hoc:
            warnings ||
            "Ca trực nằm trong khung giờ an toàn, phù hợp với nhịp sinh học của bạn.",
          telemetry: {
            toc_do_kmh: matchedTrip?.latest_telemetry?.toc_do_kmh ?? 0,
            co2_ppm: matchedTrip?.latest_telemetry?.co2_ppm ?? 450,
            cpu_temp_c:
              veh.iot_device?.cpu_temp_c ?? veh.iotDevice?.cpuTempC ?? null,
            nearest_rest_stop: restStopDisplay,
          },
        };
      } else {
        driverState.currentShift = null;
      }

      if (Array.isArray(scoreLogs)) {
        driverState.scoreLogs = scoreLogs.map((l) => ({
          id: l.id,
          loai_thay_doi: l.loai_thay_doi || l.loaiThayDoi,
          so_diem_thay_doi: l.so_diem_thay_doi ?? l.soDiemThayDoi,
          diem_truoc: l.diem_truoc ?? l.diemTruoc,
          diem_sau: l.diem_sau ?? l.diemSau,
          ly_do: l.ly_do || l.lyDo,
          created_at: formatDateTimeVN(l.created_at || l.createdAt),
        }));
      }

      if (Array.isArray(violations)) {
        const disputeList = Array.isArray(disputes) ? disputes : [];
        driverState.violations = violations.map((v) => {
          const matchedDispute = disputeList.find(
            (d) => (d.violation_id || d.violationId) === v.id,
          );
          const disputeStatus =
            matchedDispute?.trang_thai ||
            matchedDispute?.trangThai ||
            v.dispute_status ||
            v.dispute?.trangThai ||
            null;
          const disputeReason =
            matchedDispute?.noi_dung_giai_trinh ||
            matchedDispute?.noiDungGiaiTrinh ||
            v.dispute_reason ||
            v.dispute?.noiDungGiaiTrinh ||
            null;
          const disputeResponse =
            matchedDispute?.phan_hoi ||
            matchedDispute?.phanHoi ||
            v.dispute?.phanHoi ||
            null;
          const pointStatus =
            v.trang_thai_diem ||
            v.trangThaiDiem ||
            (disputeStatus === "APPROVED" ? "REFUNDED" : "APPLIED");

          return {
            id: v.id,
            ma_loai: v.ma_loai || v.loaiViPham,
            ten_loai: v.ten_loai || v.tenLoai || v.loaiViPham,
            diem_bi_tru: v.diem_bi_tru ?? v.diemTru,
            trang_thai_xac_thuc: v.trang_thai_xac_thuc || v.trangThai,
            trang_thai_diem: pointStatus,
            thoi_gian: formatDateTimeVN(
              v.thoi_gian_chot_vi_pham || v.thoiGianViPham,
            ),
            mo_ta: v.mo_ta || v.moTa,
            video_url: v.video_url || v.videoUrl,
            dispute_status: disputeStatus,
            dispute_reason: disputeReason,
            dispute_response: disputeResponse,
          };
        });
      }
    } catch (err) {
      console.warn("Đang dùng dữ liệu cache cục bộ:", err.message);
    }

    return {
      user: driverState.user ? { ...driverState.user } : null,
      biometricProfile: driverState.biometricProfile
        ? { ...driverState.biometricProfile }
        : null,
      currentShift: driverState.currentShift
        ? { ...driverState.currentShift }
        : null,
      scoreLogs: [...driverState.scoreLogs],
      violations: [...driverState.violations],
    };
  },

  // 3. TÀI XẾ NHẬN CA / TỪ CHỐI CA
  async respondShift(decision, reason = "") {
    if (!driverState.currentShift) return null;
    if (authToken) {
      const shiftId = driverState.currentShift.id;
      await apiRequest(`/shifts/${shiftId}/respond`, {
        method: "PATCH",
        body: JSON.stringify({
          trang_thai: decision,
          ly_do_tu_choi: reason,
        }),
      });
    }
    driverState.currentShift.shift_status = decision;
    return { ...driverState.currentShift };
  },

  // 4. HIỆU CHUẨN KHUÔN MẶT ĐẦU CA & BẮT ĐẦU CHUYẾN (ONLINE / OFFLINE HANDOVER)
  async completeCalibration(isOfflineMode = false) {
    if (!driverState.currentShift) return null;
    if (authToken && !isOfflineMode && driverState.user) {
      const trip = await apiRequest("/trips/start", {
        method: "POST",
        body: JSON.stringify({
          driver_id: driverState.user.driver_id,
          shift_id: driverState.currentShift.id,
          trip_id: driverState.currentShift.trip_id,
          isOfflineMode,
        }),
      });
      if (trip?.id) {
        driverState.currentShift.trip_id = trip.id;
      }
    }
    driverState.currentShift.trip_status = "IN_PROGRESS";
    driverState.currentShift.calibration_mode = isOfflineMode
      ? "PENDING_CALIBRATION"
      : "ONLINE_SUCCESS";
    return { ...driverState.currentShift };
  },

  // 5. TẠM DỪNG NGHỈ TẠI TRẠM (RESTING)
  async toggleResting() {
    if (!driverState.currentShift) return null;
    const current = driverState.currentShift.trip_status;
    const nextStatus = current === "RESTING" ? "IN_PROGRESS" : "RESTING";

    if (authToken && driverState.currentShift.trip_id) {
      const tripId = driverState.currentShift.trip_id;
      await apiRequest(`/trips/${tripId}/status`, {
        method: "PATCH",
        body: JSON.stringify({ trang_thai: nextStatus }),
      });
    }

    driverState.currentShift.trip_status = nextStatus;
    return { ...driverState.currentShift };
  },

  // 6. ĐỔI TÀI (CO-DRIVING HANDOVER)
  async getCoDriverCandidates() {
    if (!authToken || !driverState.user) return [];
    const list = await apiRequest("/drivers");
    const ownId = Number(
      driverState.user.driver_id || driverState.user.id,
    );
    return (Array.isArray(list) ? list : [])
      .filter(
        (d) =>
          Number(d.user_id || d.id) !== ownId &&
          d.cap_canh_bao !== "SUSPENDED_RED",
      )
      .map((d) => ({
        id: d.user_id || d.id,
        ho_ten: d.ho_ten || d.user?.ho_ten || `Tài xế #${d.id}`,
        cap_canh_bao: d.cap_canh_bao || "NORMAL",
        trang_thai_lai_xe: d.trang_thai_lai_xe || "AVAILABLE",
      }));
  },

  async switchCoDriver(isOfflineMode = false, coDriver = null) {
    const shift = driverState.currentShift;
    if (!shift) return null;
    if (authToken && !isOfflineMode && shift.trip_id) {
      const updatedTrip = await apiRequest(
        `/trips/${shift.trip_id}/switch-driver`,
        {
          method: "POST",
          body: JSON.stringify({
            newDriverId: coDriver?.id || null,
            isOfflineMode,
          }),
        },
      );
      const nextDriverName =
        updatedTrip?.driver?.user?.ho_ten ||
        updatedTrip?.driver?.user?.hoTen ||
        coDriver?.ho_ten;
      if (nextDriverName) {
        shift.active_driver_name = nextDriverName;
      }
    } else if (coDriver?.ho_ten) {
      shift.active_driver_name = coDriver.ho_ten;
    } else if (shift.tai_xe_phu) {
      shift.active_driver_name =
        shift.active_driver_name === shift.tai_xe_chinh
          ? shift.tai_xe_phu
          : shift.tai_xe_chinh;
    }

    shift.calibration_mode = isOfflineMode
      ? "PENDING_CALIBRATION"
      : "ONLINE_SUCCESS";
    return { ...shift };
  },

  // 7. HOÀN THÀNH CHUYẾN ĐI
  async completeTrip() {
    if (!driverState.currentShift) return null;
    if (authToken && driverState.currentShift.trip_id) {
      const tripId = driverState.currentShift.trip_id;
      await apiRequest(`/trips/${tripId}/status`, {
        method: "PATCH",
        body: JSON.stringify({ trang_thai: "COMPLETED" }),
      });
    }
    driverState.currentShift.trip_status = "COMPLETED";
    return { ...driverState.currentShift };
  },

  // 8. GỬI ĐƠN KHIẾU NẠI VI PHẠM
  async submitDispute({ violationId, reasonCode, explanation }) {
    if (authToken && driverState.user) {
      await apiRequest("/disputes", {
        method: "POST",
        body: JSON.stringify({
          violation_id: violationId,
          driver_id: driverState.user.driver_id,
          ma_ly_do: reasonCode,
          noi_dung_giai_trinh: explanation,
        }),
      });
    }

    const target = driverState.violations.find((v) => v.id === violationId);
    if (target) {
      target.dispute_status = "OPEN";
      target.dispute_reason = `[${reasonCode}] ${explanation}`;
    }
    return [...driverState.violations];
  },

  // 9. CẬP NHẬT THÔNG TIN CÁ NHÂN (CHỈ CHO SỬA TÊN / SĐT)
  async updateProfile(updatedFields) {
    const cleanPayload = {
      ho_ten: updatedFields.ho_ten?.trim(),
      so_dien_thoai: updatedFields.so_dien_thoai?.trim(),
    };

    if (authToken) {
      await apiRequest("/auth/me", {
        method: "PATCH",
        body: JSON.stringify(cleanPayload),
      });
    }

    driverState.user = {
      ...(driverState.user || {}),
      ...cleanPayload,
    };

    await AsyncStorage.setItem(
      STORAGE_KEYS.USER,
      JSON.stringify(driverState.user),
    ).catch(() => {});

    return { ...driverState.user };
  },

  // 10. TÀI XẾ TỰ ĐỔI MẬT KHẨU BẢO MẬT
  async changePassword({ currentPassword, newPassword }) {
    if (!currentPassword || !newPassword) {
      throw new Error(
        "Vui lòng nhập đầy đủ mật khẩu hiện tại và mật khẩu mới!",
      );
    }
    if (newPassword.length < 6) {
      throw new Error("Mật khẩu mới phải có tối thiểu 6 ký tự!");
    }

    if (authToken) {
      await apiRequest("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ currentPassword, newPassword }),
      });
    }
    return { success: true };
  },

  // 11. LẤY TOKEN HIỆN TẠI ĐỂ KẾT NỐI SOCKET.IO
  getAuthToken() {
    return authToken;
  },
};

export default driverDataService;
