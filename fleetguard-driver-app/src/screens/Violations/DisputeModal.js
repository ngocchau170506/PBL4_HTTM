import { Scale, Send, X } from "lucide-react-native";
import React, { useState } from "react";
import {
  Alert,
  Modal,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { COLORS } from "../../../mobileConfig";
import AppButton from "../../components/common/AppButton";

const REASONS = [
  { code: "FALSE_DETECTION", label: "AI nhận diện nhầm hành vi" },
  { code: "SENSOR_ERROR", label: "Nhiễu cảm biến MQ-135 / GPS" },
  { code: "NOT_ME", label: "Đã đổi tài nhưng chưa kịp cập nhật" },
  { code: "OTHER", label: "Lý do khác" },
];

export default function DisputeModal({
  visible,
  violation,
  onClose,
  onSubmitDispute,
}) {
  const [selectedCode, setSelectedCode] = useState("FALSE_DETECTION");
  const [explanation, setExplanation] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!violation) return null;

  const handleSend = async () => {
    if (submitting) return;
    if (!explanation.trim()) {
      Alert.alert("Thiếu thông tin", "Vui lòng nhập nội dung giải trình!");
      return;
    }
    setSubmitting(true);
    try {
      await onSubmitDispute({
        violationId: violation.id,
        reasonCode: selectedCode,
        explanation: explanation.trim(),
      });
      setExplanation("");
      onClose();
    } catch {
      // Lỗi đã được hiển thị qua Alert ở App.js
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide">
      <View style={styles.overlay}>
        <View style={styles.modalBox}>
          <View style={styles.header}>
            <View style={styles.titleRow}>
              <Scale size={18} color={COLORS.brandBlueSky} />
              <Text style={styles.title}>
                Khiếu nại Vi phạm #{violation.id}
              </Text>
            </View>
            <TouchableOpacity onPress={onClose}>
              <X size={20} color={COLORS.textMuted} />
            </TouchableOpacity>
          </View>

          <Text style={styles.subText}>
            Lỗi: {violation.ten_loai} (-{violation.diem_bi_tru} điểm) • Quyền
            khiếu nại trong 48 giờ theo nguyên tắc Four-Eyes (SRS § 4.3).
          </Text>

          <Text style={styles.label}>Chọn lý do khiếu nại *</Text>
          <View style={styles.reasonList}>
            {REASONS.map((r) => {
              const active = selectedCode === r.code;
              return (
                <TouchableOpacity
                  key={r.code}
                  onPress={() => setSelectedCode(r.code)}
                  style={[styles.reasonBtn, active && styles.reasonBtnActive]}
                >
                  <Text
                    style={[
                      styles.reasonText,
                      active && { color: COLORS.textPrimary },
                    ]}
                  >
                    {r.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.label}>Nội dung giải trình chi tiết *</Text>
          <TextInput
            value={explanation}
            onChangeText={setExplanation}
            multiline
            numberOfLines={3}
            placeholder="VD: Lúc đó tôi chỉnh lại tai nghe bộ đàm chứ không dùng điện thoại cá nhân..."
            placeholderTextColor={COLORS.textSubtle}
            style={styles.textarea}
          />

          <View style={styles.footer}>
            <AppButton
              title="Hủy"
              variant="secondary"
              onPress={onClose}
              style={{ flex: 1 }}
            />
            <AppButton
              title={submitting ? "Đang gửi..." : "Gửi đơn khiếu nại"}
              icon={Send}
              isLoading={submitting}
              onPress={handleSend}
              style={{ flex: 2 }}
            />
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.82)",
    justifyContent: "center",
    padding: 18,
  },
  modalBox: {
    backgroundColor: COLORS.brandSurface,
    borderRadius: 24,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    padding: 20,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  title: {
    color: COLORS.textPrimary,
    fontSize: 16,
    fontWeight: "800",
  },
  subText: {
    color: COLORS.textMuted,
    fontSize: 11,
    marginTop: 6,
    marginBottom: 14,
    lineHeight: 16,
  },
  label: {
    color: COLORS.textPrimary,
    fontSize: 12,
    fontWeight: "700",
    marginBottom: 8,
  },
  reasonList: {
    gap: 8,
    marginBottom: 14,
  },
  reasonBtn: {
    padding: 11,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    backgroundColor: COLORS.brandDark,
  },
  reasonBtnActive: {
    borderColor: COLORS.brandPrimary,
    backgroundColor: COLORS.brandBlueNight,
  },
  reasonText: {
    color: COLORS.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  textarea: {
    backgroundColor: COLORS.brandDark,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    borderRadius: 14,
    padding: 12,
    color: COLORS.textPrimary,
    fontSize: 12,
    minHeight: 80,
    textAlignVertical: "top",
    marginBottom: 16,
  },
  footer: {
    flexDirection: "row",
    gap: 10,
  },
});