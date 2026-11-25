import {
  CheckSquare,
  Server,
  ShieldCheck,
  Square,
  Truck,
} from "lucide-react-native";
import React, { useEffect, useState } from "react";
import {
  Alert,
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
import driverDataService from "../../services/driverDataService";

export default function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("tx_hoangnam");
  const [password, setPassword] = useState("password123");
  const [serverUrl, setServerUrl] = useState(driverDataService.getServerUrl());
  const [showServerConfig, setShowServerConfig] = useState(false);
  const [consent, setConsent] = useState(true);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setServerUrl(driverDataService.getServerUrl());
  }, []);

  const handleSubmit = async () => {
    setLoading(true);
    try {
      await driverDataService.setServerUrl(serverUrl);
      await onLogin(username, password, consent);
    } catch (err) {
      Alert.alert("Thông báo", err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScrollView
      style={styles.scroll}
      contentContainerStyle={styles.container}
      keyboardShouldPersistTaps="handled"
    >
      <View style={styles.brandBox}>
        <View style={styles.logoCircle}>
          <Truck size={34} color={COLORS.brandBlueSky} />
        </View>
        <Text style={styles.appName}>FleetGuard Driver</Text>
        <Text style={styles.appTag}>
          HỆ THỐNG AN TOÀN BUỒNG LÁI & ĐIỀU PHỐI CA
        </Text>
      </View>

      <AppCard style={styles.formCard}>
        <Text style={styles.formTitle}>Đăng nhập Tài xế</Text>
        <Text style={styles.formSub}>
          Kết nối đồng bộ với thiết bị Raspberry Pi 4 & Cloud
        </Text>

        <View style={styles.field}>
          <Text style={styles.label}>Mã tài khoản tài xế *</Text>
          <TextInput
            value={username}
            onChangeText={setUsername}
            placeholder="VD: tx_hoangnam"
            placeholderTextColor={COLORS.textSubtle}
            style={styles.input}
            autoCapitalize="none"
          />
        </View>

        <View style={styles.field}>
          <Text style={styles.label}>Mật khẩu *</Text>
          <TextInput
            value={password}
            onChangeText={setPassword}
            placeholder="Nhập mật khẩu..."
            placeholderTextColor={COLORS.textSubtle}
            secureTextEntry
            style={styles.input}
          />
        </View>

        {/* Điều khoản đồng ý dữ liệu sinh trắc học theo Nghị định 13/2023/NĐ-CP */}
        <TouchableOpacity
          activeOpacity={0.8}
          onPress={() => setConsent(!consent)}
          style={styles.consentRow}
        >
          {consent ? (
            <CheckSquare size={20} color={COLORS.brandPrimary} />
          ) : (
            <Square size={20} color={COLORS.textMuted} />
          )}
          <Text style={styles.consentText}>
            Tôi đồng ý cho phép thu thập{" "}
            <Text style={styles.highlight}>
              Dữ liệu sinh trắc học khuôn mặt (AES-256)
            </Text>{" "}
            để hiệu chuẩn chống ngủ gật theo Nghị định 13/2023/NĐ-CP.
          </Text>
        </TouchableOpacity>

        <AppButton
          title="Đăng nhập Ca trực"
          icon={ShieldCheck}
          isLoading={loading}
          onPress={handleSubmit}
          style={{ marginTop: 10 }}
        />

        <TouchableOpacity
          activeOpacity={0.8}
          onPress={() => setShowServerConfig(!showServerConfig)}
          style={styles.serverToggleBtn}
        >
          <Server size={14} color={COLORS.brandBlueSky} />
          <Text style={styles.serverToggleText}>
            {showServerConfig
              ? "Ẩn cấu hình địa chỉ Server"
              : `Server: ${serverUrl} (Chạm để đổi IP LAN)`}
          </Text>
        </TouchableOpacity>

        {showServerConfig && (
          <View style={styles.serverBox}>
            <Text style={styles.label}>
              Địa chỉ Server (IP LAN máy chạy Docker / Backend)
            </Text>
            <TextInput
              value={serverUrl}
              onChangeText={setServerUrl}
              placeholder="VD: http://192.168.1.100 (Docker :80) hoặc :3000"
              placeholderTextColor={COLORS.textSubtle}
              style={styles.input}
              autoCapitalize="none"
              autoCorrect={false}
            />
          </View>
        )}
      </AppCard>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: {
    flex: 1,
    backgroundColor: COLORS.brandDeep,
  },
  container: {
    flexGrow: 1,
    backgroundColor: COLORS.brandDeep,
    justifyContent: "center",
    padding: 20,
  },
  brandBox: {
    alignItems: "center",
    marginBottom: 24,
  },
  logoCircle: {
    width: 68,
    height: 68,
    borderRadius: 22,
    backgroundColor: COLORS.brandBlueNight,
    borderWidth: 1.5,
    borderColor: COLORS.brandPrimary,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 12,
  },
  appName: {
    color: COLORS.textPrimary,
    fontSize: 24,
    fontWeight: "900",
  },
  appTag: {
    color: COLORS.brandBlueSky,
    fontSize: 10,
    fontWeight: "700",
    marginTop: 4,
    letterSpacing: 0.8,
  },
  formCard: {
    padding: 22,
  },
  formTitle: {
    color: COLORS.textPrimary,
    fontSize: 18,
    fontWeight: "800",
  },
  formSub: {
    color: COLORS.textMuted,
    fontSize: 12,
    marginTop: 2,
    marginBottom: 18,
  },
  field: {
    marginBottom: 14,
  },
  label: {
    color: COLORS.textPrimary,
    fontSize: 12,
    fontWeight: "700",
    marginBottom: 6,
  },
  input: {
    backgroundColor: COLORS.brandDark,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 11,
    color: COLORS.textPrimary,
    fontSize: 13,
  },
  consentRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    marginVertical: 10,
    backgroundColor: COLORS.brandDark,
    padding: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
  },
  consentText: {
    flex: 1,
    color: COLORS.textSecondary,
    fontSize: 11,
    lineHeight: 16,
  },
  highlight: {
    color: COLORS.brandBlueSky,
    fontWeight: "700",
  },
  serverToggleBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: 14,
    paddingVertical: 6,
  },
  serverToggleText: {
    color: COLORS.brandBlueSky,
    fontSize: 11,
    fontWeight: "600",
  },
  serverBox: {
    marginTop: 8,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: COLORS.brandBorder,
  },
});