import { Download } from "lucide-react";

export default function DashboardHeader({
  timeRange,
  setTimeRange,
  onExportReport,
}) {
  return (
    <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
      <div>
        <div className="flex items-center gap-2">
          <span className="flex h-2.5 w-2.5 rounded-full bg-blue-500 shadow-sm shadow-blue-500/50"></span>
          <p className="text-brand-blue-light text-[11px] font-bold tracking-widest uppercase">
            Hệ thống Phân tích Nghiệp vụ (Fleet BI)
          </p>
        </div>
        <h1 className="text-brand-text-primary mt-1 text-2xl font-black tracking-tight">
          Báo cáo An toàn & Hiệu suất Vận hành
        </h1>
        <p className="text-brand-text-muted text-xs">
          Tổng hợp dữ liệu Telemetry, cảnh báo buồn ngủ Edge AI và phân tích
          nhịp sinh học
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2.5">
        {/* Bộ lọc khoảng thời gian */}
        <div className="card-glass border-brand-border flex items-center gap-1 rounded-2xl border p-1 shadow-md">
          {[
            { key: "ALL", label: "Tất cả" },
            { key: "7D", label: "7 ngày qua" },
            { key: "30D", label: "30 ngày" },
            { key: "THIS_MONTH", label: "Tháng này" },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setTimeRange(tab.key)}
              className={`rounded-xl px-3 py-1.5 text-xs font-bold transition-all ${
                timeRange === tab.key
                  ? "bg-brand-primary text-white shadow-md shadow-blue-500/25"
                  : "text-brand-text-muted hover:text-white"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Nút Xuất báo cáo CSV */}
        <button
          onClick={onExportReport}
          className="card-glass border-brand-border hover:border-brand-primary text-brand-text-primary flex cursor-pointer items-center gap-1.5 rounded-2xl border px-3.5 py-2 text-xs font-bold transition-all active:scale-95"
        >
          <Download className="text-brand-blue-sky h-3.5 w-3.5" />
          <span>Xuất CSV</span>
        </button>
      </div>
    </div>
  );
}
