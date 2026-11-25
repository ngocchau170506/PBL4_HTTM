import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import dataService from "../../services/dataService";
import { useFleetStore } from "../../store/useFleetStore";
import CircadianRiskChart from "./components/CircadianRiskChart";
import DashboardHeader from "./components/DashboardHeader";
import DriverPerformanceCard from "./components/DriverPerformanceCard";
import ExecutiveKpiCards from "./components/ExecutiveKpiCards";

const isWithinRange = (rawDate, range) => {
  if (range === "ALL" || !rawDate) return true;
  const d = new Date(rawDate);
  if (Number.isNaN(d.getTime())) return true;
  const now = new Date();
  if (range === "7D") return now.getTime() - d.getTime() <= 7 * 86400000;
  if (range === "30D") return now.getTime() - d.getTime() <= 30 * 86400000;
  if (range === "THIS_MONTH") {
    return (
      d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()
    );
  }
  return true;
};

export default function DashboardPage() {
  const drivers = useFleetStore((state) => state.drivers);
  const violations = useFleetStore((state) => state.violations);
  const [trips, setTrips] = useState([]);
  const [timeRange, setTimeRange] = useState("ALL");

  useEffect(() => {
    dataService
      .getTrips()
      .then((list) => setTrips(Array.isArray(list) ? list : []))
      .catch(() => setTrips([]));
  }, [violations.length, drivers.length]);

  const filteredViolations = useMemo(
    () =>
      (violations || []).filter((v) =>
        isWithinRange(
          v.thoi_gian_chot_vi_pham || v.thoi_gian_vi_pham || v.created_at,
          timeRange,
        ),
      ),
    [violations, timeRange],
  );

  const filteredTrips = useMemo(
    () =>
      (trips || []).filter((t) =>
        isWithinRange(t.thoi_gian_bat_dau || t.created_at || t.createdAt, timeRange),
      ),
    [trips, timeRange],
  );

  const handleExportCsv = () => {
    const header = [
      "ID",
      "Thoi gian",
      "Bien so xe",
      "Tai xe",
      "Loai vi pham",
      "Trang thai",
      "Diem tru",
    ];
    const escapeCell = (val) =>
      `"${String(val ?? "").replace(/"/g, '""')}"`;
    const rows = filteredViolations.map((v) => [
      v.id,
      v.thoi_gian_chot_vi_pham || v.thoi_gian_vi_pham || "",
      v.vehicle?.bien_so_xe || v.trip?.vehicle?.bien_so_xe || "",
      v.driver?.user?.ho_ten || v.driver_name || "",
      v.ten_loai || v.ma_loai || "",
      v.trang_thai_xac_thuc || v.trang_thai || "",
      v.diem_bi_tru ?? v.diem_tru ?? 0,
    ]);
    const csvContent =
      "\uFEFF" + [header, ...rows].map((r) => r.map(escapeCell).join(",")).join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute(
      "download",
      `Bao_cao_An_toan_PBL4_${new Date().toISOString().slice(0, 10)}.csv`,
    );
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    toast.success(
      `Đã xuất báo cáo CSV (${filteredViolations.length} sự kiện vi phạm)!`,
      { icon: "📊" },
    );
  };

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* 1. Header Báo cáo & Nút Xuất CSV */}
      <DashboardHeader
        timeRange={timeRange}
        setTimeRange={setTimeRange}
        onExportReport={handleExportCsv}
      />

      {/* 2. 4 Thẻ KPI Cấp Quản Lý */}
      <ExecutiveKpiCards
        drivers={drivers}
        violations={filteredViolations}
        trips={filteredTrips}
      />

      {/* 3. Biểu đồ Đường Cong Rủi Ro Sinh Học 24 Giờ */}
      <CircadianRiskChart violations={filteredViolations} />

      {/* 4. Danh Sách Tài Xế Gương Mẫu vs Cần Can Thiệp */}
      <DriverPerformanceCard drivers={drivers} />
    </div>
  );
}
