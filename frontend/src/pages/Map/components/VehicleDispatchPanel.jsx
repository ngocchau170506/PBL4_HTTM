import {
  Gauge,
  MapPin,
  ExternalLink,
  Navigation,
  PhoneCall,
  Sparkles,
  Video,
  Wind,
} from "lucide-react";

export default function VehicleDispatchPanel({
  vehicle,
  countdownText,
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
  const speed = vehicle?.currentSpeed ?? vehicle?.speed ?? 60;
  const co2 = vehicle?.currentCo2 ?? vehicle?.co2 ?? 850;
  const isCo2High = Number(co2) > 1000;

  return (
    <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-6 shadow-2xl lg:col-span-5">
      <div className="space-y-4">
        {/* Header thông tin xe & Đếm ngược */}
        <div className="border-brand-border-dark flex items-start justify-between border-b pb-4">
          <div>
            <div className="flex items-center gap-2">
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
              <span className="bg-brand-surface border-brand-border text-brand-text-muted rounded-md border px-2 py-0.5 text-[10px] font-bold">
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

          <div className="rounded-xl border border-amber-500/30 bg-amber-950/40 p-2.5 text-right">
            <p className="text-[10px] font-bold tracking-wider text-amber-400 uppercase">
              Thời hạn dừng xe
            </p>
            <p className="font-mono text-lg font-black text-amber-300">
              {countdownText}
            </p>
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
              {vehicle?.score || vehicle?.diem_uy_tin || "88"}/100
              {/* <span className="text-brand-text-subtle text-xs font-normal">
                /100
              </span> */}
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
        <div className="border-brand-border-dark bg-brand-dark/50 rounded-2xl border p-3.5 text-xs">
          <div className="flex items-center gap-1.5">
            <Sparkles className="text-brand-blue-sky h-3.5 w-3.5" />
            <p className="text-brand-text-primary font-bold">
              Sự kiện: {vehicle?.alertType || "MICROSLEEP"}
            </p>
          </div>
          <p className="text-brand-text-muted mt-1.5 text-[11px] leading-relaxed">
            {vehicle?.alertDesc ||
              "Mô hình Edge AI phát hiện dấu hiệu buồn ngủ kéo dài hoặc nồng độ khí cabin vượt ngưỡng an toàn."}
          </p>
        </div>

        {/* Khối Thông tin Trạm dừng chân gần nhất tính từ lúc buồn ngủ */}
        <div className="border border-emerald-500/30 bg-emerald-950/20 rounded-2xl p-3.5 space-y-2.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-emerald-400 font-bold text-xs">
              <MapPin className="h-4 w-4" />
              <span>Trạm Dừng Chân Gần Nhất</span>
            </div>
            <span className="bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 font-mono text-[10px] font-bold px-2 py-0.5 rounded-full">
              ~{vehicle?.nearestRestStop?.etaMinutes ?? 4} phút lái xe
            </span>
          </div>

          <div className="bg-brand-dark/85 rounded-xl p-3 border border-brand-border-dark space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-white font-bold text-xs truncate max-w-[210px]">
                {vehicle?.nearestRestStop?.name || vehicle?.nearestRestStop?.tenTram || "Trạm Dừng Nghỉ Nam Hải Vân (Km 22)"}
              </p>
              <div className="text-right">
                <span className="text-emerald-400 font-mono text-base font-black">
                  {vehicle?.nearestRestStop?.kmRemaining ?? 4.2} <span className="text-xs font-normal text-slate-400">km</span>
                </span>
              </div>
            </div>

            <div className="border-t border-slate-800/80 pt-2 grid grid-cols-2 gap-2 text-[11px]">
              <div>
                <span className="text-slate-400 block text-[10px]">Đã chạy từ lúc buồn ngủ:</span>
                <span className="font-mono font-bold text-amber-400">
                  {vehicle?.drowsinessOnset?.distanceTraveledKm ?? 1.2} km
                </span>
              </div>
              <div className="text-right">
                <span className="text-slate-400 block text-[10px]">Khoảng cách ban đầu:</span>
                <span className="font-mono font-bold text-slate-300">
                  {vehicle?.drowsinessOnset?.initialDistanceKm ?? 5.8} km
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Cụm nút thao tác */}
      <div className="mt-5 space-y-2.5">
        <button
          onClick={onDispatchNav}
          className="btn-nav-active flex w-full cursor-pointer items-center justify-center gap-2 rounded-2xl py-3 text-xs font-black tracking-wider text-white uppercase transition-all hover:brightness-110 active:scale-[0.98]"
        >
          <Navigation className="h-4 w-4" />
          Điều hướng Trạm Nghỉ ({vehicle?.nearestRestStop?.name || "Km 227"})
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
