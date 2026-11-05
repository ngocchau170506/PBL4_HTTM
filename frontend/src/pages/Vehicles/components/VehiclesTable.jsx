import {
  AlertCircle,
  CheckCircle2,
  HardDrive,
  Thermometer,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";
import dataService from "../../../services/dataService";
import StatusBadge from "../../../components/common/StatusBadge";

export default function VehiclesTable({
  vehicles = [],
  onOpenDeviceModal,
  onToggleMaintenance,
}) {
  const [shifts, setShifts] = useState([]);

  useEffect(() => {
    dataService.getShifts().then((data) => setShifts(data || []));
  }, [vehicles]);

  const getActiveDriverInfo = (vehicle) => {
    if (vehicle.tai_xe_hien_tai) {
      return { name: vehicle.tai_xe_hien_tai, shiftInfo: "Ca đang chạy" };
    }
    const activeShift = shifts.find(
      (s) =>
        (s.vehicle_id === vehicle.id || s.vehicle?.id === vehicle.id) &&
        (s.trang_thai === "IN_PROGRESS" || s.trang_thai === "SCHEDULED"),
    );
    if (activeShift) {
      const driverName =
        activeShift.driver?.user?.ho_ten ||
        activeShift.driver?.ho_ten ||
        "Tài xế nhận ca";
      const routeName = activeShift.route?.ten_tuyen || "Đang hành trình";
      return {
        name: driverName,
        shiftInfo: `Ca #${activeShift.id} - ${routeName.split("-")[0]?.trim() || "Tuyến chính"}`,
      };
    }
    return null;
  };

  return (
    <div className="card-glass border-brand-border rounded-3xl border p-6 shadow-xl">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-brand-border-dark bg-brand-dark/70 text-brand-text-muted border-b text-xs font-bold tracking-wider uppercase">
              <th className="rounded-tl-2xl px-4 py-4">Biển số & Dòng xe</th>
              <th className="px-4 py-4">Tải trọng</th>
              <th className="px-4 py-4">Sức khỏe Edge IoT (Pi 4)</th>
              <th className="px-4 py-4">Tài xế / Ca trực</th>
              <th className="px-4 py-4">Trạng thái xe</th>
              <th className="rounded-tr-2xl px-4 py-4 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-brand-border-dark/60 divide-y">
            {vehicles.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="text-brand-text-subtle py-12 text-center text-xs"
                >
                  Không tìm thấy phương tiện nào phù hợp
                </td>
              </tr>
            ) : (
              vehicles.map((v) => {
                const plate = v.bien_so_xe || v.bien_so;
                const iot = v.iot_device || v.iotDevice;
                const isOverheat = iot?.cpu_temp_c && iot.cpu_temp_c >= 70;
                const isMaintenance =
                  v.trang_thai === "MAINTENANCE" ||
                  v.trang_thai === "BAO_DUONG";
                const driverData = getActiveDriverInfo(v);
                return (
                  <tr
                    key={v.id}
                    className="hover:bg-brand-surface/80 transition-colors"
                  >
                    <td className="px-4 py-4">
                      <p className="text-brand-text-primary text-base font-bold tracking-tight">
                        {plate}
                      </p>
                      <p className="text-brand-text-muted text-xs">
                        {v.loai_xe}
                      </p>
                    </td>
                    <td className="text-brand-text-secondary px-4 py-4 text-xs font-semibold">
                      {v.tai_trong_tan
                        ? `${v.tai_trong_tan} Tấn`
                        : v.tai_trong || "--"}
                    </td>
                    <td className="px-4 py-4">
                      {iot ? (
                        <div className="space-y-1.5">
                          <div className="flex items-center gap-2">
                            <span
                              className={`h-2 w-2 rounded-full ${
                                iot.trang_thai_ket_noi === "ONLINE"
                                  ? "bg-emerald-400 shadow-sm shadow-emerald-400/50"
                                  : "bg-slate-400"
                              }`}
                            />
                            <span className="text-brand-text-primary text-xs font-bold">
                              {iot.ma_thiet_bi || "Pi4-ESP32"}
                            </span>
                            <span className="text-brand-text-subtle text-[10px]">
                              ({iot.firmware_version || iot.phien_ban || "v1.2"}
                              )
                            </span>
                          </div>
                          <div className="flex items-center gap-3 text-[11px]">
                            <span
                              className={`inline-flex items-center gap-1 font-semibold ${
                                isOverheat
                                  ? "font-bold text-rose-400"
                                  : "text-emerald-400"
                              }`}
                              title="Nhiệt độ CPU Raspberry Pi 4"
                            >
                              <Thermometer className="h-3 w-3" />
                              {iot.cpu_temp_c ? `${iot.cpu_temp_c}°C` : "--"}
                            </span>
                            <span
                              className="text-brand-text-muted inline-flex items-center gap-1"
                              title="Dung lượng trống"
                            >
                              <HardDrive className="h-3 w-3 text-blue-400" />
                              {iot.free_storage_mb
                                ? `${(iot.free_storage_mb / 1024).toFixed(1)} GB`
                                : "24 GB"}
                            </span>
                          </div>
                        </div>
                      ) : (
                        <span className="text-brand-text-subtle inline-flex items-center gap-1 text-xs italic">
                          <AlertCircle className="h-3.5 w-3.5" /> Chưa gắn IoT
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-4 text-xs">
                      {driverData ? (
                        <div className="flex items-center gap-2.5">
                          <div className="border-brand-primary/40 bg-brand-blue-night text-brand-blue-sky flex h-7 w-7 items-center justify-center rounded-lg border text-[11px] font-bold shadow-xs">
                            {driverData.name.charAt(0)}
                          </div>
                          <div>
                            <p className="text-brand-text-primary flex items-center gap-1.5 font-bold">
                              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400"></span>
                              {driverData.name}
                            </p>
                            <p className="text-brand-text-subtle text-[10px]">
                              {driverData.shiftInfo}
                            </p>
                          </div>
                        </div>
                      ) : isMaintenance ? (
                        <span className="text-[11px] text-amber-400/80 italic">
                          Đang trong xưởng
                        </span>
                      ) : (
                        <span className="text-brand-text-subtle text-[11px] italic">
                          Trống tài (Nghỉ)
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-4">
                      <StatusBadge code={v.trang_thai} />
                    </td>
                    <td className="px-4 py-4 text-right">
                      {iot && (
                        <button
                          onClick={() => onOpenDeviceModal(v)}
                          className="text-brand-blue-sky hover:text-brand-primary mr-3 cursor-pointer text-xs font-bold hover:underline"
                        >
                          Sức khỏe IoT
                        </button>
                      )}
                      <button
                        onClick={() => onToggleMaintenance(v)}
                        className={`cursor-pointer text-xs font-bold hover:underline ${
                          isMaintenance
                            ? "text-emerald-400 hover:text-emerald-300"
                            : "text-amber-400 hover:text-amber-300"
                        }`}
                      >
                        {isMaintenance ? "Mở khóa xe" : "Bảo dưỡng"}
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
