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

const createRestStopIcon = (stopName, kmRemaining, etaMinutes) => {
  return L.divIcon({
    className: "!bg-transparent !border-0",
    iconSize: [160, 60],
    iconAnchor: [80, 50],
    html: `
      <div style="display: flex; flex-direction: column; align-items: center; cursor: pointer;">
        <div style="background: linear-gradient(135deg, #059669, #047857); border: 2px solid #34d399; border-radius: 9999px; padding: 4px 10px; box-shadow: 0 4px 16px rgba(16, 185, 129, 0.5); display: flex; align-items: center; gap: 5px; color: white; font-weight: 800; font-size: 11px;">
          <span>🛑 Trạm Nghỉ</span>
          <span style="background: rgba(0,0,0,0.3); padding: 1px 6px; border-radius: 6px; font-family: monospace; font-size: 10px; color: #a7f3d0;">Còn ${kmRemaining} km</span>
        </div>
        <div style="width: 0; height: 0; border-left: 6px solid transparent; border-right: 6px solid transparent; border-top: 6px solid #047857; margin-top: -1px;"></div>
        <div style="margin-top: 2px; font-size: 10px; font-weight: 700; color: #6ee7b7; text-shadow: 0 2px 4px rgba(0,0,0,0.9); white-space: nowrap;">
          ${stopName} (~${etaMinutes}p)
        </div>
      </div>
    `,
  });
};

