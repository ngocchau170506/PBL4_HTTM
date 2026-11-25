import {
  CheckCircle2,
  KeyRound,
  Lock,
  Save,
  ShieldCheck,
  UserCheck,
} from "lucide-react-native";
import React, { useEffect, useState } from "react";
import {
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { COLORS } from "../../../mobileConfig";
import AppButton from "../../components/common/AppButton";
import AppCard from "../../components/common/AppCard";
import StatusBadge from "../../components/common/StatusBadge";

export default function ProfileScreen({
  user,
  onUpdateProfile,
  onChangePassword,
}) {
  const [formData, setFormData] = useState({
    ho_ten: user?.ho_ten || "",
    so_dien_thoai: user?.so_dien_thoai || "",
  });

  useEffect(() => {
    setFormData({
      ho_ten: user?.ho_ten || "",
      so_dien_thoai: user?.so_dien_thoai || "",
    });
  }, [user?.id, user?.ho_ten, user?.so_dien_thoai]);

  const [pwdData, setPwdData] = useState({
    currentPassword: "",
    newPassword: "",
    confirmPassword: "",
  });

  const [savingProfile, setSavingProfile] = useState(false);
  const [savingPwd, setSavingPwd] = useState(false);

  const handleSaveProfile = async () => {
    if (!formData.ho_ten.trim() || !formData.so_dien_thoai.trim()) {
      Alert.alert("Thông báo", "Họ tên và Số điện thoại không được để trống!");
      return;
    }
    setSavingProfile(true);
    try {
      await onUpdateProfile(formData);
      Alert.alert(
        "Cập nhật thành công",
        "Thông tin hồ sơ cá nhân của bạn đã được lưu và đồng bộ!",
      );
    } catch (err) {
      Alert.alert("Lỗi", err.message);
    } finally {
      setSavingProfile(false);
    }
  };

  const handleSavePassword = async () => {
    if (!pwdData.currentPassword || !pwdData.newPassword) {
      Alert.alert(
        "Thiếu thông tin",
        "Vui lòng nhập mật khẩu hiện tại và mật khẩu mới!",
      );
      return;
    }
    if (pwdData.newPassword !== pwdData.confirmPassword) {
      Alert.alert(
        "Không khớp",
        "Mật khẩu xác nhận không trùng với mật khẩu mới!",
      );
      return;
    }
    setSavingPwd(true);
    try {
      await onChangePassword(pwdData);
      setPwdData({ currentPassword: "", newPassword: "", confirmPassword: "" });
      Alert.alert(
        "Đổi mật khẩu thành công",
        "Mật khẩu mới đã được mã hóa bcrypt và áp dụng cho phiên đăng nhập!",
      );
    } catch (err) {
      Alert.alert("Lỗi", err.message);
    } finally {
      setSavingPwd(false);
    }
  };

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{ padding: 16, paddingBottom: 32 }}
    >
      {/* THẺ TÓM TẮT TÀI KHOẢN */}
      <AppCard variant="active" style={styles.heroCard}>
        <View style={styles.avatarBig}>
          <Text style={styles.avatarBigText}>
            {formData.ho_ten ? formData.ho_ten.charAt(0) : "T"}
          </Text>
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.heroName}>{formData.ho_ten || user?.ho_ten || "Tài xế"}</Text>
          <Text style={styles.heroUsername}>
            Tài khoản: @{user?.username} • Mã TX: #DRV-{user?.driver_id || user?.id || "--"}
          </Text>
          <View style={{ marginTop: 8 }}>
            <StatusBadge
              label={`${user?.hang_bang_lai || "Chưa cập nhật"} • Số: ${user?.so_bang_lai || "---"}`}
              type="info"
            />
          </View>
        </View>
      </AppCard>

      {/* FORM 1: CẬP NHẬT THÔNG TIN CÁ NHÂN */}
      <AppCard>
        <View style={styles.sectionHeader}>
          <UserCheck size={18} color={COLORS.brandBlueSky} />
          <Text style={styles.sectionTitle}>Thông tin Hồ sơ Cá nhân</Text>
        </View>

        <View style={styles.field}>
          <Text style={styles.label}>Họ và tên tài xế *</Text>
          <TextInput
            value={formData.ho_ten}
            onChangeText={(val) => setFormData({ ...formData, ho_ten: val })}
            style={styles.input}
            placeholder="Nhập họ và tên..."
            placeholderTextColor={COLORS.textSubtle}
          />
        </View>

        <View style={styles.row2}>
          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.label}>Số điện thoại *</Text>
            <TextInput
              value={formData.so_dien_thoai}
              onChangeText={(val) =>
                setFormData({ ...formData, so_dien_thoai: val })
              }
              keyboardType="phone-pad"
              style={styles.input}
              placeholder="0905..."
              placeholderTextColor={COLORS.textSubtle}
            />
          </View>

          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.label}>
              Số GPLX ({user?.hang_bang_lai || "Chưa cập nhật"})
            </Text>
            <TextInput
              value={user?.so_bang_lai || "Chưa cập nhật"}
              editable={false}
              style={[styles.input, styles.inputDisabled]}
            />
            <Text style={styles.readOnlyNote}>
              Chỉ Quản trị viên có quyền cập nhật số GPLX
            </Text>
          </View>
        </View>

        <AppButton
          title="Lưu Thông tin Cá nhân"
          icon={Save}
          isLoading={savingProfile}
          onPress={handleSaveProfile}
          style={{ marginTop: 4 }}
        />
      </AppCard>

      {/* FORM 2: ĐỔI MẬT KHẨU BẢO MẬT */}
      <AppCard>
        <View style={styles.sectionHeader}>
          <KeyRound size={18} color={COLORS.alertWarning} />
          <Text style={styles.sectionTitle}>Đổi Mật khẩu Đăng nhập</Text>
        </View>

        <View style={styles.field}>
          <Text style={styles.label}>Mật khẩu hiện tại *</Text>
          <TextInput
            value={pwdData.currentPassword}
            onChangeText={(val) =>
              setPwdData({ ...pwdData, currentPassword: val })
            }
            secureTextEntry
            placeholder="Nhập mật khẩu đang dùng..."
            placeholderTextColor={COLORS.textSubtle}
            style={styles.input}
          />
        </View>

        <View style={styles.row2}>
          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.label}>Mật khẩu mới *</Text>
            <TextInput
              value={pwdData.newPassword}
              onChangeText={(val) =>
                setPwdData({ ...pwdData, newPassword: val })
              }
              secureTextEntry
              placeholder="Tối thiểu 6 ký tự"
              placeholderTextColor={COLORS.textSubtle}
              style={styles.input}
            />
          </View>

          <View style={[styles.field, { flex: 1 }]}>
            <Text style={styles.label}>Nhập lại mật khẩu mới *</Text>
            <TextInput
              value={pwdData.confirmPassword}
              onChangeText={(val) =>
                setPwdData({ ...pwdData, confirmPassword: val })
              }
              secureTextEntry
              placeholder="Xác nhận lại"
              placeholderTextColor={COLORS.textSubtle}
              style={styles.input}
            />
          </View>
        </View>

        <AppButton
          title="Cập nhật Mật khẩu"
          variant="secondary"
          icon={Lock}
          isLoading={savingPwd}
          onPress={handleSavePassword}
          style={{ marginTop: 4 }}
        />
      </AppCard>

      {/* KHỐI 3: TRẠNG THÁI DỮ LIỆU SINH TRẮC HỌC (NFR_6) */}
      <AppCard variant="safe">
        <View style={styles.sectionHeader}>
          <ShieldCheck size={18} color={COLORS.alertSafe} />
          <Text style={styles.sectionTitle}>
            Bảo mật Sinh trắc học (Nghị định 13/2023/NĐ-CP)
          </Text>
        </View>
        <Text style={styles.privacyText}>
          • Vector khuôn mặt gốc đã được mã hóa chuẩn{" "}
          <Text style={{ fontWeight: "800", color: COLORS.textPrimary }}>
            AES-256-GCM
          </Text>
          .{"\n"}• Phiên bản đồng ý: {user?.privacy_consent_version || "v1.2-ND13/2023"} (Ngày ký:{" "}
          {user?.privacy_consent_at || "Khi đăng nhập"}).
        </Text>
        <View style={styles.consentBadgeRow}>
          <CheckCircle2 size={15} color={COLORS.alertSafe} />
          <Text style={styles.consentActiveText}>
            Đang kích hoạt bảo vệ danh tính khi hiệu chuẩn đầu ca
          </Text>
        </View>
      </AppCard>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.brandDeep,
  },
  heroCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
  },
  avatarBig: {
    width: 64,
    height: 64,
    borderRadius: 20,
    backgroundColor: COLORS.brandBlueNight,
    borderWidth: 2,
    borderColor: COLORS.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarBigText: {
    color: COLORS.brandBlueSky,
    fontSize: 26,
    fontWeight: "900",
  },
  heroName: {
    color: COLORS.textPrimary,
    fontSize: 18,
    fontWeight: "900",
  },
  heroUsername: {
    color: COLORS.textMuted,
    fontSize: 12,
    marginTop: 2,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 14,
  },
  sectionTitle: {
    color: COLORS.textPrimary,
    fontSize: 14,
    fontWeight: "800",
  },
  field: {
    marginBottom: 12,
  },
  row2: {
    flexDirection: "row",
    gap: 10,
  },
  label: {
    color: COLORS.textSecondary,
    fontSize: 12,
    fontWeight: "700",
    marginBottom: 6,
  },
  input: {
    backgroundColor: COLORS.brandDark,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: COLORS.textPrimary,
    fontSize: 13,
    fontWeight: "600",
  },
  inputDisabled: {
    opacity: 0.7,
    color: COLORS.textMuted,
  },
  readOnlyNote: {
    color: COLORS.textSubtle,
    fontSize: 10,
    marginTop: 4,
    fontStyle: "italic",
  },
  privacyText: {
    color: COLORS.textSecondary,
    fontSize: 12,
    lineHeight: 19,
  },
  consentBadgeRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 10,
  },
  consentActiveText: {
    color: COLORS.alertSafe,
    fontSize: 12,
    fontWeight: "700",
  },
});