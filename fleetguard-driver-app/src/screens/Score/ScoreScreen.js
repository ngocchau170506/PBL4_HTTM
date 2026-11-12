import {
  ArrowDownRight,
  ArrowUpRight,
  Clock,
  HeartPulse,
  ShieldAlert,
} from "lucide-react-native";
import React from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { COLORS } from "../../../mobileConfig";
import AppCard from "../../components/common/AppCard";
import StatusBadge from "../../components/common/StatusBadge";

export default function ScoreScreen({ user, biometricProfile, scoreLogs = [] }) {
  const currentScore = user?.diem_uy_tin ?? 100;
  const isRed = currentScore <= 20;
  const isYellow = currentScore <= 65;
  const badgeLabel = isRed ? "BÁO ĐỘNG ĐỎ" : isYellow ? "CẢNH CÁO VÀNG" : "HOẠT ĐỘNG TỐT";
  const badgeType = isRed ? "danger" : isYellow ? "warning" : "safe";
  const cardVariant = isRed ? "danger" : isYellow ? "warning" : "active";

  const dynamicAssessment = (biometricProfile?.danh_gia || "Hồ sơ sức khỏe ổn định.")
    .replace(/Điểm uy tín \d+/, `Điểm uy tín ${currentScore}`);

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 16 }}>
      {/* VÒNG ĐIỂM UY TÍN HIỆN TẠI */}
      <AppCard variant={cardVariant} style={styles.scoreHero}>
        <View style={styles.scoreCircle}>
          <Text style={styles.scoreNum}>{currentScore}</Text>
          <Text style={styles.scoreMax}>100 điểm</Text>
        </View>
        <View style={{ flex: 1 }}>
          <StatusBadge label={badgeLabel} type={badgeType} />
          <Text style={styles.heroTitle}>Hồ sơ Uy tín Tài xế</Text>
          <Text style={styles.heroSub}>
            {isRed
              ? "Báo động đỏ (Điểm <= 20): Tài khoản bị tạm khóa nhận ca mới. Cần liên hệ Quản trị viên để xét duyệt mở lại."
              : isYellow
                ? "Điểm ≤ 65: Giảm 30% tần suất gợi ý ca đêm. Nếu rơi xuống ≤ 20 điểm sẽ bị khóa cứng nhận ca."
                : "Điểm > 65: Tài xế đạt chuẩn an toàn cao. Đủ điều kiện nhận ca đêm và hành trình dài."}
          </Text>
        </View>
      </AppCard>

      {/* HỒ SƠ SINH HỌC 30 NGÀY (SRS § 6.4) */}
      <AppCard>
        <View style={styles.rowHeader}>
          <HeartPulse size={18} color={COLORS.brandBlueSky} />
          <Text style={styles.sectionTitle}>
            Hồ sơ Sinh học Cá nhân (30 ngày gần nhất)
          </Text>
        </View>
        <View style={styles.bioGrid}>
          <View style={styles.bioItem}>
            <Clock size={15} color={COLORS.alertWarning} />
            <Text style={styles.bioLabel}>Khung giờ yếu sinh học</Text>
            <Text style={styles.bioVal}>
              {biometricProfile?.khung_gio_yeu_sinh_hoc?.join(", ")}
            </Text>
          </View>
          <View style={styles.bioItem}>
            <ShieldAlert size={15} color={COLORS.brandBlueSky} />
            <Text style={styles.bioLabel}>Lái tối đa khuyến nghị</Text>
            <Text style={styles.bioVal}>
              {(biometricProfile?.thoi_gian_lai_toi_da_khuyen_nghi_phut || 180) /
                60}{" "}
              Giờ / ca
            </Text>
          </View>
        </View>
        <Text style={styles.bioDesc}>{dynamicAssessment}</Text>
      </AppCard>

      {/* LỊCH SỬ SỔ CÁI ĐIỂM UY TÍN (driver_score_logs) */}
      <AppCard>
        <Text style={styles.sectionTitle}>
          Lịch sử Biến động Sổ cái
        </Text>
        {scoreLogs.map((log) => {
          const isMinus = log.so_diem_thay_doi < 0;
          return (
            <View key={log.id} style={styles.logRow}>
              <View
                style={[
                  styles.iconBox,
                  {
                    backgroundColor: isMinus
                      ? COLORS.alertDangerBg
                      : COLORS.alertSafeBg,
                  },
                ]}
              >
                {isMinus ? (
                  <ArrowDownRight size={16} color={COLORS.alertDanger} />
                ) : (
                  <ArrowUpRight size={16} color={COLORS.alertSafe} />
                )}
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.logReason}>{log.ly_do}</Text>
                <Text style={styles.logTime}>{log.created_at}</Text>
              </View>
              <View style={{ alignItems: "flex-end" }}>
                <Text
                  style={[
                    styles.logDelta,
                    { color: isMinus ? COLORS.alertDanger : COLORS.alertSafe },
                  ]}
                >
                  {log.so_diem_thay_doi > 0
                    ? `+${log.so_diem_thay_doi}`
                    : log.so_diem_thay_doi}
                </Text>
                <Text style={styles.logBalance}>
                  {log.diem_truoc} → {log.diem_sau}
                </Text>
              </View>
            </View>
          );
        })}
      </AppCard>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.brandDeep,
  },
  scoreHero: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
  },
  scoreCircle: {
    width: 84,
    height: 84,
    borderRadius: 42,
    backgroundColor: COLORS.brandDeep,
    borderWidth: 3,
    borderColor: COLORS.alertWarning,
    alignItems: "center",
    justifyContent: "center",
  },
  scoreNum: {
    color: COLORS.alertWarning,
    fontSize: 26,
    fontWeight: "900",
  },
  scoreMax: {
    color: COLORS.textMuted,
    fontSize: 10,
  },
  heroTitle: {
    color: COLORS.textPrimary,
    fontSize: 16,
    fontWeight: "800",
    marginTop: 6,
  },
  heroSub: {
    color: COLORS.textSecondary,
    fontSize: 11,
    lineHeight: 16,
    marginTop: 4,
  },
  rowHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 12,
  },
  sectionTitle: {
    color: COLORS.textPrimary,
    fontSize: 14,
    fontWeight: "800",
  },
  bioGrid: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 10,
  },
  bioItem: {
    flex: 1,
    backgroundColor: COLORS.brandDark,
    padding: 12,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
  },
  bioLabel: {
    color: COLORS.textMuted,
    fontSize: 11,
    marginTop: 6,
  },
  bioVal: {
    color: COLORS.textPrimary,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 2,
  },
  bioDesc: {
    color: COLORS.textSecondary,
    fontSize: 11,
    lineHeight: 16,
  },
  logRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.brandBorderDark,
  },
  iconBox: {
    width: 34,
    height: 34,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
  },
  logReason: {
    color: COLORS.textPrimary,
    fontSize: 12,
    fontWeight: "700",
  },
  logTime: {
    color: COLORS.textSubtle,
    fontSize: 10,
    marginTop: 2,
  },
  logDelta: {
    fontSize: 14,
    fontWeight: "900",
  },
  logBalance: {
    color: COLORS.textSubtle,
    fontSize: 10,
  },
});