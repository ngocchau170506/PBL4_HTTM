import {
  AlertTriangle,
  CheckCircle2,
  Coffee,
  Flag,
  Gauge,
  Navigation,
  Play,
  RefreshCw,
  Truck,
  Users,
  Wind,
  X,
  XCircle,
} from "lucide-react-native";
import React, { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { COLORS } from "../../../mobileConfig";
import AppButton from "../../components/common/AppButton";
import AppCard from "../../components/common/AppCard";
import StatusBadge from "../../components/common/StatusBadge";
import driverDataService from "../../services/driverDataService";
import CalibrationModal from "./CalibrationModal";

export default function TripScreen({
  shift,
  isOfflineMode,
  onRespondShift,
  onCompleteCalibration,
  onToggleRest,
  onSwitchDriver,
  onCompleteTrip,
}) {
  const [showCalibModal, setShowCalibModal] = useState(false);
  const [showDeclineModal, setShowDeclineModal] = useState(false);
  const [declineReason, setDeclineReason] = useState("");
  const [showSwitchModal, setShowSwitchModal] = useState(false);
  const [coDrivers, setCoDrivers] = useState([]);
  const [loadingCoDrivers, setLoadingCoDrivers] = useState(false);
  const [selectedCoDriver, setSelectedCoDriver] = useState(null);

  const handleFinishCalibration = async () => {
    setShowCalibModal(false);
    await onCompleteCalibration(isOfflineMode);
    Alert.alert(
      "Khởi hành thành công",
      isOfflineMode
        ? "Đã mở chuyến ở chế độ Tạm ứng Ngoại tuyến (PENDING_CALIBRATION). Xe sẽ tự đồng bộ khi có sóng 4G!"
        : "Hiệu chuẩn khuôn mặt thành công (ear_baseline = 0.285). Chúc bác tài thượng lộ bình an!",
    );
  };

  const handleOpenSwitchModal = async () => {
    setShowSwitchModal(true);
    setSelectedCoDriver(null);
    setLoadingCoDrivers(true);
    try {
      const list = await driverDataService.getCoDriverCandidates();
      setCoDrivers(list);
      if (list.length > 0) setSelectedCoDriver(list[0]);
    } catch {
      setCoDrivers([]);
    } finally {
      setLoadingCoDrivers(false);
    }
  };

  const handleConfirmSwitchCoDriver = async () => {
    if (!selectedCoDriver) {
      Alert.alert("Chưa chọn tài xế", "Vui lòng chọn tài xế phụ nhận bàn giao tay lái!");
      return;
    }
    await onSwitchDriver(isOfflineMode, selectedCoDriver);
    setShowSwitchModal(false);
    Alert.alert(
      "Đổi tài thành công (Co-driving)",
      isOfflineMode
        ? `Đã bàn giao tay lái cho ${selectedCoDriver.ho_ten} qua chế độ ngoại tuyến!`
        : `Đã bàn giao tay lái cho ${selectedCoDriver.ho_ten} và ghi nhận nhật ký hệ thống!`,
    );
  };

  const handleConfirmDecline = async () => {
    if (!declineReason.trim()) {
      Alert.alert("Thiếu lý do", "Vui lòng nhập lý do từ chối ca trực!");
      return;
    }
    await onRespondShift("DECLINED", declineReason.trim());
    setDeclineReason("");
    setShowDeclineModal(false);
  };

  if (!shift) {
    return (
      <ScrollView
        style={styles.container}
        contentContainerStyle={{ padding: 16 }}
      >
        <AppCard>
          <View style={styles.vehicleRow}>
            <Truck size={20} color={COLORS.brandBlueSky} />
            <Text style={styles.plateText}>Chưa có ca phân công</Text>
          </View>
          <View style={styles.divider} />
          <Text style={styles.subInfo}>
            Hiện tại bạn chưa có ca trực nào được giao hoặc đang chờ xuất bến.
            Hệ thống sẽ tự động thông báo ngay khi Điều phối viên xếp lịch mới.
          </Text>
        </AppCard>
      </ScrollView>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 16 }}>
      {/* THÔNG TIN CA TRỰC ĐƯỢC GIAO */}
      <AppCard variant="active">
        <View style={styles.topRow}>
          <View style={styles.vehicleRow}>
            <Truck size={20} color={COLORS.brandBlueSky} />
            <Text style={styles.plateText}>{shift.bien_so_xe}</Text>
          </View>
          <StatusBadge
            label={
              shift.trip_status === "IN_PROGRESS"
                ? "ĐANG LĂN BÁNH"
                : shift.trip_status === "RESTING"
                  ? "ĐANG TẠM NGHỈ"
                  : shift.trip_status === "COMPLETED"
                    ? "ĐÃ HOÀN THÀNH"
                    : shift.shift_status === "ACCEPTED"
                      ? "ĐÃ NHẬN CA"
                      : "CA MỚI PHÂN CÔNG"
            }
            type={
              shift.trip_status === "IN_PROGRESS"
                ? "safe"
                : shift.trip_status === "RESTING"
                  ? "warning"
                  : "info"
            }
          />
        </View>

        <Text style={styles.routeTitle}>{shift.ten_tuyen}</Text>
        <Text style={styles.subInfo}>
          Quãng đường: {shift.khoang_cach_km} km • Thiết bị: {shift.ma_thiet_bi}
        </Text>

        <View style={styles.divider} />

        <View style={styles.metaGrid}>
          <View>
            <Text style={styles.metaLabel}>Khung giờ dự kiến</Text>
            <Text style={styles.metaVal}>{shift.thoi_gian_bat_dau}</Text>
          </View>
          <View>
            <Text style={styles.metaLabel}>Người cầm lái hiện tại</Text>
            <Text style={[styles.metaVal, { color: COLORS.brandBlueSky }]}>
              {shift.active_driver_name}
            </Text>
          </View>
        </View>

        {shift.calibration_mode && (
          <View style={{ marginTop: 10 }}>
            <StatusBadge
              label={
                shift.calibration_mode === "PENDING_CALIBRATION"
                  ? "Hiệu chuẩn: TẠM ỨNG NGOẠI TUYẾN (PENDING_CALIBRATION)"
                  : "Hiệu chuẩn: ĐÃ XÁC THỰC AI CLOUD (SUCCESS)"
              }
              type={
                shift.calibration_mode === "PENDING_CALIBRATION"
                  ? "warning"
                  : "safe"
              }
            />
          </View>
        )}
      </AppCard>

      {/* CẢNH BÁO NHỊP SINH HỌC SMART SCHEDULING */}
      <AppCard variant="warning">
        <View style={styles.warnHeader}>
          <AlertTriangle size={18} color={COLORS.alertWarning} />
          <Text style={styles.warnTitle}>
            Lưu ý Sức khỏe Sinh học (Smart Scheduling)
          </Text>
        </View>
        <Text style={styles.warnBody}>{shift.canh_bao_sinh_hoc}</Text>
      </AppCard>

      {/* CHỈ SỐ TELEMETRY BUỒNG LÁI THỜI GIAN THỰC */}
      {(shift.trip_status === "IN_PROGRESS" ||
        shift.trip_status === "RESTING") && (
        <AppCard>
          <Text style={styles.sectionTitle}>
            Chỉ số Cảm biến Buồng lái Trực tuyến (ESP32 + Pi 4)
          </Text>
          <View style={styles.teleGrid}>
            <View style={styles.teleBox}>
              <Gauge size={18} color={COLORS.brandBlueSky} />
              <Text style={styles.teleVal}>
                {shift.trip_status === "RESTING"
                  ? 0
                  : shift.telemetry.toc_do_kmh}{" "}
                km/h
              </Text>
              <Text style={styles.teleLabel}>Vận tốc GPS</Text>
            </View>
            <View style={styles.teleBox}>
              <Wind size={18} color={COLORS.alertSafe} />
              <Text style={styles.teleVal}>{shift.telemetry.co2_ppm} ppm</Text>
              <Text style={styles.teleLabel}>Khí cabin MQ-135</Text>
            </View>
          </View>
          <View style={styles.restStopBar}>
            <Navigation size={15} color={COLORS.brandBlueSky} />
            <Text style={styles.restStopText}>
              Trạm nghỉ gần nhất: {shift.telemetry.nearest_rest_stop}
            </Text>
          </View>
        </AppCard>
      )}

      {/* BẢNG ĐIỀU KHIỂN THAO TÁC CHUYẾN ĐI (FR_5.2 & FR_5.3) */}
      <AppCard>
        <Text style={styles.sectionTitle}>Thao tác Vận hành Ca & Chuyến đi</Text>

        {shift.shift_status === "SCHEDULED" && (
          <View style={styles.btnRow}>
            <AppButton
              title="Từ chối ca"
              variant="danger"
              icon={XCircle}
              onPress={() => setShowDeclineModal(true)}
              style={{ flex: 1 }}
            />
            <AppButton
              title="Nhận ca chạy"
              variant="primary"
              icon={CheckCircle2}
              onPress={() => onRespondShift("ACCEPTED")}
              style={{ flex: 1.5 }}
            />
          </View>
        )}

        {shift.shift_status === "ACCEPTED" &&
          shift.trip_status === "NOT_STARTED" && (
            <AppButton
              title="Bắt đầu chuyến (Chụp IR Hiệu chuẩn)"
              variant="primary"
              icon={Play}
              onPress={() => setShowCalibModal(true)}
            />
          )}

        {(shift.trip_status === "IN_PROGRESS" ||
          shift.trip_status === "RESTING") && (
          <View style={{ gap: 10 }}>
            <View style={styles.btnRow}>
              <AppButton
                title={
                  shift.trip_status === "RESTING"
                    ? "Tiếp tục hành trình"
                    : "Tạm dừng nghỉ"
                }
                variant="warning"
                icon={Coffee}
                onPress={onToggleRest}
                style={{ flex: 1 }}
              />
              <AppButton
                title="Đổi tài (Co-driver)"
                variant="secondary"
                icon={RefreshCw}
                onPress={handleOpenSwitchModal}
                style={{ flex: 1 }}
              />
            </View>
            <AppButton
              title="Hoàn thành Chuyến đi (+5đ An toàn)"
              variant="safe"
              icon={Flag}
              onPress={onCompleteTrip}
            />
          </View>
        )}

        {shift.trip_status === "COMPLETED" && (
          <Text style={styles.completedNote}>
            ✅ Chuyến đi đã hoàn tất. Hệ thống sẽ tự động xét cộng điểm thưởng
            sau 2 giờ nếu không phát sinh vi phạm!
          </Text>
        )}
      </AppCard>

      <CalibrationModal
        visible={showCalibModal}
        onClose={() => setShowCalibModal(false)}
        isOfflineMode={isOfflineMode}
        driverName={shift.active_driver_name}
        onFinishCalibration={handleFinishCalibration}
      />

      {/* Modal nhập lý do từ chối ca */}
      <Modal visible={showDeclineModal} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Lý do từ chối ca #{shift.id}</Text>
              <TouchableOpacity onPress={() => setShowDeclineModal(false)}>
                <X size={20} color={COLORS.textMuted} />
              </TouchableOpacity>
            </View>
            <Text style={styles.subInfo}>
              Vui lòng nêu rõ lý do để Điều phối viên kịp thời sắp xếp tài xế thay thế.
            </Text>
            <TextInput
              value={declineReason}
              onChangeText={setDeclineReason}
              multiline
              numberOfLines={3}
              placeholder="VD: Sức khỏe không đảm bảo / Đang có lịch nghỉ phép..."
              placeholderTextColor={COLORS.textSubtle}
              style={styles.modalInput}
            />
            <View style={styles.btnRow}>
              <AppButton
                title="Hủy"
                variant="secondary"
                onPress={() => setShowDeclineModal(false)}
                style={{ flex: 1 }}
              />
              <AppButton
                title="Xác nhận từ chối"
                variant="danger"
                onPress={handleConfirmDecline}
                style={{ flex: 1.5 }}
              />
            </View>
          </View>
        </View>
      </Modal>

      {/* Modal chọn tài xế phụ khi đổi tài */}
      <Modal visible={showSwitchModal} transparent animationType="slide">
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <View style={styles.modalHeader}>
              <View style={styles.vehicleRow}>
                <Users size={18} color={COLORS.brandBlueSky} />
                <Text style={styles.modalTitle}>Chọn Tài xế nhận bàn giao</Text>
              </View>
              <TouchableOpacity onPress={() => setShowSwitchModal(false)}>
                <X size={20} color={COLORS.textMuted} />
              </TouchableOpacity>
            </View>

            {loadingCoDrivers ? (
              <ActivityIndicator
                size="small"
                color={COLORS.brandBlueSky}
                style={{ marginVertical: 24 }}
              />
            ) : coDrivers.length === 0 ? (
              <Text style={[styles.subInfo, { marginVertical: 16 }]}>
                Không có tài xế hợp lệ khác sẵn sàng nhận bàn giao tay lái.
              </Text>
            ) : (
              <View style={{ gap: 8, marginVertical: 14 }}>
                {coDrivers.map((d) => {
                  const active = selectedCoDriver?.id === d.id;
                  return (
                    <TouchableOpacity
                      key={d.id}
                      onPress={() => setSelectedCoDriver(d)}
                      style={[
                        styles.coDriverItem,
                        active && styles.coDriverItemActive,
                      ]}
                    >
                      <Text style={styles.coDriverName}>{d.ho_ten}</Text>
                      <Text style={styles.coDriverMeta}>
                        {d.trang_thai_lai_xe} • {d.cap_canh_bao}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
            )}

            <View style={styles.btnRow}>
              <AppButton
                title="Đóng"
                variant="secondary"
                onPress={() => setShowSwitchModal(false)}
                style={{ flex: 1 }}
              />
              <AppButton
                title="Xác nhận Đổi tài"
                variant="primary"
                onPress={handleConfirmSwitchCoDriver}
                style={{ flex: 1.5 }}
              />
            </View>
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.brandDeep,
  },
  topRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 10,
  },
  vehicleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  plateText: {
    color: COLORS.textPrimary,
    fontSize: 20,
    fontWeight: "900",
  },
  routeTitle: {
    color: COLORS.textPrimary,
    fontSize: 14,
    fontWeight: "700",
    lineHeight: 20,
  },
  subInfo: {
    color: COLORS.textMuted,
    fontSize: 11,
    marginTop: 4,
  },
  divider: {
    height: 1,
    backgroundColor: COLORS.brandBorder,
    marginVertical: 12,
  },
  metaGrid: {
    flexDirection: "row",
    justifyContent: "space-between",
  },
  metaLabel: {
    color: COLORS.textSubtle,
    fontSize: 11,
  },
  metaVal: {
    color: COLORS.textPrimary,
    fontSize: 12,
    fontWeight: "700",
    marginTop: 2,
  },
  warnHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 6,
  },
  warnTitle: {
    color: "#fbbf24",
    fontSize: 13,
    fontWeight: "800",
  },
  warnBody: {
    color: "#fde68a",
    fontSize: 12,
    lineHeight: 18,
  },
  sectionTitle: {
    color: COLORS.textPrimary,
    fontSize: 13,
    fontWeight: "800",
    marginBottom: 12,
  },
  teleGrid: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 10,
  },
  teleBox: {
    flex: 1,
    backgroundColor: COLORS.brandDark,
    borderRadius: 14,
    padding: 12,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    alignItems: "center",
  },
  teleVal: {
    color: COLORS.textPrimary,
    fontSize: 17,
    fontWeight: "900",
    marginTop: 6,
  },
  teleLabel: {
    color: COLORS.textMuted,
    fontSize: 11,
    marginTop: 2,
  },
  restStopBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: COLORS.brandDark,
    padding: 10,
    borderRadius: 12,
  },
  restStopText: {
    color: COLORS.brandBlueLight,
    fontSize: 11,
    fontWeight: "600",
  },
  btnRow: {
    flexDirection: "row",
    gap: 10,
  },
  completedNote: {
    color: "#34d399",
    fontSize: 12,
    fontWeight: "600",
    lineHeight: 18,
  },
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.82)",
    justifyContent: "center",
    padding: 18,
  },
  modalBox: {
    backgroundColor: COLORS.brandSurface,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    padding: 18,
  },
  modalHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 8,
  },
  modalTitle: {
    color: COLORS.textPrimary,
    fontSize: 15,
    fontWeight: "800",
  },
  modalInput: {
    backgroundColor: COLORS.brandDark,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    borderRadius: 12,
    padding: 12,
    color: COLORS.textPrimary,
    fontSize: 12,
    minHeight: 76,
    textAlignVertical: "top",
    marginVertical: 14,
  },
  coDriverItem: {
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    backgroundColor: COLORS.brandDark,
  },
  coDriverItemActive: {
    borderColor: COLORS.brandPrimary,
    backgroundColor: COLORS.brandBlueNight,
  },
  coDriverName: {
    color: COLORS.textPrimary,
    fontSize: 13,
    fontWeight: "700",
  },
  coDriverMeta: {
    color: COLORS.textMuted,
    fontSize: 11,
    marginTop: 2,
  },
});