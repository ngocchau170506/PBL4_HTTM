import { FileVideo, Scale } from "lucide-react-native";
import React, { useState } from "react";
import { Alert, ScrollView, StyleSheet, Text, View } from "react-native";
import { COLORS } from "../../../mobileConfig";
import AppButton from "../../components/common/AppButton";
import AppCard from "../../components/common/AppCard";
import StatusBadge from "../../components/common/StatusBadge";
import DisputeModal from "./DisputeModal";

export default function ViolationsScreen({ violations = [], onSubmitDispute }) {
  const [selectedForDispute, setSelectedForDispute] = useState(null);

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 16 }}>
      <Text style={styles.pageTitle}>
        Sự kiện Vi phạm AI & Khiếu nại trong 48h
      </Text>
      <Text style={styles.pageSub}>
        Chỉ hiển thị dữ liệu cá nhân của bạn. Các vi phạm CONFIRMED được quyền gửi khiếu nại 1 lần.
      </Text>

      {violations.map((v) => {
        const isConfirmed = v.trang_thai_xac_thuc === "CONFIRMED";
        const isRefunded =
          v.trang_thai_diem === "REFUNDED" || v.dispute_status === "APPROVED";
        const isDisputeRejected = v.dispute_status === "REJECTED";
        const isDisputeOpen = v.dispute_status === "OPEN";
        const canDispute = isConfirmed && !v.dispute_status && !isRefunded;

        return (
          <AppCard key={v.id}>
            <View style={styles.topRow}>
              <Text style={styles.vTitle}>
                #{v.id} • {v.ten_loai}
              </Text>
              <View style={{ alignItems: "flex-end" }}>
                <Text
                  style={[
                    styles.penaltyText,
                    isRefunded && {
                      color: COLORS.alertSafe,
                      textDecorationLine: "line-through",
                      fontSize: 12,
                    },
                  ]}
                >
                  -{v.diem_bi_tru} điểm
                </Text>
                {isRefunded && (
                  <Text
                    style={{
                      color: COLORS.alertSafe,
                      fontWeight: "bold",
                      fontSize: 11,
                    }}
                  >
                    +0đ (Đã hoàn lại)
                  </Text>
                )}
              </View>
            </View>

            <Text style={styles.timeText}>{v.thoi_gian}</Text>
            <Text style={styles.descText}>{v.mo_ta}</Text>

            <View style={styles.badgeRow}>
              {isRefunded ? (
                <StatusBadge label="ĐÃ HOÀN ĐIỂM KHIẾU NẠI" type="safe" />
              ) : isDisputeRejected ? (
                <>
                  <StatusBadge label="ĐÃ XÁC NHẬN TRỪ ĐIỂM" type="danger" />
                  <StatusBadge label="KHIẾU NẠI BỊ BÁC BỎ" type="danger" />
                </>
              ) : isDisputeOpen ? (
                <>
                  <StatusBadge label="ĐÃ XÁC NHẬN TRỪ ĐIỂM" type="danger" />
                  <StatusBadge
                    label="ĐANG CHỜ GIẢI QUYẾT KHIẾU NẠI"
                    type="info"
                  />
                </>
              ) : (
                <StatusBadge
                  label={
                    isConfirmed
                      ? "ĐÃ XÁC NHẬN TRỪ ĐIỂM"
                      : "CHỜ ĐIỀU PHỐI VIÊN DUYỆT"
                  }
                  type={isConfirmed ? "danger" : "warning"}
                />
              )}
            </View>

            {v.dispute_reason && (
              <View style={styles.disputeBox}>
                <Text style={styles.disputeText}>
                  Đơn khiếu nại đã gửi: “{v.dispute_reason}”
                </Text>
              </View>
            )}

            {v.dispute_response && (
              <View
                style={[
                  styles.disputeBox,
                  {
                    marginTop: 6,
                    borderColor: isRefunded
                      ? "rgba(52, 211, 153, 0.4)"
                      : "rgba(248, 113, 113, 0.4)",
                    backgroundColor: isRefunded
                      ? "rgba(6, 78, 59, 0.3)"
                      : "rgba(127, 29, 29, 0.3)",
                  },
                ]}
              >
                <Text
                  style={[
                    styles.disputeText,
                    { color: isRefunded ? COLORS.alertSafe : COLORS.alertDanger },
                  ]}
                >
                  Phản hồi từ Điều phối viên: “{v.dispute_response}”
                </Text>
              </View>
            )}

            <View style={styles.actionRow}>
              <AppButton
                title="Xem Clip 10s"
                variant="secondary"
                icon={FileVideo}
                onPress={() =>
                  Alert.alert(
                    `Bằng chứng Video #${v.id}`,
                    "Đang phát clip 10 giây trích xuất từ Ring Buffer RAM trên Raspberry Pi 4 (H.264 <= 2MB).",
                  )
                }
                style={{ flex: 1 }}
              />
              {canDispute && (
                <AppButton
                  title="Gửi Khiếu nại"
                  variant="primary"
                  icon={Scale}
                  onPress={() => setSelectedForDispute(v)}
                  style={{ flex: 1 }}
                />
              )}
            </View>
          </AppCard>
        );
      })}

      <DisputeModal
        visible={Boolean(selectedForDispute)}
        violation={selectedForDispute}
        onClose={() => setSelectedForDispute(null)}
        onSubmitDispute={onSubmitDispute}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.brandDeep,
  },
  pageTitle: {
    color: COLORS.textPrimary,
    fontSize: 17,
    fontWeight: "900",
  },
  pageSub: {
    color: COLORS.textMuted,
    fontSize: 11,
    marginTop: 4,
    marginBottom: 14,
    lineHeight: 16,
  },
  topRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  vTitle: {
    color: COLORS.textPrimary,
    fontSize: 14,
    fontWeight: "800",
  },
  penaltyText: {
    color: COLORS.alertDanger,
    fontSize: 14,
    fontWeight: "900",
  },
  timeText: {
    color: COLORS.textSubtle,
    fontSize: 11,
    marginTop: 2,
  },
  descText: {
    color: COLORS.textSecondary,
    fontSize: 12,
    lineHeight: 18,
    marginVertical: 10,
  },
  badgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 12,
  },
  disputeBox: {
    backgroundColor: COLORS.brandDark,
    padding: 10,
    borderRadius: 10,
    marginBottom: 12,
    borderLeftWidth: 3,
    borderLeftColor: COLORS.brandPrimary,
  },
  disputeText: {
    color: COLORS.brandBlueLight,
    fontSize: 11,
    fontStyle: "italic",
  },
  actionRow: {
    flexDirection: "row",
    gap: 10,
  },
});