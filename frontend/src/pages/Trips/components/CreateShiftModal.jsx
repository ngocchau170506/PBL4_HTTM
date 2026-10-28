import {
  AlertOctagon,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Plus,
  X,
} from "lucide-react";
import React, { useEffect, useState } from "react";
import toast from "react-hot-toast";
import Button from "../../../components/common/Button";
import InputField from "../../../components/common/InputField";
import SelectField from "../../../components/common/SelectField";
import dataService from "../../../services/dataService";

export default function CreateShiftModal({
  isOpen,
  onClose,
  onAddShift,
  shifts: propShifts,
}) {
  const [drivers, setDrivers] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  const [routes, setRoutes] = useState([]);
  const [existingShifts, setExistingShifts] = useState([]);

  const [driverId, setDriverId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [routeId, setRouteId] = useState("");
  const [startTime, setStartTime] = useState("2026-10-23T13:30");
  const [durationHours, setDurationHours] = useState(4);
  const [overrideReason, setOverrideReason] = useState("");

  useEffect(() => {
    if (!isOpen) return;
    const fetchOptions = async () => {
      const [dList, vList, rList, sList] = await Promise.all([
        dataService.getDrivers(),
        dataService.getVehicles(),
        dataService.getRoutes(),
        dataService.getShifts(),
      ]);
      setDrivers(dList || []);
      setVehicles(vList || []);
      setRoutes(rList || []);
      setExistingShifts(propShifts || sList || []);
      if (dList?.length) setDriverId(dList[0].id);
      if (vList?.length) setVehicleId(vList[0].id);
      if (rList?.length) setRouteId(rList[0].id);
    };
    fetchOptions();
  }, [isOpen, propShifts]);

  if (!isOpen) return null;

  const selectedDriver = drivers.find((d) => d.id === Number(driverId));
  const selectedVehicle = vehicles.find((v) => v.id === Number(vehicleId));
  const selectedRoute = routes.find((r) => r.id === Number(routeId));

  // 1. Kiểm tra Tài xế đình chỉ & Xe bảo dưỡng
  const driverScore = selectedDriver?.diem_uy_tin ?? 100;
  const isDriverSuspended =
    driverScore <= 20 || selectedDriver?.cap_canh_bao === "SUSPENDED_RED";
  const isVehicleMaintenance = selectedVehicle?.trang_thai === "MAINTENANCE";

  // 2. Kiểm tra Trùng lịch ca chạy & Khoảng nghỉ tối thiểu 10 tiếng
  const newStart = new Date(startTime);
  const newEnd = new Date(
    newStart.getTime() + Number(durationHours || 1) * 3600000,
  );

  const activeShiftStatuses = ["SCHEDULED", "ACCEPTED", "IN_PROGRESS"];
  let overlapConflictMsg = null;
  let restTimeConflictMsg = null;

  for (const s of existingShifts) {
    if (!activeShiftStatuses.includes(s.trang_thai)) continue;
    const exStart = new Date(s.thoi_gian_bat_dau_du_kien);
    const exEnd = new Date(s.thoi_gian_ket_thuc_du_kien);
    const sameDriver = Number(s.driver_id || s.driver?.id) === Number(driverId);
    const sameVehicle =
      Number(s.vehicle_id || s.vehicle?.id) === Number(vehicleId);

    // (a) Kiểm tra giao thoa trùng giờ (Overlap: newStart < exEnd && newEnd > exStart)
    if ((sameDriver || sameVehicle) && newStart < exEnd && newEnd > exStart) {
      overlapConflictMsg = sameDriver
        ? `Tài xế ${selectedDriver?.user?.ho_ten} đang bị trùng lịch với Ca #${s.id} (${exStart.toLocaleString("vi-VN")})!`
        : `Xe ${selectedVehicle?.bien_so_xe} đang bị trùng lịch chạy ở Ca #${s.id}!`;
      break;
    }

    // (b) Kiểm tra khoảng nghỉ giữa 2 ca liên tiếp của cùng tài xế < 10 giờ
    if (sameDriver) {
      const gapAfterExistingHours = (newStart - exEnd) / 3600000;
      const gapBeforeExistingHours = (exStart - newEnd) / 3600000;

      if (gapAfterExistingHours >= 0 && gapAfterExistingHours < 10) {
        restTimeConflictMsg = `Thời gian nghỉ sau Ca #${s.id} chỉ có ${gapAfterExistingHours.toFixed(1)} giờ (Bắt buộc tối thiểu >= 10 giờ theo SRS 4.1)!`;
        break;
      }
      if (gapBeforeExistingHours >= 0 && gapBeforeExistingHours < 10) {
        restTimeConflictMsg = `Thời gian nghỉ trước Ca #${s.id} chỉ có ${gapBeforeExistingHours.toFixed(1)} giờ (Bắt buộc tối thiểu >= 10 giờ theo SRS 4.1)!`;
        break;
      }
    }
  }

  const isHardBlocked =
    isDriverSuspended ||
    isVehicleMaintenance ||
    Boolean(overlapConflictMsg) ||
    Boolean(restTimeConflictMsg);

  const startHour = newStart.getHours();
  const isWeakHour = startHour >= 12 && startHour <= 15;
  const isLongDuration = Number(durationHours) >= 5;

  const driverOptions = drivers.map((d) => ({
    value: d.id,
    label: `${d.user?.ho_ten || d.ho_ten} - Điểm uy tín: ${d.diem_uy_tin}/100 ${
      d.diem_uy_tin <= 20 ? "(ĐÌNH CHỈ)" : ""
    }`,
  }));

  const vehicleOptions = vehicles.map((v) => ({
    value: v.id,
    label: `${v.bien_so_xe} (${
      v.trang_thai === "MAINTENANCE" ? "BẢO DƯỠNG" : v.loai_xe
    })`,
  }));

  const routeOptions = routes.map((r) => ({
    value: r.id,
    label: `${r.ten_tuyen} (${r.khoang_cach_km} km)`,
  }));

  const handleSubmit = (e) => {
    e.preventDefault();
    if (isHardBlocked) {
      toast.error("Không thể phân ca do vi phạm quy tắc cứng (HARD_BLOCK)!");
      return;
    }
    if ((isLongDuration || isWeakHour) && !overrideReason.trim()) {
      toast.error("Bắt buộc nhập lý do ghi đè (Override)!");
      return;
    }

    const alerts = [];
    if (isWeakHour)
      alerts.push({
        level: "NOTICE",
        message: "Giao thoa khung giờ yếu sinh học (13h-15h)",
      });
    if (isLongDuration)
      alerts.push({
        level: "SOFT_WARNING",
        message: `Ca dài (${durationHours}h) vượt mức khuyến nghị`,
      });

    const newShift = {
      id: Date.now(),
      driver_id: Number(driverId),
      vehicle_id: Number(vehicleId),
      route_id: Number(routeId),
      thoi_gian_bat_dau_du_kien: newStart.toISOString(),
      thoi_gian_ket_thuc_du_kien: newEnd.toISOString(),
      trang_thai: "SCHEDULED",
      canh_bao_khi_tao: alerts,
      ly_do_override: overrideReason,
      driver: selectedDriver,
      vehicle: selectedVehicle,
      route: selectedRoute,
    };

    onAddShift(newShift);
    toast.success(`Đã phân ca cho tài xế ${selectedDriver?.user?.ho_ten}!`, {
      icon: "📅",
    });
    setOverrideReason("");
    onClose();
  };

  // Xác định thông điệp lỗi HARD_BLOCK chi tiết nhất
  const getHardBlockReason = () => {
    if (isDriverSuspended) {
      return `Tài xế ${selectedDriver?.user?.ho_ten} đang bị đình chỉ (Điểm: ${driverScore} <= 20). Không thể giao ca!`;
    }
    if (isVehicleMaintenance) {
      return `Xe ${selectedVehicle?.bien_so_xe} đang trong xưởng bảo dưỡng!`;
    }
    if (overlapConflictMsg) return overlapConflictMsg;
    if (restTimeConflictMsg) return restTimeConflictMsg;
    return "";
  };

  return (
    <div className="fixed inset-0 z-9999 flex items-center justify-center bg-black/80 p-4 backdrop-blur-md">
      <div className="card-glass border-brand-border animate-fade-in-up max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-3xl border shadow-2xl">
        <div className="border-brand-border-dark bg-brand-dark/90 sticky top-0 z-10 flex items-center justify-between border-b px-6 py-4 backdrop-blur-md">
          <div className="flex items-center gap-2.5">
            <div className="border-brand-primary/40 bg-brand-blue-night text-brand-blue-sky flex h-9 w-9 items-center justify-center rounded-xl border">
              <CalendarClock className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-brand-text-primary text-sm font-bold">
                Lập Ca Phân Bổ Mới
              </h3>
              <p className="text-brand-text-muted text-[11px]">
                Kiểm tra Smart Scheduling: Điểm uy tín, Trùng lịch & Nghỉ &ge;
                10h
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-brand-text-muted cursor-pointer rounded-full p-1 hover:text-white"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 p-6 text-xs">
          <SelectField
            label="Tài xế nhận ca"
            name="driverId"
            required
            value={driverId}
            onChange={(e) => setDriverId(e.target.value)}
            options={driverOptions}
          />

          <div className="grid grid-cols-2 gap-3">
            <SelectField
              label="Phương tiện"
              name="vehicleId"
              value={vehicleId}
              onChange={(e) => setVehicleId(e.target.value)}
              options={vehicleOptions}
            />
            <SelectField
              label="Lộ trình tuyến"
              name="routeId"
              value={routeId}
              onChange={(e) => setRouteId(e.target.value)}
              options={routeOptions}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <InputField
              label="Giờ xuất phát"
              name="startTime"
              type="datetime-local"
              value={startTime}
              onChange={(e) => setStartTime(e.target.value)}
            />
            <InputField
              label="Thời lượng dự kiến (Giờ)"
              name="durationHours"
              type="number"
              min={1}
              max={12}
              value={durationHours}
              onChange={(e) => setDurationHours(e.target.value)}
            />
          </div>

          {isHardBlocked ? (
            <div className="rounded-2xl border border-rose-500/40 bg-rose-950/30 p-3.5 text-rose-300">
              <div className="flex items-center gap-2 font-bold">
                <AlertOctagon className="h-4 w-4 shrink-0 text-rose-400" />
                <span>CHẶN CỨNG (HARD_BLOCK - HTTP 422)</span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed">
                {getHardBlockReason()}
              </p>
            </div>
          ) : isWeakHour || isLongDuration ? (
            <div className="space-y-2 rounded-2xl border border-amber-500/40 bg-amber-950/30 p-3.5 text-amber-300">
              <div className="flex items-center gap-2 font-bold">
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
                <span>CẢNH BÁO THÔNG MINH SINH HỌC (SOFT_WARNING)</span>
              </div>
              <p className="text-[11px] leading-relaxed">
                {isWeakHour &&
                  "• Ca chạy giao thoa với khung giờ yếu sinh học (13:00 - 15:00). "}
                {isLongDuration &&
                  `• Thời lượng ca (${durationHours}h) vượt mức khuyến nghị.`}
              </p>
              <InputField
                label="Nhập lý do ghi đè (Bắt buộc theo SRS 4.1)"
                name="overrideReason"
                required
                placeholder="VD: Đổi tài trạm Tam Kỳ..."
                value={overrideReason}
                onChange={(e) => setOverrideReason(e.target.value)}
              />
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-3 text-emerald-400">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              <span>
                Lịch phân ca hoàn toàn hợp lệ (Không trùng ca, đảm bảo nghỉ &ge;
                10h)
              </span>
            </div>
          )}

          <div className="border-brand-border-dark flex items-center justify-end gap-2.5 border-t pt-4">
            <Button variant="secondary" onClick={onClose}>
              Hủy
            </Button>
            <Button type="submit" variant="primary" disabled={isHardBlocked}>
              <Plus className="h-4 w-4" />
              <span>Xác nhận lập ca</span>
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
