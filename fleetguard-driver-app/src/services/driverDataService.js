// ============================================================================
// CẤU HÌNH KẾT NỐI BACKEND CHO MOBILE APP (REACT NATIVE / WEB MOBILE PREVIEW)
// ============================================================================
const USE_MOCK = false; // Đổi thành true nếu muốn chạy 100% dữ liệu giả lập
// Lưu ý:
// - Nếu chạy trên Web / iOS Simulator: dùng "http://localhost:3000/api/v1"
// - Nếu chạy trên Android Emulator: đổi thành "http://10.0.2.2:3000/api/v1"
// - Nếu chạy trên Điện thoại thật (Expo Go): đổi thành IP LAN máy tính, VD: "http://192.168.1.100:3000/api/v1"
const API_BASE_URL = "http://localhost:3000/api/v1";

let authToken = null;

// Helper gọi API chuẩn (Không cần cài thêm thư viện ngoài, chạy được cả trên RN và Web)
async function apiRequest(endpoint, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    ...(options.headers || {}),
  };

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  const json = await response.json();
  if (!response.ok || json.success === false) {
    throw new Error(
      json.message || json.error?.message || "Lỗi kết nối tới máy chủ",
    );
  }
  return json.data !== undefined ? json.data : json;
}

// ============================================================================
// STATE DỰ PHÒNG & ĐỒNG BỘ UI MOBILE (FALLBACK & LOCAL CACHE)
// ============================================================================
let mockDriverState = {
  user: {
    id: 4,
    driver_id: 4,
    username: "tx_hoangnam",
    ho_ten: "Phạm Hoàng Nam",
    email: "hoangnam.driver@fleetguard.vn",
    so_dien_thoai: "0905444555",
    hang_bang_lai: "Hạng C",
    so_bang_lai: "C-430054321",
    ngay_het_han_bang_lai: "15/08/2029",
    diem_uy_tin: 58,
    cap_canh_bao: "WARNING_YELLOW",
    privacy_consent_at: "05/09/2026",
    privacy_consent_version: "v1.2-ND13/2023",
  },
  biometricProfile: {
    khung_gio_yeu_sinh_hoc: ["13:00 - 15:00", "01:00 - 04:00"],
    thoi_gian_lai_toi_da_khuyen_nghi_phut: 180,
    thoi_gian_lai_trung_binh_truoc_met_moi_phut: 145,
    ear_baseline: 0.285,
    danh_gia:
      "Điểm uy tín 58 (Cảnh cáo vàng). Hệ thống khuyến nghị nghỉ giải lao 15 phút sau mỗi 2.5 giờ lái liên tục.",
  },
  currentShift: {
    id: 2,
    trip_id: 2,
    bien_so_xe: "43H-567.89",
    loai_xe: "Xe tải thùng 8 tấn",
    ma_thiet_bi: "IOT-DN-02 (Pi 4 + ESP32)",
    ten_tuyen: "KCN Hòa Khánh (Đà Nẵng) → KCN VSIP (Quảng Ngãi)",
    khoang_cach_km: 138.0,
    thoi_gian_bat_dau: "14:00 - 22/10/2026",
    thoi_gian_ket_thuc: "18:30 - 22/10/2026",
    tai_xe_chinh: "Phạm Hoàng Nam",
    tai_xe_phu: "Võ Quốc Bảo",
    active_driver_name: "Phạm Hoàng Nam",
    shift_status: "SCHEDULED",
    trip_status: "NOT_STARTED",
    calibration_mode: null,
    canh_bao_sinh_hoc:
      "Lưu ý: Ca chạy đi qua khung giờ yếu sinh học (13:00 - 15:00). Bắt buộc dừng nghỉ tại Trạm Tam Kỳ (Km 72).",
    telemetry: {
      toc_do_kmh: 64,
      co2_ppm: 780,
      cpu_temp_c: 63.5,
      nearest_rest_stop: "Trạm dịch vụ Tam Kỳ (Cách 14 km)",
    },
  },
  scoreLogs: [],
  violations: [],
};

