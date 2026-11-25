import React, { useEffect, useState } from "react";
import { Alert, SafeAreaView, StatusBar, StyleSheet, View } from "react-native";
import { COLORS } from "./mobileConfig";
import BottomTabBar from "./src/components/layout/BottomTabBar";
import DriverHeader from "./src/components/layout/DriverHeader";
import LoginScreen from "./src/screens/Login/LoginScreen";
import ProfileScreen from "./src/screens/Profile/ProfileScreen";
import ScoreScreen from "./src/screens/Score/ScoreScreen";
import TripScreen from "./src/screens/Trip/TripScreen";
import ViolationsScreen from "./src/screens/Violations/ViolationsScreen";
import driverDataService from "./src/services/driverDataService";
import mobileSocket from "./src/services/mobileSocketService";

export default function App() {
  const [user, setUser] = useState(null);
  const [activeTab, setActiveTab] = useState("TRIP");
  const [isOfflineMode, setIsOfflineMode] = useState(false);

  const [shift, setShift] = useState(null);
  const [biometricProfile, setBiometricProfile] = useState(null);
  const [scoreLogs, setScoreLogs] = useState([]);
  const [violations, setViolations] = useState([]);

  const loadDashboard = async () => {
    const data = await driverDataService.getDriverDashboard();
    setUser(data.user);
    setShift(data.currentShift);
    setBiometricProfile(data.biometricProfile);
    setScoreLogs(data.scoreLogs);
    setViolations(data.violations);
  };

  useEffect(() => {
    driverDataService.initSession().then((session) => {
      if (session.hasSession && session.user) {
        setUser(session.user);
        loadDashboard();
      }
    });
  }, []);

  // KẾT NỐI SOCKET.IO THỜI GIAN THỰC CHO TÀI XẾ (P0/P1/P2 REALTIME)
  useEffect(() => {
    if (!user) return;

    const token = driverDataService.getAuthToken();
    const driverId = user.driver_id || user.id;

    mobileSocket.connect(token, driverId, {
      onScoreChanged: (data) => {
        // Cập nhật điểm uy tín hiển thị tức thì
        if (data.diem_sau !== undefined) {
          setUser((prev) => (prev ? { ...prev, diem_uy_tin: data.diem_sau, cap_canh_bao: data.cap_canh_bao || prev.cap_canh_bao } : null));
        }
        loadDashboard();

        const diff = data.so_diem_thay_doi;
        const sign = diff > 0 ? "+" : "";
        Alert.alert(
          "Biến Động Điểm Uy Tín",
          `${data.ly_do || "Cập nhật điểm uy tín"}\nThay đổi: ${sign}${diff ?? 0} điểm\nĐiểm hiện tại: ${data.diem_sau ?? user.diem_uy_tin}đ`
        );
      },
      onViolationNew: (data) => {
        loadDashboard();
        Alert.alert(
          "🚨 CẢNH BÁO VI PHẠM THỜI GIAN THỰC",
          `Phát hiện: ${data.ten_loai || data.ma_loai || "Hành vi mất an toàn"}\nSố điểm bị trừ: -${data.diem_bi_tru || 10}đ\nHãy tập trung lái xe hoặc ghé trạm dừng nghỉ!`
        );
      },
      onShiftUpdated: () => {
        loadDashboard();
      },
    });

    return () => {
      mobileSocket.disconnect();
    };
  }, [user?.id, user?.driver_id]);

  const handleLogout = async () => {
    mobileSocket.disconnect();
    await driverDataService.logout();
    setUser(null);
    setShift(null);
    setViolations([]);
    setScoreLogs([]);
  };

  const handleLogin = async (username, password, consent) => {
    const loggedUser = await driverDataService.login(
      username,
      password,
      consent,
    );
    setUser(loggedUser);
    await loadDashboard();
  };

  const handleRespondShift = async (decision, reason = "") => {
    try {
      const updated = await driverDataService.respondShift(decision, reason);
      setShift(updated);
      await loadDashboard();
    } catch (err) {
      Alert.alert("Lỗi thao tác ca", err.message || "Không thể phản hồi ca trực.");
      throw err;
    }
  };

  const handleCompleteCalibration = async (offline) => {
    try {
      const updated = await driverDataService.completeCalibration(offline);
      setShift(updated);
    } catch (err) {
      Alert.alert("Lỗi bắt đầu chuyến", err.message || "Không thể bắt đầu chuyến.");
      throw err;
    }
  };

  const handleToggleRest = async () => {
    try {
      const updated = await driverDataService.toggleResting();
      setShift(updated);
    } catch (err) {
      Alert.alert("Lỗi cập nhật trạng thái", err.message || "Không thể đổi trạng thái nghỉ.");
    }
  };

  const handleSwitchDriver = async (offline, coDriver = null) => {
    try {
      const updated = await driverDataService.switchCoDriver(offline, coDriver);
      setShift(updated);
    } catch (err) {
      Alert.alert("Lỗi đổi tài", err.message || "Không thể bàn giao tay lái.");
      throw err;
    }
  };

  const handleCompleteTrip = async () => {
    try {
      const updated = await driverDataService.completeTrip();
      setShift(updated);
      await loadDashboard();
    } catch (err) {
      Alert.alert("Lỗi kết thúc chuyến", err.message || "Không thể hoàn thành chuyến đi.");
    }
  };

  const handleSubmitDispute = async (payload) => {
    try {
      const updatedList = await driverDataService.submitDispute(payload);
      setViolations(updatedList);
      Alert.alert(
        "Đã gửi khiếu nại",
        "Đơn khiếu nại đã được đẩy lên Web Dashboard của Điều phối viên theo quy trình Four-Eyes!",
      );
    } catch (err) {
      Alert.alert("Lỗi gửi khiếu nại", err.message || "Không thể gửi đơn khiếu nại.");
      throw err;
    }
  };

  const handleUpdateProfile = async (updatedFields) => {
    const updatedUser = await driverDataService.updateProfile(updatedFields);
    setUser(updatedUser);
    await loadDashboard();
  };

  const handleChangePassword = async (pwdPayload) => {
    await driverDataService.changePassword(pwdPayload);
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar
        barStyle="light-content"
        backgroundColor={COLORS.brandDark}
        translucent={false}
      />
      {!user ? (
        <LoginScreen onLogin={handleLogin} />
      ) : (
        <View style={styles.mainWrap}>
          <DriverHeader
            user={user}
            isOfflineMode={isOfflineMode}
            onToggleOffline={() => setIsOfflineMode(!isOfflineMode)}
            onOpenProfile={() => setActiveTab("PROFILE")}
            onLogout={handleLogout}
          />

          <View style={styles.content}>
            {activeTab === "TRIP" && (
              <TripScreen
                shift={shift}
                isOfflineMode={isOfflineMode}
                onRespondShift={handleRespondShift}
                onCompleteCalibration={handleCompleteCalibration}
                onToggleRest={handleToggleRest}
                onSwitchDriver={handleSwitchDriver}
                onCompleteTrip={handleCompleteTrip}
              />
            )}

            {activeTab === "SCORE" && (
              <ScoreScreen
                user={user}
                biometricProfile={biometricProfile}
                scoreLogs={scoreLogs}
              />
            )}

            {activeTab === "VIOLATIONS" && (
              <ViolationsScreen
                violations={violations}
                onSubmitDispute={handleSubmitDispute}
              />
            )}

            {activeTab === "PROFILE" && (
              <ProfileScreen
                user={user}
                onUpdateProfile={handleUpdateProfile}
                onChangePassword={handleChangePassword}
              />
            )}
          </View>

          <BottomTabBar
            activeTab={activeTab}
            onChangeTab={setActiveTab}
            violationCount={violations.length}
          />
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: COLORS.brandDark,
  },
  mainWrap: {
    flex: 1,
    backgroundColor: COLORS.brandDeep,
  },
  content: {
    flex: 1,
  },
});