const createOnsetMarkerIcon = (timeText, initialKm) => {
  return L.divIcon({
    className: "!bg-transparent !border-0",
    iconSize: [150, 50],
    iconAnchor: [75, 45],
    html: `
      <div style="display: flex; flex-direction: column; align-items: center; cursor: pointer;">
        <div style="background: #991b1b; border: 2px solid #f87171; border-radius: 8px; padding: 3px 8px; box-shadow: 0 4px 14px rgba(239, 68, 68, 0.6); display: flex; align-items: center; gap: 4px; color: white; font-size: 10px; font-weight: 800;">
          <span>😴 Bắt đầu buồn ngủ</span>
        </div>
        <div style="width: 0; height: 0; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 5px solid #991b1b;"></div>
        <div style="font-size: 9px; color: #fca5a5; font-weight: 700; text-shadow: 0 1px 3px rgba(0,0,0,0.9); margin-top: 2px;">
          Cách trạm ban đầu: ${initialKm || '5.8'} km
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
}) {
  const mapContainerRef = useRef(null);
  const [map, setMap] = useState(null);
  const layerGroupRef = useRef(null);

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

    // Tạo khay chứa LayerGroup gắn trực tiếp vào bản đồ
    const layerGroup = L.layerGroup().addTo(leafletMap);
    layerGroupRef.current = layerGroup;

    setMap(leafletMap);
    if (mapInstanceRef) mapInstanceRef.current = leafletMap;

    // Ép bản đồ tính toán lại kích thước khung hình sau khi CSS vẽ xong
    const resizeTimer = setTimeout(() => {
      leafletMap.invalidateSize();
    }, 200);

    return () => {
      clearTimeout(resizeTimer);
      leafletMap.remove();
      setMap(null);
      layerGroupRef.current = null;
      if (mapInstanceRef) mapInstanceRef.current = null;
    };
  }, [mapInstanceRef]);

  // 2. Vẽ và cập nhật chấm xe lên LayerGroup (Bảo đảm luôn vẽ đúng bản đồ hiện tại)
  useEffect(() => {
    if (!map || !layerGroupRef.current) return;

    const layerGroup = layerGroupRef.current;
    layerGroup.clearLayers(); // Dọn sạch các marker cũ, không sợ rò rỉ rác

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

    // 3. Vẽ Trạm dừng chân gần nhất, Điểm bắt đầu buồn ngủ & Tuyến đường khẩn cấp
    const activeTarget = selectedVehicle || vehicles.find((v) => v.priorityLevel === "DANGER") || vehicles[0];
    if (activeTarget && activeTarget.nearestRestStop) {
      const vLat = activeTarget.lat ?? 16.0544;
      const vLng = activeTarget.lng ?? 108.2022;

      const stop = activeTarget.nearestRestStop;
      const stopLat = stop.lat || stop.vi_do || 16.1485;
      const stopLng = stop.lng || stop.kinh_do || 108.1322;
      const stopName = stop.name || stop.tenTram || "Trạm Dừng Nghỉ Nam Hải Vân";
      const kmRemaining = stop.kmRemaining ?? stop.kmConLai ?? 4.2;
      const etaMinutes = stop.etaMinutes ?? stop.phutConLai ?? 4;

      // Marker Trạm Dừng Chân
      const restIcon = createRestStopIcon(stopName, kmRemaining, etaMinutes);
      const restMarker = L.marker([stopLat, stopLng], { icon: restIcon }).addTo(layerGroup);
      restMarker.bindPopup(`
        <div style="font-family: sans-serif; min-width: 180px; color: #0f172a; padding: 4px;">
          <h4 style="margin: 0 0 6px; font-size: 13px; font-weight: 800; color: #065f46;">🛑 ${stopName}</h4>
          <p style="margin: 3px 0; font-size: 11px;"><b>Khoảng cách còn lại:</b> <span style="color: #059669; font-weight: bold;">${kmRemaining} km</span></p>
          <p style="margin: 3px 0; font-size: 11px;"><b>Thời gian dự kiến:</b> ~ ${etaMinutes} phút</p>
          <a href="${stop.googleMapsUrl || 'https://www.google.com/maps/dir/?api=1&destination=' + stopLat + ',' + stopLng}" target="_blank" rel="noopener noreferrer" style="display: block; margin-top: 8px; background: #059669; color: white; text-align: center; text-decoration: none; padding: 6px 10px; border-radius: 6px; font-size: 11px; font-weight: bold;">
            🧭 Mở Google Maps chỉ đường
          </a>
        </div>
      `);

      // Đường dẫn khẩn cấp từ Xe -> Trạm dừng chân
      const routeLine = L.polyline(
        [
          [vLat, vLng],
          [stopLat, stopLng],
        ],
        {
          color: activeTarget.priorityLevel === "DANGER" ? "#f59e0b" : "#10b981",
          weight: 4,
          dashArray: "8, 8",
          opacity: 0.85,
        }
      ).addTo(layerGroup);

      routeLine.bindTooltip(
        `🧭 Khoảng cách đến trạm dừng: ${kmRemaining} km (~${etaMinutes} phút)`,
        { permanent: false, direction: "top", className: "bg-slate-900 text-white font-mono text-xs px-2 py-1 rounded shadow" }
      );

      // Điểm bắt đầu buồn ngủ (nếu có)
      const onset = activeTarget.drowsinessOnset;
      if (onset?.lat && onset?.lng) {
        const onsetIcon = createOnsetMarkerIcon(onset.timestamp, onset.initialDistanceKm);
        const onsetMarker = L.marker([onset.lat, onset.lng], { icon: onsetIcon }).addTo(layerGroup);
        onsetMarker.bindPopup(`
          <div style="font-family: sans-serif; color: #0f172a; padding: 4px;">
            <h4 style="margin: 0 0 4px; color: #b91c1c; font-size: 12px; font-weight: 800;">😴 Điểm phát hiện buồn ngủ</h4>
            <p style="margin: 2px 0; font-size: 11px;">Cách trạm ban đầu: <b>${onset.initialDistanceKm || 5.8} km</b></p>
            <p style="margin: 2px 0; font-size: 11px;">Đã chạy kể từ lúc buồn ngủ: <b style="color: #dc2626;">${onset.distanceTraveledKm || 1.2} km</b></p>
          </div>
        `);

        // Đường nối từ điểm phát hiện buồn ngủ tới vị trí xe hiện tại
        L.polyline(
          [
            [onset.lat, onset.lng],
            [vLat, vLng],
          ],
          {
            color: "#ef4444",
            weight: 2.5,
            dashArray: "4, 6",
            opacity: 0.7,
          }
        ).addTo(layerGroup);
      }
    }
  }, [map, vehicles, selectedVehicle, onSelectVehicle]);

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