const formatDateTimeVN = (isoStr) => {
  if (!isoStr) return "22/10/2026 • 14:00";
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

    if (USE_MOCK) return { ...mockDriverState.user };

    const res = await apiRequest("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: username.trim(), password }),
    });

    authToken = res.access_token || res.token;
    const u = res.user || {};
    const drv = u.driver || {};

    mockDriverState.user = {
      ...mockDriverState.user,
      id: u.id,
      driver_id: drv.userId || u.id,
      username: u.username,
      ho_ten: u.ho_ten || u.hoTen || mockDriverState.user.ho_ten,
      so_dien_thoai: u.so_dien_thoai || u.sdt || "0905444555",
      hang_bang_lai: `Hạng ${drv.hangBangLai || "C"}`,
      so_bang_lai: drv.bangLai || "C-430054321",
      diem_uy_tin: drv.uyTin ?? 95,
      cap_canh_bao: drv.capCanhBao || "NORMAL",
      privacy_consent_at: new Date().toLocaleDateString("vi-VN"),
    };

    return { ...mockDriverState.user };
  },

  // 2. LẤY TOÀN BỘ DỮ LIỆU DASHBOARD CHO APP TÀI XẾ
  async getDriverDashboard() {
    if (USE_MOCK || !authToken) {
      return {
        user: { ...mockDriverState.user },
        biometricProfile: { ...mockDriverState.biometricProfile },
        currentShift: { ...mockDriverState.currentShift },
        scoreLogs: [...mockDriverState.scoreLogs],
        violations: [...mockDriverState.violations],
      };
    }

    try {
      const driverId =
        mockDriverState.user.driver_id || mockDriverState.user.id;
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
        mockDriverState.user = {
          ...mockDriverState.user,
          id: me.id,
          driver_id: drv.userId || me.id,
          username: me.username,
          ho_ten: me.ho_ten || me.hoTen,
          so_dien_thoai: me.so_dien_thoai || me.sdt || "0905444555",
          hang_bang_lai: `Hạng ${drv.hangBangLai || "C"}`,
          so_bang_lai: drv.bangLai || mockDriverState.user.so_bang_lai,
          diem_uy_tin: drv.uyTin ?? mockDriverState.user.diem_uy_tin,
          cap_canh_bao: drv.capCanhBao || mockDriverState.user.cap_canh_bao,
        };
      }

      if (bio) {
        mockDriverState.biometricProfile = {
          khung_gio_yeu_sinh_hoc: bio.khungGioYeuSinhHoc || [
            "13:00 - 15:00",
            "01:00 - 04:00",
          ],
          thoi_gian_lai_toi_da_khuyen_nghi_phut:
            bio.thoiGianLaiToiDaKhuyenNghi || 180,
          thoi_gian_lai_trung_binh_truoc_met_moi_phut:
            bio.thoiGianLaiTrungBinhTruocMetMoi || 145,
          ear_baseline: 0.285,
          danh_gia:
            bio.danhGiaChung || mockDriverState.biometricProfile.danh_gia,
        };
      }

      const latestShift =
        Array.isArray(shifts) && shifts.length > 0 ? shifts[0] : null;
      if (latestShift || activeTrip) {
        const veh = activeTrip?.vehicle || latestShift?.vehicle || {};
        const rt = activeTrip?.route || latestShift?.route || {};
        const warnings = Array.isArray(latestShift?.canh_bao_khi_tao)
          ? latestShift.canh_bao_khi_tao.join(" ")
          : "";

        mockDriverState.currentShift = {
          ...mockDriverState.currentShift,
          id: latestShift?.id || activeTrip?.shift_id || 2,
          trip_id: activeTrip?.id || latestShift?.trip?.id || 2,
          bien_so_xe: veh.bien_so_xe || veh.bienSo || "43H-567.89",
          loai_xe: veh.loai_xe || veh.loaiXe || "Xe tải thùng 8 tấn",
          ma_thiet_bi: `${veh.iot_device?.ma_thiet_bi || "IOT-DN-02"} (Pi 4 + ESP32)`,
          ten_tuyen:
            rt.ten_tuyen ||
            rt.tenTuyen ||
            mockDriverState.currentShift.ten_tuyen,
          khoang_cach_km: rt.khoang_cach_km || rt.khoangCachKm || 138.0,
          thoi_gian_bat_dau: formatDateTimeVN(
            latestShift?.thoi_gian_bat_dau_du_kien ||
              activeTrip?.thoi_gian_bat_dau,
          ),
          thoi_gian_ket_thuc: formatDateTimeVN(
            latestShift?.thoi_gian_ket_thuc_du_kien,
          ),
          tai_xe_chinh: mockDriverState.user.ho_ten,
          active_driver_name:
            activeTrip?.driver?.user?.ho_ten || mockDriverState.user.ho_ten,
          shift_status: latestShift?.trang_thai || "ACCEPTED",
          trip_status:
            activeTrip?.trang_thai === "IN_PROGRESS"
              ? "IN_PROGRESS"
              : activeTrip?.trang_thai === "RESTING"
                ? "RESTING"
                : activeTrip?.trang_thai === "COMPLETED"
                  ? "COMPLETED"
                  : "NOT_STARTED",
          canh_bao_sinh_hoc:
            warnings || mockDriverState.currentShift.canh_bao_sinh_hoc,
          telemetry: {
            toc_do_kmh: activeTrip?.latest_telemetry?.toc_do_kmh ?? 64,
            co2_ppm: activeTrip?.latest_telemetry?.co2_ppm ?? 780,
            cpu_temp_c: veh.iot_device?.cpu_temp_c ?? 63.5,
            nearest_rest_stop: "Trạm dịch vụ Tam Kỳ (Cách 14 km)",
          },
        };
      }

      if (Array.isArray(scoreLogs)) {
        mockDriverState.scoreLogs = scoreLogs.map((l) => ({
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
        mockDriverState.violations = violations.map((v) => {
          const matchedDispute = disputeList.find(
            (d) => (d.violation_id || d.violationId) === v.id,
          );
          return {
            id: v.id,
            ma_loai: v.ma_loai || v.loaiViPham,
            ten_loai: v.ten_loai || v.tenLoai || v.loaiViPham,
            diem_bi_tru: v.diem_bi_tru ?? v.diemTru,
            trang_thai_xac_thuc: v.trang_thai_xac_thuc || v.trangThai,
            thoi_gian: formatDateTimeVN(
              v.thoi_gian_chot_vi_pham || v.thoiGianViPham,
            ),
            mo_ta: v.mo_ta || v.moTa,
            video_url: v.video_url || v.videoUrl,
            dispute_status: matchedDispute ? matchedDispute.trang_thai : null,
            dispute_reason: matchedDispute
              ? matchedDispute.noi_dung_giai_trinh
              : null,
          };
        });
      }
    } catch (err) {
      console.warn("Đang dùng dữ liệu cache cục bộ:", err.message);
    }

    return {
      user: { ...mockDriverState.user },
      biometricProfile: { ...mockDriverState.biometricProfile },
      currentShift: { ...mockDriverState.currentShift },
      scoreLogs: [...mockDriverState.scoreLogs],
      violations: [...mockDriverState.violations],
    };
  },

  // 3. TÀI XẾ NHẬN CA / TỪ CHỐI CA
  async respondShift(decision, reason = "") {
    if (!USE_MOCK && authToken) {
      const shiftId = mockDriverState.currentShift.id;
      await apiRequest(`/shifts/${shiftId}/respond`, {
        method: "PATCH",
        body: JSON.stringify({
          trang_thai: decision,
          ly_do_tu_choi: reason,
        }),
      });
    }
    mockDriverState.currentShift.shift_status = decision;
    return { ...mockDriverState.currentShift };
  },

  // 4. HIỆU CHUẨN KHUÔN MẶT ĐẦU CA & BẮT ĐẦU CHUYẾN (ONLINE / OFFLINE HANDOVER)
  async completeCalibration(isOfflineMode = false) {
    if (!USE_MOCK && authToken && !isOfflineMode) {
      const trip = await apiRequest("/trips/start", {
        method: "POST",
        body: JSON.stringify({
          driver_id: mockDriverState.user.driver_id,
          shift_id: mockDriverState.currentShift.id,
          trip_id: mockDriverState.currentShift.trip_id,
          isOfflineMode,
        }),
      });
      if (trip?.id) {
        mockDriverState.currentShift.trip_id = trip.id;
      }
    }
    mockDriverState.currentShift.trip_status = "IN_PROGRESS";
    mockDriverState.currentShift.calibration_mode = isOfflineMode
      ? "PENDING_CALIBRATION"
      : "ONLINE_SUCCESS";
    return { ...mockDriverState.currentShift };
  },

  // 5. TẠM DỪNG NGHỈ TẠI TRẠM (RESTING)
  async toggleResting() {
    const current = mockDriverState.currentShift.trip_status;
    const nextStatus = current === "RESTING" ? "IN_PROGRESS" : "RESTING";

    if (!USE_MOCK && authToken) {
      const tripId = mockDriverState.currentShift.trip_id;
      await apiRequest(`/trips/${tripId}/status`, {
        method: "PATCH",
        body: JSON.stringify({ trang_thai: nextStatus }),
      });
    }

    mockDriverState.currentShift.trip_status = nextStatus;
    return { ...mockDriverState.currentShift };
  },

  // 6. ĐỔI TÀI (CO-DRIVING HANDOVER)
  async switchCoDriver(isOfflineMode = false) {
    const shift = mockDriverState.currentShift;
    if (!USE_MOCK && authToken && !isOfflineMode) {
      await apiRequest(`/trips/${shift.trip_id}/switch-driver`, {
        method: "POST",
        body: JSON.stringify({ isOfflineMode }),
      }).catch(() => null);
    }

    shift.active_driver_name =
      shift.active_driver_name === shift.tai_xe_chinh
        ? shift.tai_xe_phu
        : shift.tai_xe_chinh;
    shift.calibration_mode = isOfflineMode
      ? "PENDING_CALIBRATION"
      : "ONLINE_SUCCESS";
    return { ...shift };
  },

  // 7. HOÀN THÀNH CHUYẾN ĐI
  async completeTrip() {
    if (!USE_MOCK && authToken) {
      const tripId = mockDriverState.currentShift.trip_id;
      await apiRequest(`/trips/${tripId}/status`, {
        method: "PATCH",
        body: JSON.stringify({ trang_thai: "COMPLETED" }),
      });
    }
    mockDriverState.currentShift.trip_status = "COMPLETED";
    return { ...mockDriverState.currentShift };
  },

  // 8. GỬI ĐƠN KHIẾU NẠI VI PHẠM
  async submitDispute({ violationId, reasonCode, explanation }) {
    if (!USE_MOCK && authToken) {
      await apiRequest("/disputes", {
        method: "POST",
        body: JSON.stringify({
          violation_id: violationId,
          driver_id: mockDriverState.user.driver_id,
          ma_ly_do: reasonCode,
          noi_dung_giai_trinh: explanation,
        }),
      });
    }

    const target = mockDriverState.violations.find((v) => v.id === violationId);
    if (target) {
      target.dispute_status = "OPEN";
      target.dispute_reason = `[${reasonCode}] ${explanation}`;
    }
    return [...mockDriverState.violations];
  },

  // 9. CẬP NHẬT THÔNG TIN CÁ NHÂN (CHỈ CHO SỬA SĐT / TÊN LIÊN HỆ, KHÔNG CHO SỬA BẰNG LÁI)
  async updateProfile(updatedFields) {
    if (!USE_MOCK && authToken) {
      await apiRequest("/auth/me", {
        method: "PATCH",
        body: JSON.stringify({
          ho_ten: updatedFields.ho_ten,
          so_dien_thoai: updatedFields.so_dien_thoai,
        }),
      });
    }

    mockDriverState.user = {
      ...mockDriverState.user,
      ...updatedFields,
    };
    return { ...mockDriverState.user };
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

    if (!USE_MOCK && authToken) {
      await apiRequest("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ currentPassword, newPassword }),
      });
    }
    return { success: true };
  },
};

export default driverDataService;
