import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";

const createVehicleIcon = (colorHex, isDanger = false, plateText = "") => {
  return L.divIcon({
    className: "!bg-transparent !border-0",
    iconSize: [46, 54],
    iconAnchor: [23, 27],
    html: `
      <div style="position: relative; display: flex; flex-direction: column; align-items: center; cursor: pointer;">
        ${
          isDanger
            ? `<div style="position: absolute; top: 0; width: 36px; height: 36px; border-radius: 50%; background-color: ${colorHex}; opacity: 0.6; animation: ping 1.2s cubic-bezier(0, 0, 0.2, 1) infinite;"></div>`
            : ""
        }
        <div style="position: relative; width: 34px; height: 34px; border-radius: 50%; background-color: #0f172a; border: 2.5px solid ${colorHex}; box-shadow: 0 4px 14px rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; font-size: 16px;">
          🚚
        </div>
        <div style="margin-top: 3px; background-color: rgba(15, 23, 42, 0.95); border: 1px solid #334155; border-radius: 4px; padding: 1px 5px; font-size: 9px; font-weight: 800; color: #f8fafc; font-family: monospace; white-space: nowrap; box-shadow: 0 2px 6px rgba(0,0,0,0.5);">
          ${plateText}
        </div>
      </div>
    `,
  });
};

const createRestStopIcon = (isActive = false, label = "Trạm dừng") => {
  const border = isActive ? "#38bdf8" : "#f59e0b";
  const emoji = isActive ? "🧭" : "⛽";
  return L.divIcon({
    className: "!bg-transparent !border-0",
    iconSize: [44, 52],
    iconAnchor: [22, 26],
    html: `
      <div style="display: flex; flex-direction: column; align-items: center;">
        <div style="width: 32px; height: 32px; border-radius: 10px; background-color: #0f172a; border: 2px solid ${border}; box-shadow: 0 4px 12px rgba(0,0,0,0.65); display: flex; align-items: center; justify-content: center; font-size: 15px;">
          ${emoji}
        </div>
        <div style="margin-top: 3px; background-color: rgba(15, 23, 42, 0.95); border: 1px solid ${border}; border-radius: 4px; padding: 1px 6px; font-size: 9px; font-weight: 800; color: #e2e8f0; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          ${label}
        </div>
      </div>
    `,
  });
};

