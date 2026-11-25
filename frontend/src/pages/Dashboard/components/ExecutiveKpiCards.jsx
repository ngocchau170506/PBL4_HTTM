import {
  AlertOctagon,
  HeartPulse,
  Route,
  ShieldCheck,
} from "lucide-react";

export default function ExecutiveKpiCards({
  drivers = [],
  violations = [],
  trips = [],
}) {
  const suspendedCount = drivers.filter(
    (d) => (d.diem_uy_tin ?? 100) <= 20 || d.cap_canh_bao === "SUSPENDED_RED",
  ).length;

  const safeDriversCount = drivers.filter(
    (d) => (d.diem_uy_tin ?? 100) > 65 && d.cap_canh_bao !== "SUSPENDED_RED",
  ).length;

  const avgSafetyRate = drivers.length
    ? (
        drivers.reduce((sum, d) => sum + (d.diem_uy_tin ?? 100), 0) /
        drivers.length
      ).toFixed(1)
    : "0.0";

  const monitoredTrips = trips.filter((t) =>
    ["IN_PROGRESS", "RESTING", "COMPLETED"].includes(
      t.trang_thai || t.trangThai,
    ),
  );
  const totalDistanceKm = Math.round(
    monitoredTrips.reduce(
      (sum, t) =>
        sum + Number(t.route?.khoang_cach_km ?? t.route?.khoangCachKm ?? 0),
      0,
    ),
  );

  const totalInterventions = violations.length;
  const pendingReviewCount = violations.filter(
    (v) =>
      (v.trang_thai_xac_thuc || v.trang_thai) === "PENDING_MANUAL_REVIEW",
  ).length;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <p className="text-brand-text-muted text-xs font-bold tracking-wider uppercase">
            Chỉ số An toàn Toàn đội
          </p>
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-emerald-500/30 bg-emerald-950/40 text-emerald-400">
            <ShieldCheck className="h-5 w-5" />
          </div>
        </div>
        <div className="mt-4">
          <div className="flex items-baseline gap-2">
            <p className="text-brand-text-primary text-3xl font-black">
              {avgSafetyRate}%
            </p>
            <span className="text-xs font-bold text-emerald-400">
              {safeDriversCount}/{drivers.length} an toàn
            </span>
          </div>
          <p className="text-brand-text-subtle mt-1 text-[11px]">
            Trung bình uy tín {drivers.length} tài xế • Chuẩn ISO-39001
          </p>
        </div>
      </div>

      <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <p className="text-brand-text-muted text-xs font-bold tracking-wider uppercase">
            Tổng Quãng đường Giám sát
          </p>
          <div className="border-brand-primary/30 bg-brand-blue-night/60 text-brand-blue-light flex h-9 w-9 items-center justify-center rounded-xl border">
            <Route className="h-5 w-5" />
          </div>
        </div>
        <div className="mt-4">
          <p className="text-brand-text-primary text-3xl font-black">
            {totalDistanceKm.toLocaleString("vi-VN")}
          </p>
          <p className="text-brand-text-subtle mt-1 text-[11px]">
            Kilomet từ {monitoredTrips.length} chuyến đang chạy / đã hoàn thành
          </p>
        </div>
      </div>

      <div className="card-glass flex flex-col justify-between rounded-3xl border border-amber-500/30 bg-amber-950/15 p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <p className="text-xs font-bold tracking-wider text-amber-400 uppercase">
            Can thiệp rủi ro Tức thời
          </p>
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-amber-500/30 bg-amber-500/20 text-amber-400">
            <HeartPulse className="h-5 w-5" />
          </div>
        </div>
        <div className="mt-4">
          <div className="flex items-baseline gap-2">
            <p className="text-3xl font-black text-amber-300">
              {totalInterventions}
            </p>
            <span className="text-xs font-bold text-amber-400/90">
              vụ ({pendingReviewCount} chờ duyệt)
            </span>
          </div>
          <p className="mt-1 text-[11px] text-amber-200/70">
            Tổng sự kiện vi phạm &amp; cảnh báo từ hệ thống Edge AI
          </p>
        </div>
      </div>

      <div className="card-glass flex flex-col justify-between rounded-3xl border border-rose-500/30 bg-rose-950/15 p-5 shadow-xl">
        <div className="flex items-center justify-between">
          <p className="text-xs font-bold tracking-wider text-rose-400 uppercase">
            Tài xế Nguy cơ cao
          </p>
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-rose-500/30 bg-rose-500/20 text-rose-400">
            <AlertOctagon className="h-5 w-5" />
          </div>
        </div>
        <div className="mt-4">
          <div className="flex items-baseline gap-2">
            <p className="text-3xl font-black text-rose-400">
              {String(suspendedCount).padStart(2, "0")}
            </p>
            <span className="text-xs font-bold text-rose-300">
              Khóa (&le; 20đ)
            </span>
          </div>
          <p className="mt-1 text-[11px] text-rose-200/70">
            Đang đình chỉ nhận ca &amp; chờ đào tạo lại
          </p>
        </div>
      </div>
    </div>
  );
}
