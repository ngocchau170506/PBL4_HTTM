import { useEffect, useMemo, useRef, useState } from "react";
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
    handleRestDispatchSocket,
  } = useFleetStore();

  const [showVideoModal, setShowVideoModal] = useState(false);
  const [showCallModal, setShowCallModal] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [nearbyStops, setNearbyStops] = useState([]);
  const [selectedStop, setSelectedStop] = useState(null);
  const [loadingStops, setLoadingStops] = useState(false);
  const [isPickingPin, setIsPickingPin] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const mapInstanceRef = useRef(null);

  // Đồng hồ 1s tính đếm ngược thực tế theo restDeadlineAt
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Tự động tìm Top 3 trạm dừng/xăng dầu gần tọa độ xe hiện tại (Overpass + fallback DB)
  useEffect(() => {
    if (!selectedVehicle?.lat || !selectedVehicle?.lng) {
      setNearbyStops([]);
      setSelectedStop(null);
      return;
    }

    let cancelled = false;
    setLoadingStops(true);
    setIsPickingPin(false);

    dataService
      .getNearbyRestStops({
        lat: selectedVehicle.lat,
        lng: selectedVehicle.lng,
        vehicleId: selectedVehicle.id,
        routeId: selectedVehicle.route_id || undefined,
        limit: 3,
      })
      .then((res) => {
        if (cancelled) return;
        const stops = res?.stops || [];
        setNearbyStops(stops);
        setSelectedStop(stops[0] || null);
      })
      .catch(() => {
        if (cancelled) return;
        const fb = selectedVehicle?.nearestRestStop;
        const fallbackList = fb?.lat
          ? [
              {
                id: "route-fallback",
                name: fb.name,
                categoryLabel: "Trạm trên tuyến",
                lat: fb.lat,
                lng: fb.lng,
                distanceKm: fb.distanceKm ?? "--",
                source: "ROUTE_DB",
                googleMapsUrl: fb.googleMapsUrl,
              },
            ]
          : [];
        setNearbyStops(fallbackList);
        setSelectedStop(fallbackList[0] || null);
      })
      .finally(() => {
        if (!cancelled) setLoadingStops(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedVehicle?.id]);

  const countdownInfo = useMemo(() => {
    if (selectedVehicle?.trip_status === "RESTING") {
      return {
        mode: "RESTING",
        label: "Đang dừng nghỉ",
        sub: "An toàn tại trạm",
      };
    }

    if (selectedVehicle?.countdownActive && selectedVehicle?.restDeadlineAt) {
      const deadlineMs = new Date(selectedVehicle.restDeadlineAt).getTime();
      const diffSec = Math.max(0, Math.round((deadlineMs - nowMs) / 1000));
      if (diffSec <= 0) {
        return { mode: "EXPIRED", label: "00:00 (QUÁ HẠN)", sub: "Cần can thiệp ngay" };
      }
      const m = Math.floor(diffSec / 60);
      const s = diffSec % 60;
      return {
        mode: "ACTIVE",
        label: `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`,
        sub: "Giới hạn 15 phút tấp trạm",
      };
    }

    return {
      mode: "IDLE",
      label: "Không áp dụng",
      sub:
        selectedVehicle?.trip_status === "IN_PROGRESS"
          ? "Xe đang chạy an toàn"
          : "Xe đang đỗ tại bãi",
    };
  }, [selectedVehicle, nowMs]);

  const handleSelectVehicle = (vehicle) => {
    setSelectedVehicle(vehicle);
    if (mapInstanceRef.current && vehicle?.lat && vehicle?.lng) {
      mapInstanceRef.current.flyTo([vehicle.lat, vehicle.lng], 13.5, {
        animate: true,
        duration: 1.0,
      });
    }
  };

  const handlePickMapPoint = (lat, lng) => {
    const manualStop = {
      id: "manual-pin",
      name: `Điểm dừng chỉ định (${lat.toFixed(4)}, ${lng.toFixed(4)})`,
      category: "MANUAL_PIN",
      categoryLabel: "Chấm tay trên bản đồ",
      lat: Number(lat.toFixed(6)),
      lng: Number(lng.toFixed(6)),
      distanceKm: null,
      source: "MANUAL_PIN",
    };
    setNearbyStops((prev) => [
      manualStop,
      ...prev.filter((s) => s.id !== "manual-pin"),
    ]);
    setSelectedStop(manualStop);
    setIsPickingPin(false);
    toast.success("Đã chọn điểm dừng tùy chỉnh trên bản đồ!", { icon: "📍" });
  };

  const handleDispatchNav = async () => {
    if (!selectedVehicle || dispatching) return;
    setDispatching(true);
    try {
      const plate =
        selectedVehicle.plate || selectedVehicle.bien_so || "Xe IoT";
      const target = selectedStop || selectedVehicle.nearestRestStop;
      const res = await dataService.dispatchVehicleToRestStop({
        vehicleId: selectedVehicle.id,
        vehiclePlate: plate,
        restStopName: target?.name || "Trạm dừng nghỉ gần nhất",
        lat: target?.lat,
        lng: target?.lng,
        fromLat: selectedVehicle.lat,
        fromLng: selectedVehicle.lng,
        category: target?.category,
      });

      const dispatch = res?.dispatch;
      if (dispatch) {
        handleRestDispatchSocket(dispatch);
      }

      toast.success(
        dispatch
          ? `Đã điều hướng ${plate} -> ${dispatch.restStopName} (${dispatch.distanceKm} km • ~${dispatch.etaMinutes} phút)!`
          : `Đã phát lệnh điều hướng xe ${plate}!`,
        {
          duration: 5000,
          icon: "🧭",
        },
      );
    } catch (err) {
      toast.error(err?.message || "Không thể phát lệnh điều hướng trạm nghỉ.");
    } finally {
      setDispatching(false);
    }
  };

  return (
    <div className="animate-fade-in-up space-y-5 font-sans">
      <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
        <div>
          <h1 className="text-brand-text-primary text-2xl font-black tracking-tight">
            Giám sát Thời gian thực & Điều phối xe
          </h1>
          <p className="text-brand-text-muted text-xs">
            Tự động tìm trạm dừng OpenStreetMap + Dẫn đường OSRM + Đồng bộ Google Maps Mobile
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
          selectedStop={selectedStop}
          isPickingPin={isPickingPin}
          onPickMapPoint={handlePickMapPoint}
        />
        <VehicleDispatchPanel
          vehicle={selectedVehicle}
          countdownInfo={countdownInfo}
          nearbyStops={nearbyStops}
          selectedStop={selectedStop}
          onSelectStop={setSelectedStop}
          loadingStops={loadingStops}
          isPickingPin={isPickingPin}
          onTogglePickPin={() => setIsPickingPin((v) => !v)}
          dispatching={dispatching}
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