export default function LeafletMapView({
  vehicles = [],
  selectedVehicle,
  onSelectVehicle,
  mapInstanceRef,
  selectedStop = null,
  isPickingPin = false,
  onPickMapPoint,
}) {
  const mapContainerRef = useRef(null);
  const [map, setMap] = useState(null);
  const layerGroupRef = useRef(null);
  const routeLayerGroupRef = useRef(null);

  // 1. Khởi tạo bản đồ Esri Dark nền tối
  useEffect(() => {
    if (!mapContainerRef.current) return;

    const leafletMap = L.map(mapContainerRef.current, {
      zoomControl: false,
    }).setView([16.0544, 108.2022], 12);

    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      {
        attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ",
        maxZoom: 16,
      },
    ).addTo(leafletMap);

    L.control.zoom({ position: "bottomright" }).addTo(leafletMap);

    const layerGroup = L.layerGroup().addTo(leafletMap);
    const routeLayerGroup = L.layerGroup().addTo(leafletMap);
    layerGroupRef.current = layerGroup;
    routeLayerGroupRef.current = routeLayerGroup;

    setMap(leafletMap);
    if (mapInstanceRef) mapInstanceRef.current = leafletMap;

    const resizeTimer = setTimeout(() => {
      leafletMap.invalidateSize();
    }, 200);

    return () => {
      clearTimeout(resizeTimer);
      leafletMap.remove();
      setMap(null);
      layerGroupRef.current = null;
      routeLayerGroupRef.current = null;
      if (mapInstanceRef) mapInstanceRef.current = null;
    };
  }, [mapInstanceRef]);

  // 2. Xử lý chế độ chấm điểm thủ công trên bản đồ
  useEffect(() => {
    if (!map) return;
    const container = map.getContainer();
    container.style.cursor = isPickingPin ? "crosshair" : "";
    if (!isPickingPin || !onPickMapPoint) return;

    const handleMapClick = (e) => {
      onPickMapPoint(e.latlng.lat, e.latlng.lng);
    };
    map.on("click", handleMapClick);
    return () => {
      container.style.cursor = "";
      map.off("click", handleMapClick);
    };
  }, [map, isPickingPin, onPickMapPoint]);

  // 3. Vẽ các marker phương tiện
  useEffect(() => {
    if (!map || !layerGroupRef.current) return;

    const layerGroup = layerGroupRef.current;
    layerGroup.clearLayers();

    vehicles.forEach((v) => {
      const lat =
        v.lat ?? v.telemetry?.vi_do ?? v.telemetry?.vi_do_gps ?? 16.0544;
      const lng =
        v.lng ?? v.telemetry?.kinh_do ?? v.telemetry?.kinh_do_gps ?? 108.2022;
      const plate = v.plate || v.bien_so || v.bien_so_xe || "Xe IoT";

      const isDanger =
        v.priorityLevel === "DANGER" || v.alertType === "MICROSLEEP";
      const isWarning =
        v.priorityLevel === "WARNING" ||
        v.priorityLevel === "CO2_HIGH" ||
        v.alertType === "CO2_OVERLIMIT";

      const markerColor = isDanger
        ? "#ef4444"
        : isWarning
          ? "#f59e0b"
          : "#10b981";
      const icon = createVehicleIcon(markerColor, isDanger, plate);

      const marker = L.marker([lat, lng], { icon });
      marker.on("click", () => onSelectVehicle && onSelectVehicle(v));
      marker.addTo(layerGroup);
    });
  }, [map, vehicles, onSelectVehicle]);

  // 4. Vẽ tuyến đường OSRM & marker trạm dừng nghỉ được điều hướng / xem trước
  useEffect(() => {
    if (!map || !routeLayerGroupRef.current) return;
    const routeGroup = routeLayerGroupRef.current;
    routeGroup.clearLayers();

    const activeDispatch = selectedVehicle?.activeDispatch;
    if (
      activeDispatch &&
      Array.isArray(activeDispatch.geometry) &&
      activeDispatch.geometry.length >= 2
    ) {
      L.polyline(activeDispatch.geometry, {
        color: "#38bdf8",
        weight: 5,
        opacity: 0.92,
        dashArray: "10, 6",
      }).addTo(routeGroup);

      if (
        Number.isFinite(Number(activeDispatch.lat)) &&
        Number.isFinite(Number(activeDispatch.lng))
      ) {
        const destMarker = L.marker(
          [activeDispatch.lat, activeDispatch.lng],
          {
            icon: createRestStopIcon(true, activeDispatch.restStopName),
          },
        );
        destMarker.bindTooltip(
          `${activeDispatch.restStopName} • ${activeDispatch.distanceKm} km (~${activeDispatch.etaMinutes} phút)`,
          { direction: "top" },
        );
        destMarker.addTo(routeGroup);
      }
    }

    if (
      selectedStop &&
      Number.isFinite(Number(selectedStop.lat)) &&
      Number.isFinite(Number(selectedStop.lng)) &&
      (!activeDispatch ||
        Math.abs(Number(activeDispatch.lat) - Number(selectedStop.lat)) > 0.0005 ||
        Math.abs(Number(activeDispatch.lng) - Number(selectedStop.lng)) > 0.0005)
    ) {
      const previewMarker = L.marker([selectedStop.lat, selectedStop.lng], {
        icon: createRestStopIcon(false, selectedStop.name),
      });
      previewMarker.bindTooltip(
        `${selectedStop.name}${selectedStop.distanceKm != null ? ` (${selectedStop.distanceKm} km)` : ""}`,
        { direction: "top" },
      );
      previewMarker.addTo(routeGroup);
    }
  }, [map, selectedVehicle?.activeDispatch, selectedStop]);

  return (
    <div className="card-glass border-brand-border relative flex h-162.5 flex-col overflow-hidden rounded-3xl border shadow-2xl lg:col-span-7">
      {/* Badge lộ trình */}
      <div className="border-brand-border-dark bg-brand-dark/90 absolute top-4 left-4 z-20 flex items-center gap-2 rounded-2xl border px-3 py-1.5 text-xs shadow-xl backdrop-blur-md">
        <span className="bg-brand-primary rounded px-1.5 py-0.5 font-mono text-[10px] font-black text-white">
          CT01
        </span>
        <span className="text-brand-text-primary font-bold">
          Cao tốc Bắc - Nam
        </span>
        <span className="text-brand-text-subtle">|</span>
        <span className="text-alert-safe flex items-center gap-1 text-[11px] font-semibold">
          <span className="h-2 w-2 rounded-full bg-emerald-400"></span>
          Thông suốt
        </span>
      </div>

      {isPickingPin && (
        <div className="absolute top-4 right-4 z-20 rounded-xl border border-amber-400/50 bg-amber-950/90 px-3 py-1.5 text-[11px] font-bold text-amber-200 shadow-lg backdrop-blur-md">
          📍 Chạm bất kỳ vị trí nào trên bản đồ để chọn điểm dừng
        </div>
      )}

      {/* Bản đồ Leaflet */}
      <div ref={mapContainerRef} className="z-10 h-full w-full" />

      {/* Dải xe nhanh ở chân bản đồ */}
      <div className="absolute right-3 bottom-3 left-3 z-20">
        <div className="card-glass border-brand-border rounded-2xl border p-2.5 backdrop-blur-lg">
          <p className="text-brand-text-muted mb-2 text-[10px] font-bold tracking-wider uppercase">
            Danh sách phương tiện đang giám sát ({vehicles.length})
          </p>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {vehicles.map((v) => {
              const plate = v.plate || v.bien_so || v.bien_so_xe;
              const isSelected =
                (selectedVehicle?.plate ||
                  selectedVehicle?.bien_so ||
                  selectedVehicle?.bien_so_xe) === plate;
              return (
                <button
                  key={v.id}
                  onClick={() => onSelectVehicle && onSelectVehicle(v)}
                  className={`flex shrink-0 items-center gap-2 rounded-xl border px-3 py-1.5 text-left transition-all ${
                    isSelected
                      ? "border-brand-primary bg-brand-primary/25 text-white shadow-md shadow-blue-500/20"
                      : "border-brand-border-dark bg-brand-dark/70 text-brand-text-muted hover:border-brand-border"
                  }`}
                >
                  <span className="text-xs">
                    {v.priorityLevel === "DANGER"
                      ? "🔴"
                      : v.priorityLevel === "WARNING"
                        ? "🟡"
                        : "🟢"}
                  </span>
                  <div>
                    <p className="font-mono text-xs leading-tight font-bold text-white">
                      {plate}
                    </p>
                    <p className="text-brand-text-subtle max-w-22.5 truncate text-[10px]">
                      {v.driver || v.driverName || v.tai_xe_hien_tai}
                    </p>
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
