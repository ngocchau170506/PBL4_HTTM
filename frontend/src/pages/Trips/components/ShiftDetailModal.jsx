import {
  AlertOctagon,
  AlertTriangle,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  Info,
  Lock,
  Save,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import BaseModal from "../../../components/common/BaseModal";
import Button from "../../../components/common/Button";
import InputField from "../../../components/common/InputField";
import SelectField from "../../../components/common/SelectField";
import StatusBadge from "../../../components/common/StatusBadge";
import dataService from "../../../services/dataService";
import { formatFullDateTime } from "../../../utils/formatDateTime";
import { validateSmartSchedule } from "../../../utils/shiftValidator";

const toDateTimeLocal = (isoString) => {
  if (!isoString) return "2026-10-23T08:00";
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return "2026-10-23T08:00";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export default function ShiftDetailModal({
  isOpen,
  onClose,
  shift,
  existingShifts = [],
  onUpdateShift,
  onDeleteShift,
  onSwitchToTripTab,
}) {
  const [drivers, setDrivers] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  const [routes, setRoutes] = useState([]);

  const [driverId, setDriverId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [routeId, setRouteId] = useState("");
  const [startTime, setStartTime] = useState("");
  const [durationHours, setDurationHours] = useState(4);
  const [overrideReason, setOverrideReason] = useState("");

  useEffect(() => {
    if (!isOpen || !shift) return;

    const loadCatalog = async () => {
      const [dList, vList, rList] = await Promise.all([
        dataService.getDrivers(),
        dataService.getVehicles(),
        dataService.getRoutes(),
      ]);
      setDrivers(dList || []);
      setVehicles(vList || []);
      setRoutes(rList || []);
    };
    loadCatalog();

    setDriverId(shift.driver_id || shift.driver?.id || "");
    setVehicleId(shift.vehicle_id || shift.vehicle?.id || "");
    setRouteId(shift.route_id || shift.route?.id || "");
    setStartTime(toDateTimeLocal(shift.thoi_gian_bat_dau_du_kien));

    const startMs = new Date(shift.thoi_gian_bat_dau_du_kien).getTime();
    const endMs = new Date(shift.thoi_gian_ket_thuc_du_kien).getTime();
    const calcHours = Math.max(1, Math.round((endMs - startMs) / 3600000));
    setDurationHours(calcHours || 4);
    setOverrideReason(shift.ly_do_override || "");
  }, [isOpen, shift]);

  if (!shift) return null;

  // PHÂN QUYỀN CHỈNH SỬA:
  // 1. IN_PROGRESS / COMPLETED: Khóa toàn bộ (Read-only)
  // 2. ACCEPTED: Tài xế đã chốt -> Khóa Tài xế, Giờ chạy, Tuyến đường; CHỈ CHO ĐỔI XE!
  // 3. SCHEDULED: Chờ tài xế nhận -> Cho phép sửa toàn bộ hoặc Hủy ca
  const isLockedAll =
    shift.trang_thai === "IN_PROGRESS" || shift.trang_thai === "COMPLETED";
  const isAcceptedOnlyVehicle = shift.trang_thai === "ACCEPTED";
  const lockCoreFields = isLockedAll || isAcceptedOnlyVehicle;

  const selectedDriver =
    drivers.find((d) => d.id === Number(driverId)) || shift.driver;
  const selectedVehicle =
    vehicles.find((v) => v.id === Number(vehicleId)) || shift.vehicle;
  const selectedRoute =
    routes.find((r) => r.id === Number(routeId)) || shift.route;

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
    excludeShiftId: shift.id,
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

  const handleSaveUpdate = async () => {
    if (isLockedAll) return;
    if (isHardBlocked) {
      toast.error("Không thể lưu do vi phạm quy tắc cứng (HARD_BLOCK)!");
      return;
    }
    if (
      !isAcceptedOnlyVehicle &&
      (isLongDuration || isWeakHour) &&
      !overrideReason.trim()
    ) {
      toast.error("Bắt buộc nhập lý do ghi đè!");
      return;
    }

    const alerts = [];
    if (isWeakHour)
      alerts.push({
        level: "NOTICE",
        message: "Giao thoa khung giờ yếu sinh học",
      });
    if (isLongDuration)
      alerts.push({
        level: "SOFT_WARNING",
        message: `Ca dài (${durationHours}h) vượt mức khuyến nghị`,
      });

    await onUpdateShift(shift.id, {
      driver_id: Number(driverId),
      vehicle_id: Number(vehicleId),
      route_id: Number(routeId),
      thoi_gian_bat_dau_du_kien: newStart.toISOString(),
      thoi_gian_ket_thuc_du_kien: newEnd.toISOString(),
      canh_bao_khi_tao: alerts,
      ly_do_override: overrideReason,
      driver: selectedDriver,
      vehicle: selectedVehicle,
      route: selectedRoute,
    });
    onClose();
  };

  const handleConfirmDelete = async () => {
    if (lockCoreFields) return;
    if (
      window.confirm(
        `Bạn có chắc chắn muốn xóa/hủy Ca #${shift.id} của tài xế ${selectedDriver?.user?.ho_ten || ""} không?`,
      )
    ) {
      await onDeleteShift(shift.id);
      onClose();
    }
  };

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      icon={CalendarClock}
      title={`Chi tiết Ca Phân Công #${shift.id}`}
      subtitle={
        isLockedAll
          ? "Ca đang thực hiện trên đường — Chế độ Chỉ xem"
          : isAcceptedOnlyVehicle
            ? "Tài xế đã nhận ca — Chỉ cho phép điều động đổi phương tiện (Hot-swap)"
            : "Cho phép điều chỉnh kế hoạch hoặc hủy ca trước khi tài xế chốt"
      }
      footer={
        isLockedAll ? (
          <div className="flex w-full items-center justify-between">
            <span className="text-brand-text-muted flex items-center gap-1.5 text-xs">
              <Lock className="text-alert-warning h-3.5 w-3.5" />
              Khóa sửa/xóa
            </span>
            <div className="flex items-center gap-2">
              {onSwitchToTripTab && (
                <Button
                  variant="primary"
                  onClick={() => {
                    onClose();
                    onSwitchToTripTab();
                  }}
                >
                  <span>Xem chuyến thực tế</span>
                  <ArrowUpRight className="h-4 w-4" />
                </Button>
              )}
              <Button variant="secondary" onClick={onClose}>
                Đóng
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex w-full items-center justify-between">
            {!isAcceptedOnlyVehicle ? (
              <Button variant="danger" onClick={handleConfirmDelete}>
                <Trash2 className="h-4 w-4" />
                <span>Xóa / Hủy ca</span>
              </Button>
            ) : (
              <span className="text-alert-info flex items-center gap-1.5 text-[11px] font-semibold">
                <Lock className="h-3.5 w-3.5" /> Đã khóa Tài xế &amp; Giờ chạy
              </span>
            )}
            <div className="flex items-center gap-2.5">
              <Button variant="secondary" onClick={onClose}>
                Đóng
              </Button>
              <Button
                variant="primary"
                disabled={isHardBlocked}
                onClick={handleSaveUpdate}
              >
                <Save className="h-4 w-4" />
                <span>
                  {isAcceptedOnlyVehicle ? "Lưu đổi xe" : "Lưu thay đổi"}
                </span>
              </Button>
            </div>
          </div>
        )
      }
    >
      <div className="space-y-4">
        {/* TÓM TẮT TRẠNG THÁI HIỆN TẠI */}
        <div className="border-brand-border-dark bg-brand-dark/70 flex items-center justify-between rounded-2xl border p-3.5">
          <div>
            <p className="text-brand-text-muted text-[11px]">
              Trạng thái kế hoạch hiện tại
            </p>
            <p className="text-brand-text-primary mt-0.5 font-bold">
              {formatFullDateTime(shift.thoi_gian_bat_dau_du_kien)} &rarr;{" "}
              {formatFullDateTime(shift.thoi_gian_ket_thuc_du_kien)}
            </p>
          </div>
          <StatusBadge code={shift.trang_thai} />
        </div>

        {/* BANNER GIẢI THÍCH KHI CA ĐÃ ACCEPTED */}
        {isAcceptedOnlyVehicle && (
          <div className="border-alert-info/30 bg-alert-info/15 text-alert-info flex items-start gap-2.5 rounded-2xl border p-3.5">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="text-[11px] leading-relaxed">
              <b>Bảo vệ cam kết ca trực:</b> Tài xế{" "}
              <b>{selectedDriver?.user?.ho_ten}</b> đã xác nhận nhận ca. Hệ
              thống khóa thay đổi Tài xế, Lộ trình và Giờ xuất phát để đảm bảo
              nhịp sinh học. Bạn chỉ có thể <b>đổi Phương tiện</b> nếu xe cũ gặp
              sự cố kỹ thuật tại bãi.
            </div>
          </div>
        )}

        <SelectField
          label="Tài xế nhận ca"
          name="driverId"
          disabled={lockCoreFields}
          value={driverId}
          onChange={(e) => setDriverId(e.target.value)}
          options={driverOptions}
        />

        <div className="grid grid-cols-2 gap-3">
          {/* Ô PHƯƠNG TIỆN: Vẫn mở khi ACCEPTED, chỉ khóa khi IN_PROGRESS */}
          <SelectField
            label="Phương tiện"
            name="vehicleId"
            disabled={isLockedAll}
            value={vehicleId}
            onChange={(e) => setVehicleId(e.target.value)}
            options={vehicleOptions}
          />
          <SelectField
            label="Lộ trình tuyến"
            name="routeId"
            disabled={lockCoreFields}
            value={routeId}
            onChange={(e) => setRouteId(e.target.value)}
            options={routeOptions}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <InputField
            label="Giờ xuất phát dự kiến"
            name="startTime"
            type="datetime-local"
            disabled={lockCoreFields}
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
          />
          <InputField
            label="Thời lượng dự kiến (Giờ)"
            name="durationHours"
            type="number"
            min={1}
            max={12}
            disabled={lockCoreFields}
            value={durationHours}
            onChange={(e) => setDurationHours(e.target.value)}
          />
        </div>

        {/* CẢNH BÁO SMART SCHEDULING KHI SỬA */}
        {!isLockedAll && (
          <>
            {isHardBlocked ? (
              <div className="border-alert-danger/40 bg-alert-danger/15 text-alert-danger rounded-2xl border p-3.5">
                <div className="flex items-center gap-2 font-bold">
                  <AlertOctagon className="h-4 w-4 shrink-0" />
                  <span>CHẶN CỨNG (HARD_BLOCK - HTTP 422)</span>
                </div>
                <p className="text-brand-text-secondary mt-1 text-[11px] leading-relaxed">
                  {hardBlockReason}
                </p>
              </div>
            ) : !isAcceptedOnlyVehicle && (isWeakHour || isLongDuration) ? (
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
                  label="Lý do ghi đè"
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
                  Phương tiện &amp; Lịch phân ca hợp lệ (Đảm bảo an toàn vận
                  hành)
                </span>
              </div>
            )}
          </>
        )}
      </div>
    </BaseModal>
  );
}
