import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  Edit3,
  Eye,
} from "lucide-react";
import StatusBadge from "../../../components/common/StatusBadge";
import { safeParseJSON } from "../../../mock/mockData";
import { formatFullDateTime } from "../../../utils/formatDateTime";

export default function ShiftsTable({
  shifts = [],
  onSelectShift,
  onSwitchToTripTab,
}) {
  return (
    <div className="card-glass border-brand-border rounded-3xl border p-6 shadow-xl">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-brand-border-dark bg-brand-dark/70 text-brand-text-muted border-b text-xs font-bold tracking-wider uppercase">
              <th className="rounded-tl-2xl px-4 py-4">Mã ca</th>
              <th className="px-4 py-4">Tài xế &amp; Phương tiện</th>
              <th className="px-4 py-4">Lộ trình tuyến đường</th>
              <th className="px-4 py-4">Khung giờ kế hoạch</th>
              <th className="px-4 py-4">Cảnh báo Smart Scheduling</th>
              <th className="px-4 py-4 text-center">Trạng thái kế hoạch</th>
              <th className="rounded-tr-2xl px-4 py-4 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-brand-border-dark/60 divide-y">
            {shifts.map((shift) => {
              const driverName =
                shift.driver?.user?.ho_ten || shift.driver?.ho_ten || "Tài xế";
              const plate =
                shift.vehicle?.bien_so_xe || shift.vehicle?.bien_so || "Xe IoT";
              const vehicleModel = shift.vehicle?.loai_xe || "Xe tải";
              const routeName = shift.route?.ten_tuyen || "Tuyến đường chính";
              const distance = shift.route?.khoang_cach_km || 100;
              const alerts =
                safeParseJSON(shift.canh_bao_khi_tao, shift.canh_bao_khi_tao) ||
                [];
              const isInProgress = shift.trang_thai === "IN_PROGRESS";

              return (
                <tr
                  key={shift.id}
                  onClick={() => onSelectShift && onSelectShift(shift)}
                  className="hover:bg-brand-surface/80 cursor-pointer transition-colors"
                >
                  {/* Mã ca */}
                  <td className="text-brand-blue-sky px-4 py-4 text-xs font-bold">
                    #{shift.id}
                  </td>

                  {/* Tài xế & Xe */}
                  <td className="px-4 py-4">
                    <p className="text-brand-text-primary text-xs font-bold">
                      {driverName}
                    </p>
                    <p className="text-brand-text-subtle text-[11px]">
                      {plate} •{" "}
                      <span className="font-sans">{vehicleModel}</span>
                    </p>
                  </td>

                  {/* Tuyến đường */}
                  <td className="px-4 py-4">
                    <p className="text-brand-text-secondary text-xs font-semibold">
                      {routeName}
                    </p>
                    <p className="text-brand-text-subtle text-[11px]">
                      {distance} km
                    </p>
                  </td>

                  {/* Thời gian dự kiến */}
                  <td className="px-4 py-4 text-xs">
                    <div className="text-brand-text-secondary">
                      Bắt đầu:{" "}
                      <span className="text-brand-text-primary font-bold">
                        {formatFullDateTime(shift.thoi_gian_bat_dau_du_kien)}
                      </span>
                    </div>
                    <div className="text-brand-text-muted mt-0.5">
                      Kết thúc:{" "}
                      <span>
                        {formatFullDateTime(shift.thoi_gian_ket_thuc_du_kien)}
                      </span>
                    </div>
                  </td>

                  {/* Cảnh báo Smart Scheduling */}
                  <td className="px-4 py-4">
                    {alerts.length > 0 ? (
                      <div className="flex max-w-xs flex-col gap-1.5">
                        {alerts.map((al, idx) => (
                          <span
                            key={idx}
                            className="border-alert-warning/30 bg-alert-warning/15 text-alert-warning inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] font-medium"
                          >
                            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                            <span>
                              {typeof al === "string"
                                ? al
                                : al.message || al.ly_do}
                            </span>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-alert-safe inline-flex items-center gap-1 text-xs font-semibold">
                        <CheckCircle2 className="h-3.5 w-3.5" /> Phù hợp thể
                        trạng
                      </span>
                    )}
                  </td>

                  {/* Trạng thái Kế hoạch phân ca */}
                  <td className="px-4 py-4 text-center">
                    <div className="flex flex-col items-center gap-1">
                      <StatusBadge code={shift.trang_thai} />
                    </div>
                  </td>

                  <td className="px-4 py-4 text-right">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectShift && onSelectShift(shift);
                      }}
                      className="border-brand-border bg-brand-surface/80 text-brand-blue-sky hover:border-brand-primary hover:bg-brand-primary hover:text-brand-text-primary inline-flex cursor-pointer items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-bold transition-all"
                    >
                      {isInProgress ? (
                        <>
                          <Eye className="h-3.5 w-3.5" />
                          <span>Chi tiết</span>
                        </>
                      ) : (
                        <>
                          <Edit3 className="h-3.5 w-3.5" />
                          <span>Sửa / Hủy</span>
                        </>
                      )}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
