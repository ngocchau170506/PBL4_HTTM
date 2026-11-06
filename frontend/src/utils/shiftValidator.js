export function validateSmartSchedule({
  selectedDriver,
  selectedVehicle,
  startTime,
  durationHours,
  existingShifts = [],
  excludeShiftId = null, // Bỏ qua ca hiện tại khi đang ở chế độ Sửa (Edit)
}) {
  const driverScore = selectedDriver?.diem_uy_tin ?? 100;
  const isDriverSuspended =
    driverScore <= 20 || selectedDriver?.cap_canh_bao === "SUSPENDED_RED";
  const isVehicleMaintenance = selectedVehicle?.trang_thai === "MAINTENANCE";

  const newStart = new Date(startTime);
  const newEnd = new Date(
    newStart.getTime() + Number(durationHours || 1) * 3600000,
  );

  const activeShiftStatuses = ["SCHEDULED", "ACCEPTED", "IN_PROGRESS"];
  let overlapConflictMsg = null;
  let restTimeConflictMsg = null;

  for (const s of existingShifts) {
    if (excludeShiftId && Number(s.id) === Number(excludeShiftId)) continue;
    if (!activeShiftStatuses.includes(s.trang_thai)) continue;

    const exStart = new Date(s.thoi_gian_bat_dau_du_kien);
    const exEnd = new Date(s.thoi_gian_ket_thuc_du_kien);
    const sameDriver =
      Number(s.driver_id || s.driver?.id) === Number(selectedDriver?.id);
    const sameVehicle =
      Number(s.vehicle_id || s.vehicle?.id) === Number(selectedVehicle?.id);

    // 1. Kiểm tra giao thoa trùng giờ
    if ((sameDriver || sameVehicle) && newStart < exEnd && newEnd > exStart) {
      overlapConflictMsg = sameDriver
        ? `Tài xế ${selectedDriver?.user?.ho_ten} đang bị trùng lịch với Ca #${s.id} (${exStart.toLocaleString("vi-VN")})!`
        : `Xe ${selectedVehicle?.bien_so_xe} đang bị trùng lịch chạy ở Ca #${s.id}!`;
      break;
    }

    // 2. Kiểm tra khoảng nghỉ tối thiểu 10 giờ
    if (sameDriver) {
      const gapAfterHours = (newStart - exEnd) / 3600000;
      const gapBeforeHours = (exStart - newEnd) / 3600000;

      if (gapAfterHours >= 0 && gapAfterHours < 10) {
        restTimeConflictMsg = `Thời gian nghỉ sau Ca #${s.id} chỉ có ${gapAfterHours.toFixed(1)} giờ (Bắt buộc >= 10 giờ theo SRS 4.1)!`;
        break;
      }
      if (gapBeforeHours >= 0 && gapBeforeHours < 10) {
        restTimeConflictMsg = `Thời gian nghỉ trước Ca #${s.id} chỉ có ${gapBeforeHours.toFixed(1)} giờ (Bắt buộc >= 10 giờ theo SRS 4.1)!`;
        break;
      }
    }
  }

  let hardBlockReason = "";
  if (isDriverSuspended) {
    hardBlockReason = `Tài xế ${selectedDriver?.user?.ho_ten} đang bị đình chỉ (Điểm: ${driverScore} <= 20). Không thể giao ca!`;
  } else if (isVehicleMaintenance) {
    hardBlockReason = `Xe ${selectedVehicle?.bien_so_xe} đang trong xưởng bảo dưỡng!`;
  } else if (overlapConflictMsg) {
    hardBlockReason = overlapConflictMsg;
  } else if (restTimeConflictMsg) {
    hardBlockReason = restTimeConflictMsg;
  }

  const isHardBlocked = Boolean(hardBlockReason);
  const startHour = newStart.getHours();
  const isWeakHour = startHour >= 12 && startHour <= 15;
  const isLongDuration = Number(durationHours) >= 5;

  return {
    isHardBlocked,
    hardBlockReason,
    isWeakHour,
    isLongDuration,
    newStart,
    newEnd,
  };
}
