import { Award, Gauge, Wind } from "lucide-react";
import { formatTimeOnly } from "../../../utils/formatDateTime";

export default function RealTripsTable({ trips = [] }) {
  return (
    <div className="card-glass border-brand-border rounded-3xl border p-6 shadow-xl">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-brand-border-dark bg-brand-dark/70 text-brand-text-muted border-b text-xs font-bold tracking-wider uppercase">
              <th className="rounded-tl-2xl px-4 py-4">Mã Chuyến</th>
              <th className="px-4 py-4">Tài xế &amp; Phương tiện</th>
              <th className="px-4 py-4">Hành trình thực tế</th>
              <th className="px-4 py-4">Chỉ số Telemetry hiện thời</th>
              <th className="px-4 py-4 text-center">Trạng thái chuyến</th>
              <th className="rounded-tr-2xl px-4 py-4 text-center">
                Xét Thưởng Điểm
              </th>
            </tr>
          </thead>
          <tbody className="divide-brand-border-dark/60 divide-y">
            {trips.map((trip) => {
              const driverName = trip.driver?.user?.ho_ten || "Lê Minh Tuấn";
              const plate = trip.vehicle?.bien_so_xe || "43C-123.45";
              const routeName = trip.route?.ten_tuyen || "Đà Nẵng - Huế";
              const tele = trip.latest_telemetry || {};
              const isProgress = trip.trang_thai === "IN_PROGRESS";

              return (
                <tr
                  key={trip.id}
                  className="hover:bg-brand-surface/80 transition-colors"
                >
                  {/* Mã Chuyến */}
                  <td className="text-brand-blue-sky px-4 py-4 text-xs font-bold">
                    #{trip.id}
                    <div className="text-brand-text-subtle text-[10px]">
                      Ca #{trip.shift_id}
                    </div>
                  </td>

                  {/* Tài xế & Xe */}
                  <td className="px-4 py-4">
                    <p className="text-brand-text-primary text-xs font-bold">
                      {driverName}
                    </p>
                    <p className="text-brand-text-subtle text-[11px]">
                      {plate}
                    </p>
                  </td>

                  {/* Tuyến đường & Giờ xuất phát */}
                  <td className="px-4 py-4 text-xs">
                    <p className="text-brand-text-secondary font-semibold">
                      {routeName}
                    </p>
                    <p className="text-brand-text-subtle mt-0.5">
                      Bắt đầu:{" "}
                      <span className="text-slate-300">
                        {formatTimeOnly(trip.thoi_gian_bat_dau)}
                      </span>
                    </p>
                  </td>

                  {/* Telemetry hiện thời */}
                  <td className="px-4 py-4">
                    {tele.toc_do_kmh !== undefined ? (
                      <div className="flex items-center gap-3 text-xs">
                        <span className="text-brand-text-primary flex items-center gap-1 font-bold">
                          <Gauge className="h-3.5 w-3.5 text-blue-400" />
                          {tele.toc_do_kmh} km/h
                        </span>
                        <span
                          className={`flex items-center gap-1 font-bold ${
                            tele.co2_ppm > 1000
                              ? "text-rose-400"
                              : "text-emerald-400"
                          }`}
                        >
                          <Wind className="h-3.5 w-3.5" />
                          {tele.co2_ppm} ppm
                        </span>
                      </div>
                    ) : (
                      <span className="text-brand-text-subtle text-xs italic">
                        Đã về bãi
                      </span>
                    )}
                  </td>

                  {/* Trạng thái chuyến */}
                  <td className="px-4 py-4 text-center">
                    {isProgress ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-950/50 px-3 py-1 text-xs font-bold text-emerald-400">
                        <span className="h-2 w-2 animate-ping rounded-full bg-emerald-400"></span>
                        Đang lăn bánh
                      </span>
                    ) : (
                      <span className="border-brand-border bg-brand-surface text-brand-text-muted rounded-full border px-3 py-1 text-xs font-semibold">
                        Hoàn thành
                      </span>
                    )}
                  </td>

                  {/* Xét thưởng điểm sau chuyến (SRS § 5.3) */}
                  <td className="px-4 py-4 text-center">
                    {isProgress ? (
                      <span className="text-brand-text-subtle text-[11px] italic">
                        Đang giám sát sạch lỗi
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/30 bg-emerald-950/40 px-2.5 py-1 text-xs font-bold text-emerald-400">
                        <Award className="h-3.5 w-3.5 text-emerald-400" />
                        +5đ Thưởng An toàn
                      </span>
                    )}
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
