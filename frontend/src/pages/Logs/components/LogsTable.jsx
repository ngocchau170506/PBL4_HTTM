import { FileVideo } from "lucide-react";
import StatusBadge from "../../../components/common/StatusBadge";
import { formatFullDateTime } from "../../../utils/formatDateTime";

export default function LogsTable({ violations = [], onSelectViolation }) {
  return (
    <div className="card-glass border-brand-border rounded-3xl border p-6 shadow-xl">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-brand-border-dark bg-brand-dark/70 text-brand-text-muted border-b text-xs font-bold tracking-wider uppercase">
              <th className="rounded-tl-2xl px-4 py-4">Mã</th>
              <th className="px-4 py-4">Thời gian</th>
              <th className="px-4 py-4">Tài xế &amp; Phương tiện</th>
              <th className="px-4 py-4">Hành vi phát hiện</th>
              <th className="px-4 py-4 text-center">Độ tin cậy AI</th>
              <th className="px-4 py-4 text-center">Mức trừ</th>
              <th className="px-4 py-4">Trạng thái</th>
              <th className="rounded-tr-2xl px-4 py-4 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-brand-border-dark/60 divide-y">
            {violations.length === 0 ? (
              <tr>
                <td
                  colSpan={8}
                  className="text-brand-text-subtle py-12 text-center text-xs"
                >
                  Không tìm thấy bản ghi vi phạm nào phù hợp
                </td>
              </tr>
            ) : (
              violations.map((v) => {
                const driverName =
                  v.driver?.user?.ho_ten || v.driver_name || "Lê Minh Tuấn";
                const vehiclePlate =
                  v.vehicle?.bien_so_xe ||
                  v.vehicle_plate ||
                  v.trip?.vehicle?.bien_so_xe ||
                  "43C-123.45";
                const eventTime =
                  v.thoi_gian_chot_vi_pham || v.thoi_gian_vi_pham;
                const penalty =
                  v.diem_bi_tru !== undefined ? v.diem_bi_tru : v.diem_tru || 0;
                const status = v.trang_thai_xac_thuc || v.trang_thai;
                const badgeStatusCode =
                  status === "CONFIRMED" && v.nguon_quyet_dinh === "AI"
                    ? "AI_CONFIRMED"
                    : status;
                const score = v.ai_confidence_score;

                return (
                  <tr
                    key={v.id}
                    className="hover:bg-brand-surface/70 transition-colors"
                  >
                    {/* Mã vi phạm */}
                    <td className="text-brand-blue-sky px-4 py-4 text-xs font-bold">
                      #{v.id}
                    </td>

                    {/* Thời gian */}
                    <td className="px-4 py-4 text-xs">
                      <div className="text-brand-text-primary font-bold">
                        {formatFullDateTime(eventTime)}
                      </div>
                      <div className="text-brand-text-subtle text-[10px]">
                        {formatFullDateTime(eventTime)}
                      </div>
                    </td>

                    {/* Tài xế & Xe */}
                    <td className="px-4 py-4">
                      <p className="text-brand-text-primary text-xs font-bold">
                        {driverName}
                      </p>
                      <p className="text-brand-text-subtle text-[11px]">
                        {vehiclePlate} • Chuyến #
                        {v.trip_id || v.trip?.id || "501"}
                      </p>
                    </td>

                    {/* Hành vi phát hiện */}
                    <td className="px-4 py-4">
                      <StatusBadge code={v.ma_loai || v.loai_vi_pham} />
                    </td>

                    {/* Độ tin cậy AI */}
                    <td className="px-4 py-4 text-center">
                      {score !== null && score !== undefined ? (
                        <div>
                          <span
                            className={`text-xs font-bold ${
                              score >= 0.85
                                ? "text-alert-safe"
                                : score >= 0.6
                                  ? "text-alert-warning"
                                  : "text-alert-danger"
                            }`}
                          >
                            {(score * 100).toFixed(0)}%
                          </span>
                          <div className="bg-brand-dark mx-auto mt-1 h-1.5 w-14 overflow-hidden rounded-full">
                            <div
                              className={`h-full ${
                                score >= 0.85
                                  ? "bg-alert-safe"
                                  : score >= 0.6
                                    ? "bg-alert-warning"
                                    : "bg-alert-danger"
                              }`}
                              style={{ width: `${score * 100}%` }}
                            />
                          </div>
                        </div>
                      ) : (
                        <span className="text-brand-text-subtle text-xs italic">
                          Cảm biến
                        </span>
                      )}
                    </td>

                    {/* Mức điểm trừ */}
                    <td className="px-4 py-4 text-center">
                      <span
                        className={`text-xs font-bold ${
                          penalty > 0 ? "text-alert-danger" : "text-alert-safe"
                        }`}
                      >
                        {penalty > 0 ? `-${penalty}` : "0"}
                      </span>
                    </td>

                    {/* Trạng thái xác thực */}
                    <td className="px-4 py-4">
                      <StatusBadge code={badgeStatusCode} />
                    </td>

                    {/* Thao tác */}
                    <td className="px-4 py-4 text-right">
                      <button
                        onClick={() => onSelectViolation(v)}
                        className="border-brand-border-active/40 bg-brand-surface/90 text-brand-blue-sky hover:border-brand-primary hover:bg-brand-primary hover:text-brand-text-primary inline-flex cursor-pointer items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-bold transition-all"
                      >
                        <FileVideo className="h-3.5 w-3.5" />
                        <span>Xem &amp; Thẩm định</span>
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
  );
}
