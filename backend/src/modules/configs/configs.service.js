import prisma from '../../common/prisma.js';
import { logAuditAction } from '../../common/audit.service.js';

const DEFAULT_CONFIG = {
  fleet_scope: 'GLOBAL',
  microsleep_eye_closed_sec: 1.5,
  head_down_sec: 1.0,
  yawn_count_threshold: 3,
  phone_use_sec: 3.0,
  co2_warning_threshold_ppm: 1000,
  mq135_warmup_sec: 180,
  auto_confirm_min: 0.85,
  manual_review_min: 0.6,
  weight_landmarks: 0.4,
  weight_cnn: 0.4,
  weight_edge: 0.2,
  penalty_cap_per_trip: 30,
  penalty_cap_per_day: 50,
  manual_review_sla_hours: 24,
  dispute_window_hours: 48,
  min_rest_hours: 8.0,
  timezone: 'Asia/Ho_Chi_Minh',
};

export const getAiConfig = async () => {
  let dbConfig = await prisma.systemConfig.findFirst({
    where: { fleetScope: 'GLOBAL' },
    orderBy: { id: 'desc' },
  });

  if (!dbConfig) {
    dbConfig = await prisma.systemConfig.create({
      data: {
        fleetScope: 'GLOBAL',
      },
    });
  }

  return {
    id: dbConfig.id,
    fleet_scope: dbConfig.fleetScope,
    microsleep_eye_closed_sec: dbConfig.microsleepEyeClosedSec,
    head_down_sec: dbConfig.headDownSec,
    yawn_count_threshold: dbConfig.yawnCountThreshold,
    phone_use_sec: dbConfig.phoneUseSec,
    co2_warning_threshold_ppm: dbConfig.co2WarningThresholdPpm,
    mq135_warmup_sec: dbConfig.mq135WarmupSec,
    auto_confirm_min: dbConfig.autoConfirmMin,
    manual_review_min: dbConfig.manualReviewMin,
    weight_landmarks: dbConfig.weightLandmarks,
    weight_cnn: dbConfig.weightCnn,
    weight_edge: dbConfig.weightEdge,
    penalty_cap_per_trip: dbConfig.penaltyCapPerTrip,
    penalty_cap_per_day: dbConfig.penaltyCapPerDay,
    manual_review_sla_hours: dbConfig.manualReviewSlaHours,
    dispute_window_hours: dbConfig.disputeWindowHours,
    min_rest_hours: dbConfig.minRestHours,
    timezone: dbConfig.timezone,
    updated_at: dbConfig.updatedAt,
  };
};

export const updateAiConfig = async (newConfig, currentUser) => {
  const current = await getAiConfig();

  const updateData = {};
  if (newConfig.fleet_scope !== undefined) updateData.fleetScope = newConfig.fleet_scope;
  if (newConfig.microsleep_eye_closed_sec !== undefined) updateData.microsleepEyeClosedSec = parseFloat(newConfig.microsleep_eye_closed_sec);
  if (newConfig.head_down_sec !== undefined) updateData.headDownSec = parseFloat(newConfig.head_down_sec);
  if (newConfig.yawn_count_threshold !== undefined) updateData.yawnCountThreshold = parseInt(newConfig.yawn_count_threshold, 10);
  if (newConfig.phone_use_sec !== undefined) updateData.phoneUseSec = parseFloat(newConfig.phone_use_sec);
  if (newConfig.co2_warning_threshold_ppm !== undefined) updateData.co2WarningThresholdPpm = parseFloat(newConfig.co2_warning_threshold_ppm);
  if (newConfig.mq135_warmup_sec !== undefined) updateData.mq135WarmupSec = parseInt(newConfig.mq135_warmup_sec, 10);
  if (newConfig.auto_confirm_min !== undefined) updateData.autoConfirmMin = parseFloat(newConfig.auto_confirm_min);
  if (newConfig.manual_review_min !== undefined) updateData.manualReviewMin = parseFloat(newConfig.manual_review_min);
  if (newConfig.weight_landmarks !== undefined) updateData.weightLandmarks = parseFloat(newConfig.weight_landmarks);
  if (newConfig.weight_cnn !== undefined) updateData.weightCnn = parseFloat(newConfig.weight_cnn);
  if (newConfig.weight_edge !== undefined) updateData.weightEdge = parseFloat(newConfig.weight_edge);
  if (newConfig.penalty_cap_per_trip !== undefined) updateData.penaltyCapPerTrip = parseInt(newConfig.penalty_cap_per_trip, 10);
  if (newConfig.penalty_cap_per_day !== undefined) updateData.penaltyCapPerDay = parseInt(newConfig.penalty_cap_per_day, 10);
  if (newConfig.manual_review_sla_hours !== undefined) updateData.manualReviewSlaHours = parseInt(newConfig.manual_review_sla_hours, 10);
  if (newConfig.dispute_window_hours !== undefined) updateData.disputeWindowHours = parseInt(newConfig.dispute_window_hours, 10);
  if (newConfig.min_rest_hours !== undefined) updateData.minRestHours = parseFloat(newConfig.min_rest_hours);
  if (newConfig.timezone !== undefined) updateData.timezone = newConfig.timezone;

  const updated = await prisma.systemConfig.update({
    where: { id: current.id },
    data: updateData,
  });

  logAuditAction({
    userId: currentUser?.id || 1,
    hanhDong: 'UPDATE_SYSTEM_CONFIG',
    doiTuongLoai: 'SYSTEM_CONFIGS',
    doiTuongId: String(updated.id),
    giaTriTruoc: current,
    giaTriSau: updateData,
    lyDo: `Cập nhật tham số AI & Quy tắc vận hành (${newConfig.fleet_scope || 'GLOBAL'})`,
  });

  return getAiConfig();
};
