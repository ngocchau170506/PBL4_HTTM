import { ExternalLink, FileVideo, X } from "lucide-react-native";
import React from "react";
import {
  Alert,
  Linking,
  Modal,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { VideoView, useVideoPlayer } from "expo-video";
import { COLORS } from "../../../mobileConfig";
import AppButton from "../../components/common/AppButton";
import driverDataService from "../../services/driverDataService";

function ExpoVideoPlayerBox({ videoUrl }) {
  const player = useVideoPlayer(videoUrl, (p) => {
    p.loop = false;
    p.play();
  });

  return (
    <VideoView
      style={styles.videoView}
      player={player}
      allowsFullscreen
      allowsPictureInPicture
      nativeControls
      contentFit="contain"
    />
  );
}

export default function VideoEvidenceModal({ visible, violation, onClose }) {
  if (!violation) return null;

  const videoUrl = driverDataService.resolveMediaUrl(violation.video_url);

  const handleOpenExternal = async () => {
    if (!videoUrl) return;
    try {
      await Linking.openURL(videoUrl);
    } catch {
      Alert.alert("Lỗi", "Không thể mở trình phát video bên ngoài.");
    }
  };

  return (
    <Modal visible={visible} transparent animationType="fade">
      <View style={styles.overlay}>
        <View style={styles.modalBox}>
          <View style={styles.header}>
            <View style={styles.titleRow}>
              <FileVideo size={18} color={COLORS.brandBlueSky} />
              <Text style={styles.title}>
                Bằng chứng Video #{violation.id}
              </Text>
            </View>
            <TouchableOpacity onPress={onClose}>
              <X size={20} color={COLORS.textMuted} />
            </TouchableOpacity>
          </View>

          <Text style={styles.subText}>
            {violation.ten_loai || violation.ma_loai} • {violation.thoi_gian}
          </Text>

          {videoUrl ? (
            <View style={styles.videoBox}>
              <ExpoVideoPlayerBox videoUrl={videoUrl} />
            </View>
          ) : (
            <View style={styles.emptyVideo}>
              <Text style={styles.emptyTitle}>
                Sự kiện không có video đính kèm
              </Text>
              <Text style={styles.emptySub}>
                {violation.mo_ta ||
                  "Vi phạm cảm biến hoặc đang chờ thiết bị đồng bộ video lên máy chủ."}
              </Text>
            </View>
          )}

          <View style={styles.footer}>
            {videoUrl && (
              <AppButton
                title="Mở ngoài"
                variant="secondary"
                icon={ExternalLink}
                onPress={handleOpenExternal}
                style={{ flex: 1 }}
              />
            )}
            <AppButton
              title="Đóng"
              variant="primary"
              onPress={onClose}
              style={{ flex: 1 }}
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
    backgroundColor: "rgba(0,0,0,0.85)",
    justifyContent: "center",
    padding: 16,
  },
  modalBox: {
    backgroundColor: COLORS.brandSurface,
    borderRadius: 22,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    padding: 18,
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
    marginTop: 4,
    marginBottom: 12,
  },
  videoBox: {
    width: "100%",
    height: 220,
    borderRadius: 14,
    overflow: "hidden",
    backgroundColor: "#000",
    marginBottom: 14,
  },
  videoView: {
    width: "100%",
    height: "100%",
  },
  emptyVideo: {
    padding: 20,
    borderRadius: 14,
    backgroundColor: COLORS.brandDark,
    borderWidth: 1,
    borderColor: COLORS.brandBorder,
    marginBottom: 14,
  },
  emptyTitle: {
    color: COLORS.alertWarning,
    fontSize: 13,
    fontWeight: "700",
    marginBottom: 4,
  },
  emptySub: {
    color: COLORS.textMuted,
    fontSize: 11,
    lineHeight: 16,
  },
  footer: {
    flexDirection: "row",
    gap: 10,
  },
});
