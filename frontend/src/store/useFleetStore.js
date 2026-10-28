import { create } from "zustand";
import dataService from "../services/dataService";

export const useFleetStore = create((set, get) => ({
  vehicles: [],
  violations: [],
  disputes: [],
  selectedVehicle: null,
  activeAlert: null,

  syncFleetData: async () => {
    const [vehicles, violations, disputes] = await Promise.all([
      dataService.getVehicles(),
      dataService.getViolations(),
      dataService.getDisputes(),
    ]);
    set({
      vehicles: vehicles || [],
      violations: violations || [],
      disputes: disputes || [],
    });
  },

  setSelectedVehicle: (vehicle) => set({ selectedVehicle: vehicle }),

  dismissAlert: () => set({ activeAlert: null }),

  updateTelemetry: (telemetryData) => {
    set((state) => ({
      vehicles: state.vehicles.map((v) =>
        v.id === telemetryData.vehicle_id
          ? {
              ...v,
              telemetry: {
                ...v.telemetry,
                vi_do: telemetryData.vi_do_gps,
                kinh_do: telemetryData.kinh_do_gps,
                toc_do: telemetryData.toc_do_kmh,
                co2_ppm: telemetryData.co2_ppm,
                trang_thai_canh_bao: telemetryData.marker_color,
              },
            }
          : v,
      ),
    }));
  },

  addNewViolation: (newViolation) => {
    const currentViolations = get().violations;
    set({
      violations: [newViolation, ...currentViolations],
      activeAlert: newViolation,
    });
  },
}));
