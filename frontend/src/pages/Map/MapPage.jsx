import { useRef, useState } from "react";
import toast from "react-hot-toast";
import dataService from "../../services/dataService";
import { useFleetStore } from "../../store/useFleetStore";
import EmergencyCallModal from "./components/EmergencyCallModal";
import EvidenceVideoModal from "./components/EvidenceVideoModal";
import LeafletMapView from "./components/LeafletMapView";
import MapKpiStats from "./components/MapKpiStats";
import VehicleDispatchPanel from "./components/VehicleDispatchPanel";

export default function MapPage() {
  const {
    liveFleet,
    selectedVehicle,
    setSelectedVehicle,
    monitoringStats,
    isSimulating,
    setIsSimulating,
  } = useFleetStore();

  const [showVideoModal, setShowVideoModal] = useState(false);
  const [showCallModal, setShowCallModal] = useState(false);
  const mapInstanceRef = useRef(null);

  const formatCountdown = (totalSec = 0) => {
    if (totalSec <= 0) return "00:00 (Hết hạn)";
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  };

  const handleSelectVehicle = (vehicle) => {
    setSelectedVehicle(vehicle);
    if (mapInstanceRef.current && vehicle?.lat && vehicle?.lng) {
      mapInstanceRef.current.flyTo([vehicle.lat, vehicle.lng], 13.5, {
        animate: true,
        duration: 1.0,
      });
    }
  };

  const handleDispatchNav = async () => {
    const plate =
      selectedVehicle?.plate || selectedVehicle?.bien_so || "Xe IoT";
    const targetStop =
      selectedVehicle?.nearestRestStop?.name || "Trạm Tiên Hiệp (Km 185)";
    await dataService.dispatchVehicleToRestStop({
      vehiclePlate: plate,
      restStopName: targetStop,
    });
    toast.success(
      `Đã điều hướng xe vào [${targetStop}] tới Cabin xe ${plate}!`,
      {
        duration: 4500,
        icon: "🧭",
      },
    );
  };

  return (
    <div className="animate-fade-in-up space-y-5 font-sans">
      <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
        <div>
          <h1 className="text-brand-text-primary text-2xl font-black tracking-tight">
            Giám sát Thời gian thực & Điều phối xe
          </h1>
          <p className="text-brand-text-muted text-xs">
            Đồng bộ trực tiếp qua Zustand Global Store & Socket.io Telemetry
          </p>
        </div>
        <button
          onClick={() => setIsSimulating(!isSimulating)}
          className={`flex cursor-pointer items-center gap-2 rounded-xl px-3.5 py-1.5 text-xs font-bold transition-all ${
            isSimulating
              ? "border border-emerald-500/40 bg-emerald-950/60 text-emerald-400 shadow-sm"
              : "border-brand-border bg-brand-dark/80 text-brand-text-muted border"
          }`}
        >
          <span className="relative flex h-2 w-2">
            {isSimulating && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75"></span>
            )}
            <span
              className={`relative inline-flex h-2 w-2 rounded-full ${
                isSimulating ? "bg-emerald-500" : "bg-slate-500"
              }`}
            ></span>
          </span>
          <span>
            {isSimulating ? "Socket Telemetry: LIVE" : "Tạm dừng giả lập"}
          </span>
        </button>
      </div>

      <MapKpiStats stats={monitoringStats} />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
        <LeafletMapView
          vehicles={liveFleet}
          selectedVehicle={selectedVehicle}
          onSelectVehicle={handleSelectVehicle}
          mapInstanceRef={mapInstanceRef}
        />
        <VehicleDispatchPanel
          vehicle={selectedVehicle}
          countdownText={formatCountdown(selectedVehicle?.remainingSeconds)}
          onDispatchNav={handleDispatchNav}
          onOpenCallModal={() => setShowCallModal(true)}
          onOpenVideoModal={() => setShowVideoModal(true)}
        />
      </div>

      <EvidenceVideoModal
        isOpen={showVideoModal}
        onClose={() => setShowVideoModal(false)}
        vehicle={selectedVehicle}
      />
      <EmergencyCallModal
        isOpen={showCallModal}
        onClose={() => setShowCallModal(false)}
        vehicle={selectedVehicle}
      />
    </div>
  );
}
