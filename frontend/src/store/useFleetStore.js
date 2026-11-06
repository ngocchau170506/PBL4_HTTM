import { create } from "zustand";
import dataService from "../services/dataService";

export const useFleetStore = create((set, get) => ({
  drivers: [],
  vehicles: [],
  violations: [],
  disputes: [],
  liveFleet: [],
  monitoringStats: {
    activeVehicles: 4,
    microsleepAlerts: 1,
    fatigueWarnings: 1,
    normalVehicles: 2,
  },
  selectedVehicle: null,
  activeAlert: null,
  socketStatus: "DISCONNECTED", // CONNECTED | MOCK_LIVE | DISCONNECTED
  isSimulating: true,
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
        currentSpeed: parseInt(item.speed) || 60,
        currentCo2:
          item.co2_ppm ||
          parseInt(String(item.co2 || "850").replace(",", "")) ||
          850,
        remainingSeconds:
          existing?.remainingSeconds ??
          (item.countdownSeconds !== undefined ? item.countdownSeconds : 900),
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
        remainingSeconds: v.remainingSeconds > 0 ? v.remainingSeconds - 2 : 0,
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

    const updatedFleet = get().liveFleet.map((v) => {
      const plate = v.plate || v.bien_so_xe;
      if (plate === newViolation.bien_so_xe) {
        return {
          ...v,
          priorityLevel: "DANGER",
          priorityBadge: "CAN THIỆP GẤP",
          alertType: newViolation.ma_loai,
          alertDesc: newViolation.mo_ta,
        };
      }
      return v;
    });

    const currentStats = get().monitoringStats;

    set({
      violations: updatedViolations,
      liveFleet: updatedFleet,
      activeAlert: newViolation,
      monitoringStats: {
        ...currentStats,
        microsleepAlerts: (currentStats.microsleepAlerts || 0) + 1,
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
}));
