import {
  ArrowRight,
  Code2,
  Database,
  Eye,
  Lock,
  Search,
  ShieldAlert,
  UserCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import dataService from "../../services/dataService";
import { formatFullDateTime } from "../../utils/formatDateTime";

export default function AdminAuditLogsPage() {
  const [logs, setLogs] = useState([]);
  const [filterType, setFilterType] = useState("ALL");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedLog, setSelectedLog] = useState(null);

  useEffect(() => {
    dataService.getAuditLogs().then((data) => {
      const list = data || [];
      setLogs(list);
      if (list.length > 0) setSelectedLog(list[0]);
    });
  }, []);

  const filteredLogs = logs.filter((log) => {
    const targetType = (log.doi_tuong_loai || "").toLowerCase();
    if (filterType !== "ALL" && targetType !== filterType.toLowerCase()) {
      return false;
    }
    const query = searchTerm.toLowerCase();
    if (!query) return true;
    const actor = (log.user_name || "").toLowerCase();
    const action = (log.hanh_dong || "").toLowerCase();
    const reason = (log.ly_do || "").toLowerCase();
    return (
      actor.includes(query) ||
      action.includes(query) ||
      reason.includes(query) ||
      String(log.doi_tuong_id || "").includes(query)
    );
  });

  const renderActionBadge = (action = "") => {
    if (action.includes("SUSPEND") || action.includes("CONFIRM")) {
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-rose-500/40 bg-rose-950/50 px-2.5 py-0.5 text-[11px] font-bold text-rose-300">
          {action}
        </span>
      );
    }
    if (action.includes("CONFIG") || action.includes("OVERRIDE")) {
      return (
        <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-950/50 px-2.5 py-0.5 text-[11px] font-bold text-amber-300">
          {action}
        </span>
      );
    }
    return (
      <span className="border-brand-primary/30 bg-brand-blue-night/50 text-brand-blue-sky inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-bold">
        {action}
      </span>
    );
  };

  return (
    <div className="animate-fade-in-up space-y-6 font-sans">
      {/* HEADER */}
      <div className="flex flex-col justify-between gap-3 md:flex-row md:items-center">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400"></span>
            <p className="text-brand-blue-light text-[11px] font-bold tracking-widest uppercase">
              Nhật ký Kiểm toán Bất biến
            </p>
          </div>
          <h1 className="text-brand-text-primary mt-1 text-2xl font-black tracking-tight">
            Tra cứu Audit Log & Đối soát Thay đổi
          </h1>
          <p className="text-brand-text-muted text-xs">
            Phân vùng theo tháng • Quyền CSDL chỉ cho phép INSERT & SELECT
          </p>
        </div>

        <div className="flex items-center gap-2 rounded-2xl border border-emerald-500/30 bg-emerald-950/30 px-3.5 py-2 text-xs font-bold text-emerald-400">
          <Lock className="h-4 w-4" />
          <span>Immutable Ledger: ACTIVE</span>
        </div>
      </div>

      {/* KPI CARDS */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="card-glass border-brand-border flex items-center gap-3.5 rounded-3xl border p-4 shadow-lg">
          <div className="border-brand-primary/30 bg-brand-blue-night/60 text-brand-blue-light flex h-11 w-11 items-center justify-center rounded-2xl border">
            <Database className="h-5 w-5" />
          </div>
          <div>
            <p className="text-brand-text-muted text-[11px] font-semibold">
              Tổng sự kiện ghi vết
            </p>
            <p className="text-brand-text-primary text-xl font-black">
              {logs.length}{" "}
              <span className="text-brand-text-subtle text-xs font-normal">
                bản ghi
              </span>
            </p>
          </div>
        </div>

        <div className="card-glass flex items-center gap-3.5 rounded-3xl border border-rose-500/30 bg-rose-950/15 p-4 shadow-lg">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-rose-500/30 bg-rose-950/40 text-rose-400">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div>
            <p className="text-xs font-bold text-rose-400">
              Thẩm định & Chế tài
            </p>
            <p className="text-brand-text-primary text-xl font-black">
              {
                logs.filter(
                  (l) =>
                    l.doi_tuong_loai === "violation" ||
                    l.doi_tuong_loai === "driver",
                ).length
              }{" "}
              <span className="text-brand-text-subtle text-xs font-normal">
                thao tác
              </span>
            </p>
          </div>
        </div>

        <div className="card-glass flex items-center gap-3.5 rounded-3xl border border-amber-500/30 bg-amber-950/15 p-4 shadow-lg">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-amber-500/30 bg-amber-950/40 text-amber-400">
            <Code2 className="h-5 w-5" />
          </div>
          <div>
            <p className="text-xs font-bold text-amber-400">
              Thay đổi cấu hình AI
            </p>
            <p className="text-brand-text-primary text-xl font-black">
              {logs.filter((l) => l.doi_tuong_loai === "system_configs").length}{" "}
              <span className="text-brand-text-subtle text-xs font-normal">
                lần chỉnh
              </span>
            </p>
          </div>
        </div>

        <div className="card-glass flex items-center gap-3.5 rounded-3xl border border-emerald-500/30 bg-emerald-950/15 p-4 shadow-lg">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-emerald-500/30 bg-emerald-950/40 text-emerald-400">
            <UserCheck className="h-5 w-5" />
          </div>
          <div>
            <p className="text-xs font-bold text-emerald-400">
              Bảo vệ Toàn vẹn
            </p>
            <p className="text-brand-text-primary text-sm font-black">
              INSERT / SELECT ONLY
            </p>
          </div>
        </div>
      </div>

      {/* FILTER BAR */}
      <div className="card-glass border-brand-border flex flex-col justify-between gap-4 rounded-3xl border p-4 shadow-xl sm:flex-row sm:items-center">
        <div className="flex flex-wrap gap-1.5">
          {[
            { key: "ALL", label: "Tất cả" },
            { key: "violation", label: "Vi phạm AI" },
            { key: "driver", label: "Tài xế" },
            { key: "system_configs", label: "Cấu hình hệ thống" },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setFilterType(tab.key)}
              className={`cursor-pointer rounded-xl px-3.5 py-1.5 text-xs font-bold transition-all ${
                filterType === tab.key
                  ? "bg-brand-primary text-white shadow-md shadow-blue-500/25"
                  : "border-brand-border-dark bg-brand-dark/70 text-brand-text-muted hover:border-brand-border border hover:text-white"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-72">
          <Search className="text-brand-text-subtle pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Tìm người thực hiện, mã hành động, lý do..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="border-brand-border bg-brand-dark/90 text-brand-text-primary placeholder:text-brand-text-subtle focus:border-brand-primary w-full rounded-xl border py-2 pr-3 pl-9 text-xs outline-none"
          />
        </div>
      </div>

      {/* DANH SÁCH BẢNG + TRÌNH XEM JSON DIFF */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* BẢNG DANH SÁCH BÊN TRÁI (7 CỘT) */}
        <div className="card-glass border-brand-border rounded-3xl border p-6 shadow-xl lg:col-span-7">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-brand-border-dark bg-brand-dark/70 text-brand-text-muted border-b font-bold uppercase">
                  <th className="rounded-tl-xl px-3.5 py-3.5">Thời gian</th>
                  <th className="px-3.5 py-3.5">Người thực hiện</th>
                  <th className="px-3.5 py-3.5">Hành động & Đối tượng</th>
                  <th className="px-3.5 py-3.5">Lý do / Ghi chú</th>
                  <th className="rounded-tr-xl px-3.5 py-3.5 text-right">
                    JSON Diff
                  </th>
                </tr>
              </thead>
              <tbody className="divide-brand-border-dark/60 divide-y">
                {filteredLogs.length === 0 ? (
                  <tr>
                    <td
                      colSpan={5}
                      className="text-brand-text-subtle py-12 text-center"
                    >
                      Không tìm thấy bản ghi kiểm toán nào phù hợp
                    </td>
                  </tr>
                ) : (
                  filteredLogs.map((log) => {
                    const isSelected = selectedLog?.id === log.id;
                    return (
                      <tr
                        key={log.id}
                        onClick={() => setSelectedLog(log)}
                        className={`cursor-pointer transition-colors ${
                          isSelected
                            ? "bg-brand-blue-night/35"
                            : "hover:bg-brand-surface/70"
                        }`}
                      >
                        <td className="text-brand-text-muted px-3.5 py-3.5 text-[11px] whitespace-nowrap">
                          {log.created_at
                            ? formatFullDateTime(log.created_at)
                            : "Hôm nay"}
                        </td>
                        <td className="px-3.5 py-3.5">
                          <p className="text-brand-text-primary font-bold">
                            {log.user_name || "Hệ thống"}
                          </p>
                          <p className="text-brand-text-subtle text-[10px]">
                            {log.actor_type || "USER"} #{log.user_id || 1}
                          </p>
                        </td>
                        <td className="px-3.5 py-3.5">
                          {renderActionBadge(log.hanh_dong)}
                          <p className="text-brand-text-subtle mt-1 text-[10px]">
                            Bảng: <b>{log.doi_tuong_loai}</b> (ID:{" "}
                            {log.doi_tuong_id})
                          </p>
                        </td>
                        <td className="text-brand-text-secondary max-w-48 truncate px-3.5 py-3.5">
                          {log.ly_do || "--"}
                        </td>
                        <td className="px-3.5 py-3.5 text-right">
                          <button className="text-brand-blue-sky hover:text-brand-primary inline-flex items-center gap-1 font-bold">
                            <Eye className="h-3.5 w-3.5" />
                            <span>So sánh</span>
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* TRÌNH XEM JSON DIFF BÊN PHẢI (5 CỘT) */}
        <div className="card-glass border-brand-border flex flex-col justify-between rounded-3xl border p-6 shadow-xl lg:col-span-5">
          {selectedLog ? (
            <div className="space-y-4">
              <div className="border-brand-border-dark flex items-center justify-between border-b pb-3.5">
                <div>
                  <span className="text-brand-blue-sky text-[10px] font-bold tracking-wider uppercase">
                    Chi tiết biến động dữ liệu
                  </span>
                  <h3 className="text-brand-text-primary mt-0.5 text-sm font-bold">
                    Bản ghi Audit #{selectedLog.id}
                  </h3>
                </div>
                {renderActionBadge(selectedLog.hanh_dong)}
              </div>

              <div className="border-brand-border-dark bg-brand-dark/70 space-y-1.5 rounded-2xl border p-3.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-brand-text-muted">
                    Người thực hiện:
                  </span>
                  <span className="text-brand-text-primary font-bold">
                    {selectedLog.user_name}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-brand-text-muted">
                    Đối tượng tác động:
                  </span>
                  <span className="text-brand-blue-sky font-bold">
                    {selectedLog.doi_tuong_loai} (ID: {selectedLog.doi_tuong_id}
                    )
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-brand-text-muted">Lý do ghi nhận:</span>
                  <span className="text-brand-text-secondary text-right font-medium">
                    {selectedLog.ly_do || "Không có ghi chú"}
                  </span>
                </div>
              </div>

              {/* KHUNG DIFF TRƯỚC & SAU */}
              <div className="space-y-3">
                <div className="rounded-2xl border border-rose-500/30 bg-rose-950/20 p-3.5">
                  <p className="mb-1.5 text-[11px] font-bold text-rose-400 uppercase">
                    Giá trị trước thay đổi
                  </p>
                  <pre className="overflow-x-auto text-[11px] leading-relaxed text-rose-200">
                    {JSON.stringify(selectedLog.gia_tri_truoc || {}, null, 2)}
                  </pre>
                </div>

                <div className="flex justify-center">
                  <div className="border-brand-border bg-brand-surface text-brand-blue-sky flex h-7 w-7 items-center justify-center rounded-full border">
                    <ArrowRight className="h-3.5 w-3.5 rotate-90" />
                  </div>
                </div>

                <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-3.5">
                  <p className="mb-1.5 text-[11px] font-bold text-emerald-400 uppercase">
                    Giá trị sau thay đổi
                  </p>
                  <pre className="overflow-x-auto text-[11px] leading-relaxed text-emerald-200">
                    {JSON.stringify(selectedLog.gia_tri_sau || {}, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-brand-text-subtle py-16 text-center text-xs">
              Chọn một bản ghi bên trái để xem chi tiết JSON Diff
            </div>
          )}

          <div className="border-brand-border-dark text-brand-text-subtle mt-5 border-t pt-3 text-[11px]">
            ✓ Bảo mật theo Nghị định 13/2023/NĐ-CP &bull; Lưu vết IP:{" "}
            <span>{selectedLog?.ip_address || "192.168.1.100"}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
