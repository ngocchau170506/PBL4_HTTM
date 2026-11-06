import { Plus, Search } from "lucide-react";

export default function VehiclesFilterBar({
  searchTerm,
  setSearchTerm,
  statusFilter,
  setStatusFilter,
  onOpenAddModal,
}) {
  const tabs = [
    { key: "ALL", label: "Tất cả" },
    { key: "IN_USE", label: "Đang chạy" },
    { key: "AVAILABLE", label: "Sẵn sàng" },
    { key: "MAINTENANCE", label: "Bảo dưỡng" },
  ];

  return (
    <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
      {/* Cụm Tabs lọc */}
      <div className="card-glass border-brand-border flex flex-wrap gap-1 rounded-2xl border p-1 shadow-md">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setStatusFilter(tab.key)}
            className={`rounded-xl px-3.5 py-1.5 text-xs font-bold transition-all ${
              statusFilter === tab.key
                ? "bg-brand-primary text-white shadow-md shadow-blue-500/25"
                : "text-brand-text-muted hover:text-white"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tìm kiếm & Nút Thêm xe */}
      <div className="flex items-center gap-3">
        <div className="relative w-full sm:w-64">
          <Search className="text-brand-text-subtle pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Tìm biển số, loại xe..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="border-brand-border bg-brand-dark/90 text-brand-text-primary placeholder:text-brand-text-subtle focus:border-brand-primary w-full rounded-xl border py-2 pr-3 pl-9 text-xs transition-all outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <button
          onClick={onOpenAddModal}
          className="bg-brand-primary hover:bg-brand-primary-hover flex shrink-0 cursor-pointer items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-bold text-white shadow-lg shadow-blue-500/25 transition-all hover:-translate-y-0.5 active:scale-95"
        >
          <Plus className="h-4 w-4" />
          <span>Thêm phương tiện</span>
        </button>
      </div>
    </div>
  );
}
