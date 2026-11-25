import {
  ExternalLink,
  Gauge,
  MapPin,
  Navigation,
  PhoneCall,
  Sparkles,
  Video,
  Wind,
} from "lucide-react";

export default function VehicleDispatchPanel({
  vehicle,
  countdownInfo = { mode: "IDLE", label: "Không áp dụng", sub: "" },
  nearbyStops = [],
  selectedStop = null,
  onSelectStop,
  loadingStops = false,
  isPickingPin = false,
  onTogglePickPin,
  dispatching = false,
  onDispatchNav,
  onOpenCallModal,
  onOpenVideoModal,
}) {
  const plate = vehicle?.bien_so_xe || vehicle?.plate || "---";
  const driverName =
    vehicle?.driverName ||
    vehicle?.driver ||
    vehicle?.tai_xe_hien_tai ||
    "Tài xế";
  const speed = vehicle?.currentSpeed ?? parseInt(vehicle?.speed, 10) ?? 0;
  const co2 = vehicle?.currentCo2 ?? vehicle?.co2_ppm ?? 450;
  const isCo2High = Number(co2) > 1000;
  const activeDispatch = vehicle?.activeDispatch || null;

  const countdownBoxStyle =
    countdownInfo.mode === "EXPIRED"
      ? "border-rose-500/60 bg-rose-950/60 text-rose-300 animate-pulse"
      : countdownInfo.mode === "ACTIVE"
        ? "border-amber-500/40 bg-amber-950/40 text-amber-300"
        : countdownInfo.mode === "RESTING"
          ? "border-emerald-500/40 bg-emerald-950/40 text-emerald-300"
          : "border-brand-border-dark bg-brand-dark/70 text-brand-text-muted";

  const countdownTitleColor =
    countdownInfo.mode === "EXPIRED"
      ? "text-rose-400"
      : countdownInfo.mode === "ACTIVE"
        ? "text-amber-400"
        : countdownInfo.mode === "RESTING"
          ? "text-emerald-400"
          : "text-brand-text-subtle";

  return (
    <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-6 shadow-2xl lg:col-span-5">
      <div className="space-y-4">
        {/* Header thông tin xe & Đếm ngược */}
        <div className="border-brand-border-dark flex items-start justify-between gap-2 border-b pb-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-md px-2 py-0.5 text-[10px] font-black tracking-wider uppercase ${
                  vehicle?.priorityLevel === "DANGER"
                    ? "bg-alert-danger animate-pulse text-white"
                    : vehicle?.priorityLevel === "WARNING"
                      ? "bg-amber-500 text-slate-950"
                      : "bg-emerald-500 text-white"
                }`}
              >
                {vehicle?.priorityBadge || "THEO DÕI"}
              </span>
              <span className="bg-brand-surface border-brand-border text-brand-text-muted max-w-56 truncate rounded-md border px-2 py-0.5 text-[10px] font-bold">
                {vehicle?.route || "Tuyến đường chính"}
              </span>
            </div>
            <h2 className="text-brand-text-primary mt-1.5 font-mono text-3xl font-black tracking-tight">
              {plate}
            </h2>
            <p className="text-brand-text-muted text-xs">
              {vehicle?.model || vehicle?.loai_xe}
            </p>
          </div>

          <div className={`shrink-0 rounded-xl border p-2.5 text-right ${countdownBoxStyle}`}>
            <p className={`text-[10px] font-bold tracking-wider uppercase ${countdownTitleColor}`}>
              Thời hạn dừng xe
            </p>
            <p className="font-mono text-base font-black">{countdownInfo.label}</p>
            {countdownInfo.sub && (
              <p className="text-[10px] opacity-80">{countdownInfo.sub}</p>
            )}
          </div>
        </div>

        {/* Thông tin tài xế & Điểm uy tín */}
        <div className="border-brand-border-dark bg-brand-dark/70 flex items-center justify-between rounded-2xl border p-3.5">
          <div className="flex items-center gap-3">
            <div className="from-brand-primary to-brand-primary-dark border-brand-primary/40 flex h-10 w-10 items-center justify-center rounded-xl border bg-linear-to-br font-bold text-white shadow-md">
              {driverName.charAt(0)}
            </div>
            <div>
              <p className="text-brand-text-primary text-xs font-bold">
                {driverName}
              </p>
              <p className="text-brand-text-subtle text-[11px]">
                Mã: {vehicle?.driverCode || "TX-0102"} • KN:{" "}
                {vehicle?.experience || "5 năm"}
              </p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-brand-text-subtle text-[10px] font-semibold">
              Điểm uy tín
            </p>
            <p className="text-brand-blue-sky font-mono text-base font-black">
              {vehicle?.score ?? vehicle?.diem_uy_tin ?? 100}/100
            </p>
          </div>
        </div>

        {/* Đồng hồ Telemetry */}
        <div className="grid grid-cols-2 gap-3">
          <div className="border-brand-border-dark bg-brand-surface/50 rounded-2xl border p-3">
            <div className="text-brand-text-muted flex items-center gap-1.5 text-[11px]">
              <Gauge className="h-3.5 w-3.5 text-blue-400" /> Vận tốc xe
            </div>
            <p className="text-brand-text-primary mt-1 font-mono text-lg font-black">
              {speed}{" "}
              <span className="text-brand-text-muted text-xs font-normal">
                km/h
              </span>
            </p>
          </div>

          <div
            className={`rounded-2xl border p-3 ${
              isCo2High
                ? "border-alert-danger/40 bg-rose-950/25"
                : "border-brand-border-dark bg-brand-surface/50"
            }`}
          >
            <div
              className={`flex items-center gap-1.5 text-[11px] font-semibold ${
                isCo2High ? "text-alert-danger" : "text-brand-text-muted"
              }`}
            >
              <Wind className="h-3.5 w-3.5" /> CO2 Cabin (MQ-135)
            </div>
            <p
              className={`mt-1 font-mono text-lg font-black ${
                isCo2High ? "text-alert-danger" : "text-brand-text-primary"
              }`}
            >
              {co2} <span className="text-xs font-normal">ppm</span>
            </p>
          </div>
        </div>

        {/* Cảnh báo AI chi tiết */}
        <div className="border-brand-border-dark bg-brand-dark/50 rounded-2xl border p-3 text-xs">
          <div className="flex items-center gap-1.5">
            <Sparkles className="text-brand-blue-sky h-3.5 w-3.5" />
            <p className="text-brand-text-primary font-bold">
              Sự kiện: {vehicle?.alertType || "NORMAL"}
            </p>
          </div>
          <p className="text-brand-text-muted mt-1 text-[11px] leading-relaxed">
            {vehicle?.alertDesc ||
              "Phương tiện đang trong trạng thái theo dõi an toàn."}
          </p>
        </div>

        {/* Gợi ý tự động điểm dừng gần nhất quanh tọa độ GPS xe */}
        <div className="border-brand-border-dark bg-brand-dark/45 space-y-2 rounded-2xl border p-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-brand-text-primary flex items-center gap-1.5 text-[11px] font-bold">
              <MapPin className="h-3.5 w-3.5 text-amber-400" />
              Điểm dừng gần xe (OSM + Tuyến)
            </p>
            <button
              type="button"
              onClick={onTogglePickPin}
              className={`cursor-pointer rounded-lg border px-2 py-0.5 text-[10px] font-bold transition ${
                isPickingPin
                  ? "border-amber-400 bg-amber-500/20 text-amber-300"
                  : "border-brand-border bg-brand-surface text-brand-text-muted hover:text-white"
              }`}
            >
              {isPickingPin ? "Hủy chấm map" : "📍 Chấm trên map"}
            </button>
          </div>

          {loadingStops ? (
            <p className="text-brand-text-subtle py-1 text-[11px] italic">
              Đang quét trạm dừng & cây xăng gần tọa độ GPS hiện tại...
            </p>
          ) : nearbyStops.length === 0 ? (
            <p className="text-brand-text-subtle py-1 text-[11px] italic">
              Chưa tìm thấy điểm dừng quanh tọa độ hiện tại. Bạn có thể bấm "Chấm trên map".
            </p>
          ) : (
            <div className="space-y-1.5">
              {nearbyStops.map((stop) => {
                const active = selectedStop?.id === stop.id;
                return (
                  <button
                    key={stop.id}
                    type="button"
                    onClick={() => onSelectStop && onSelectStop(stop)}
                    className={`flex w-full cursor-pointer items-center justify-between gap-2 rounded-xl border px-2.5 py-2 text-left transition ${
                      active
                        ? "border-brand-primary bg-brand-primary/15 text-white"
                        : "border-brand-border-dark bg-brand-surface/40 text-brand-text-muted hover:border-brand-border"
                    }`}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-[11px] font-bold text-white">
                        {stop.name}
                      </p>
                      <p className="text-brand-text-subtle text-[10px]">
                        {stop.categoryLabel}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="text-brand-blue-sky font-mono text-[11px] font-bold">
                        {stop.distanceKm != null ? `${stop.distanceKm} km` : "Tùy chọn"}
                      </p>
                      <span className="text-[9px] font-semibold uppercase opacity-75">
                        {stop.source === "OSM_OVERPASS"
                          ? "OSM"
                          : stop.source === "MANUAL_PIN"
                            ? "Map"
                            : "Tuyến"}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {activeDispatch && (
            <div className="mt-1 flex items-center justify-between gap-2 rounded-xl border border-sky-500/40 bg-sky-950/40 px-2.5 py-2 text-[11px]">
              <div className="min-w-0">
                <p className="truncate font-bold text-sky-300">
                  Đang dẫn: {activeDispatch.restStopName}
                </p>
                <p className="text-sky-200/80 text-[10px]">
                  {activeDispatch.distanceKm} km • ~{activeDispatch.etaMinutes} phút ({activeDispatch.routeSource})
                </p>
              </div>
              {activeDispatch.googleMapsUrl && (
                <a
                  href={activeDispatch.googleMapsUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-sky-500/20 px-2 py-1 text-[10px] font-bold text-sky-300 hover:bg-sky-500/30"
                >
                  Google Maps <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Cụm nút thao tác */}
      <div className="mt-4 space-y-2.5">
        <button
          onClick={onDispatchNav}
          disabled={dispatching || !vehicle}
          className="btn-nav-active flex w-full cursor-pointer items-center justify-center gap-2 rounded-2xl py-3 text-xs font-black tracking-wider text-white uppercase transition-all hover:brightness-110 active:scale-[0.98] disabled:opacity-60"
        >
          <Navigation className="h-4 w-4" />
          <span className="truncate px-2">
            {dispatching
              ? "Đang tính tuyến OSRM..."
              : `Điều hướng (${selectedStop?.name || vehicle?.nearestRestStop?.name || "Trạm gần nhất"})`}
          </span>
        </button>

        <div className="grid grid-cols-2 gap-2.5">
          <button
            onClick={onOpenCallModal}
            className="bg-alert-warning flex cursor-pointer items-center justify-center gap-2 rounded-xl py-2.5 text-xs font-bold text-white shadow-md transition-all hover:bg-amber-600 active:scale-[0.98]"
          >
            <PhoneCall className="h-3.5 w-3.5" /> Gọi Buồng Lái
          </button>
          <button
            onClick={onOpenVideoModal}
            className="card-glass border-brand-border hover:border-brand-border-active text-brand-text-primary flex cursor-pointer items-center justify-center gap-2 rounded-xl border py-2.5 text-xs font-bold transition-all active:scale-[0.98]"
          >
            <Video className="text-brand-blue-sky h-3.5 w-3.5" /> Clip AI 10s
          </button>
        </div>
      </div>
    </div>
  );
}
