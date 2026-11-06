import {
  AlertOctagon,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Plus,
} from "lucide-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import BaseModal from "../../../components/common/BaseModal";
import Button from "../../../components/common/Button";
import InputField from "../../../components/common/InputField";
import SelectField from "../../../components/common/SelectField";
import dataService from "../../../services/dataService";
import { validateSmartSchedule } from "../../../utils/shiftValidator";

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

  const selectedDriver = drivers.find((d) => d.id === Number(driverId));
  const selectedVehicle = vehicles.find((v) => v.id === Number(vehicleId));
  const selectedRoute = routes.find((r) => r.id === Number(routeId));

  const {
    isHardBlocked,
    hardBlockReason,
    isWeakHour,
    isLongDuration,
    newStart,
    newEnd,
  } = validateSmartSchedule({
    selectedDriver,
    selectedVehicle,
    startTime,
    durationHours,
    existingShifts,
  });

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
      toast.error("Không thể phân ca do vi phạm quy tắc cứng!");
      return;
    }
    if ((isLongDuration || isWeakHour) && !overrideReason.trim()) {
      toast.error("Bắt buộc nhập lý do ghi đè!");
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

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      icon={CalendarClock}
      title="Lập Ca Phân Bổ Mới"
      subtitle="Kiểm tra Smart Scheduling"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Hủy
          </Button>
          <Button
            type="submit"
            form="create-shift-form"
            variant="primary"
            disabled={isHardBlocked}
            onClick={handleSubmit}
          >
            <Plus className="h-4 w-4" />
            <span>Xác nhận lập ca</span>
          </Button>
        </>
      }
    >
      <form
        id="create-shift-form"
        onSubmit={handleSubmit}
        className="space-y-4"
      >
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
          <div className="border-alert-danger/40 bg-alert-danger/15 text-alert-danger rounded-2xl border p-3.5">
            <div className="flex items-center gap-2 font-bold">
              <AlertOctagon className="h-4 w-4 shrink-0" />
              <span>CHẶN CỨNG</span>
            </div>
            <p className="text-brand-text-secondary mt-1 text-[11px] leading-relaxed">
              {hardBlockReason}
            </p>
          </div>
        ) : isWeakHour || isLongDuration ? (
          <div className="border-alert-warning/40 bg-alert-warning/15 text-alert-warning space-y-2 rounded-2xl border p-3.5">
            <div className="flex items-center gap-2 font-bold">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>CẢNH BÁO THÔNG MINH SINH HỌC</span>
            </div>
            <p className="text-brand-text-secondary text-[11px] leading-relaxed">
              {isWeakHour &&
                "• Ca chạy giao thoa với khung giờ yếu sinh học (13:00 - 15:00). "}
              {isLongDuration &&
                `• Thời lượng ca (${durationHours}h) vượt mức khuyến nghị.`}
            </p>
            <InputField
              label="Nhập lý do ghi đè"
              name="overrideReason"
              required
              placeholder="VD: Đổi tài trạm Tam Kỳ..."
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
            />
          </div>
        ) : (
          <div className="border-alert-safe/30 bg-alert-safe/15 text-alert-safe flex items-center gap-2 rounded-2xl border p-3 font-semibold">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            <span>
              Lịch phân ca hoàn toàn hợp lệ (Không trùng ca, đảm bảo nghỉ ≥ 10h)
            </span>
          </div>
        )}
      </form>
    </BaseModal>
  );
}
