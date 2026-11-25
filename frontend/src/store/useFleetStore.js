import { create } from "zustand";
import dataService from "../services/dataService";

export const useFleetStore = create((set, get) => ({
  drivers: [],
  vehicles: [],
  violations: [],
  disputes: [],
  liveFleet: [],
  monitoringStats: {
    activeVehicles: 0,
    microsleepAlerts: 0,
    fatigueWarnings: 0,
    normalVehicles: 0,
  },
  selectedVehicle: null,
  activeAlert: null,
  socketStatus: "DISCONNECTED", // CONNECTED | MOCK_LIVE | DISCONNECTED
  isSimulating: false,
  globalSearchQuery: "",

  setGlobalSearchQuery: (query) => set({ globalSearchQuery: query }),
  setSocketStatus: (status) => set({ socketStatus: status }),
  setIsSimulating: (val) => set({ isSimulating: val }),
  setSelectedVehicle: (vehicle) => set({ selectedVehicle: vehicle }),
  dismissAlert: () => set({ activeAlert: null }),

  syncFleetData: async () => {
    const [drivers, vehicles, violations, disputes, priorityFleet, stats] =
      await Promise.all([
        dataService.getDrivers(),
        dataService.getVehicles(),
        dataService.getViolations(),
        dataService.getDisputes(),
        dataService.getPriorityFleet(),
        dataService.getMonitoringStats(),
      ]);

    const currentLive = get().liveFleet;

    // Ưu tiên lấy tọa độ GPS thật từ Backend trả về, kết hợp giữ vị trí đang di chuyển trên Map
    const mappedFleet = (priorityFleet || []).map((item, index) => {
      const existing = currentLive.find(
        (v) => (v.plate || v.bien_so_xe) === (item.plate || item.bien_so_xe),
      );
      return {
        ...item,
        lat:
          existing?.lat ??
          item.lat ??
          16.0544 + (index === 0 ? 0.015 : index * 0.018 - 0.025),
        lng:
          existing?.lng ??
          item.lng ??
          108.2022 + (index === 0 ? -0.012 : index * 0.015 - 0.02),
        currentSpeed: parseInt(item.speed, 10) || 0,
        currentCo2:
          item.co2_ppm ||
          parseInt(String(item.co2 || "450").replace(/,/g, ""), 10) ||
          450,
        countdownActive: Boolean(item.countdownActive),
        restDeadlineAt: item.restDeadlineAt || null,
        remainingSeconds: item.countdownSeconds ?? null,
        activeDispatch: item.activeDispatch || existing?.activeDispatch || null,
      };
    });

    const currentSelected = get().selectedVehicle;
    const defaultSelected = currentSelected
      ? mappedFleet.find(
          (v) =>
            (v.plate || v.bien_so_xe) ===
            (currentSelected.plate || currentSelected.bien_so_xe),
        ) ||
        mappedFleet[0] ||
        null
      : mappedFleet.find((v) => v.priorityLevel === "DANGER") ||
        mappedFleet[0] ||
        null;

    set({
      drivers: drivers || [],
      vehicles: vehicles || [],
      violations: violations || [],
      disputes: disputes || [],
      liveFleet: mappedFleet,
      selectedVehicle: defaultSelected,
      monitoringStats: stats || get().monitoringStats,
    });
  },

  handleTelemetrySocket: (payload) => {
    set((state) => {
      const updatedFleet = state.liveFleet.map((v) => {
        const plate = v.plate || v.bien_so_xe;
        if (v.id === payload.vehicle_id || plate === payload.bien_so_xe) {
          const isRed = payload.marker_color === "RED";
          const isYellow = payload.marker_color === "YELLOW";
          return {
            ...v,
            lat: payload.vi_do_gps ?? v.lat,
            lng: payload.kinh_do_gps ?? v.lng,
            currentSpeed: payload.toc_do_kmh ?? v.currentSpeed,
            currentCo2: payload.co2_ppm ?? v.currentCo2,
            priorityLevel: isRed ? "DANGER" : isYellow ? "WARNING" : "SAFE",
          };
        }
        return v;
      });

      const sel = state.selectedVehicle;
      const updatedSelected = sel
        ? updatedFleet.find(
            (x) => (x.plate || x.bien_so_xe) === (sel.plate || sel.bien_so_xe),
          ) || sel
        : null;

      return {
        liveFleet: updatedFleet,
        selectedVehicle: updatedSelected,
      };
    });
  },

  tickRealtimeTelemetry: () => {
    const state = get();
    if (!state.isSimulating || !state.liveFleet.length) return;

    const updatedFleet = state.liveFleet.map((v) => {
      const deltaLat = (Math.random() - 0.48) * 0.0012;
      const deltaLng = (Math.random() - 0.48) * 0.0012;
      return {
        ...v,
        lat: v.lat + deltaLat,
        lng: v.lng + deltaLng,
        currentSpeed: Math.max(
          40,
          Math.min(85, Math.round(v.currentSpeed + (Math.random() - 0.5) * 4)),
        ),
        remainingSeconds:
          v.countdownActive && v.remainingSeconds > 0
            ? v.remainingSeconds - 2
            : v.remainingSeconds,
      };
    });

    const sel = state.selectedVehicle;
    const updatedSelected = sel
      ? updatedFleet.find(
          (x) => (x.plate || x.bien_so_xe) === (sel.plate || sel.bien_so_xe),
        ) || sel
      : null;

    set({
      liveFleet: updatedFleet,
      selectedVehicle: updatedSelected,
    });
  },

  handleNewViolationSocket: (newViolation) => {
    const currentViolations = get().violations;
    const updatedViolations = [newViolation, ...currentViolations];
    const videoUrl = newViolation.video_url || newViolation.duong_dan_video || null;
    const confidencePct = newViolation.ai_confidence_score
      ? Math.round(Number(newViolation.ai_confidence_score) * 100)
      : null;

    const updatedFleet = get().liveFleet.map((v) => {
      const plate = v.plate || v.bien_so_xe;
      if (plate === newViolation.bien_so_xe) {
        const deadlineAt = new Date(Date.now() + 15 * 60 * 1000).toISOString();
        return {
          ...v,
          priorityLevel: "DANGER",
          priorityBadge: "CAN THIỆP GẤP",
          alertType: newViolation.ma_loai,
          alertDesc: newViolation.mo_ta,
          countdownActive: true,
          restDeadlineAt: v.restDeadlineAt || deadlineAt,
          remainingSeconds: v.remainingSeconds ?? 900,
          video_url: videoUrl || v.video_url || null,
          ai_confidence_score:
            newViolation.ai_confidence_score ?? v.ai_confidence_score ?? null,
          evidenceClip: videoUrl
            ? {
                videoUrl,
                aiConfidence: confidencePct ?? 90,
              }
            : v.evidenceClip || null,
        };
      }
      return v;
    });

    const sel = get().selectedVehicle;
    const updatedSelected = sel
      ? updatedFleet.find(
          (x) => (x.plate || x.bien_so_xe) === (sel.plate || sel.bien_so_xe),
        ) || sel
      : null;

    const currentStats = get().monitoringStats;
    const isMicrosleep =
      (newViolation.ma_loai || "").toUpperCase() === "MICROSLEEP";

    set({
      violations: updatedViolations,
      liveFleet: updatedFleet,
      selectedVehicle: updatedSelected,
      activeAlert: newViolation,
      monitoringStats: {
        ...currentStats,
        microsleepAlerts: isMicrosleep
          ? (currentStats.microsleepAlerts || 0) + 1
          : currentStats.microsleepAlerts || 0,
        fatigueWarnings: !isMicrosleep
          ? (currentStats.fatigueWarnings || 0) + 1
          : currentStats.fatigueWarnings || 0,
      },
    });
  },

  handleDeviceHealthSocket: (healthPayload) => {
    set((state) => ({
      vehicles: state.vehicles.map((v) =>
        v.id === healthPayload.vehicle_id
          ? {
              ...v,
              iot_device: {
                ...v.iot_device,
                ...healthPayload,
              },
            }
          : v,
      ),
    }));
  },

  handleRestDispatchSocket: (dispatchPayload) => {
    if (!dispatchPayload) return;
    set((state) => {
      const updatedFleet = state.liveFleet.map((v) => {
        const plate = v.plate || v.bien_so_xe;
        const matchId =
          dispatchPayload.vehicle_id &&
          Number(v.id) === Number(dispatchPayload.vehicle_id);
        const matchPlate =
          dispatchPayload.vehiclePlate && plate === dispatchPayload.vehiclePlate;
        if (!matchId && !matchPlate) return v;

        return {
          ...v,
          activeDispatch: dispatchPayload,
          countdownActive: true,
          restDeadlineAt: dispatchPayload.restDeadlineAt || v.restDeadlineAt,
          remainingSeconds: 900,
        };
      });

      const sel = state.selectedVehicle;
      const updatedSelected = sel
        ? updatedFleet.find(
            (x) => (x.plate || x.bien_so_xe) === (sel.plate || sel.bien_so_xe),
          ) || sel
        : null;

      return {
        liveFleet: updatedFleet,
        selectedVehicle: updatedSelected,
      };
    });
  },
}));
