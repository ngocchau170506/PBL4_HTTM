import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Clock,
  HeartPulse,
  History,
  UserCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import BaseModal from "../../../components/common/BaseModal";
import Button from "../../../components/common/Button";
import dataService from "../../../services/dataService";
import { formatFullDateTime } from "../../../utils/formatDateTime";

export default function DriverProfileModal({ isOpen, onClose, driver }) {
  const [activeTab, setActiveTab] = useState("BIOMETRIC");
  const [bioProfile, setBioProfile] = useState(null);
  const [scoreLogs, setScoreLogs] = useState([]);

  useEffect(() => {
    if (!isOpen || !driver) return;
    const driverId = driver.id || driver.user_id;

    const loadProfileDetails = async () => {
      const [bio, logs] = await Promise.all([
        dataService.getDriverBiometrics(driverId),
        dataService.getDriverScoreLogs(driverId),
      ]);
      setBioProfile(
        bio || {
          khung_gio_yeu_sinh_hoc: ["13:00 - 15:00"],
          thoi_gian_lai_toi_da_khuyen_nghi: 240,
          thoi_gian_lai_trung_binh_truoc_met_moi: 180,
          danh_gia_chung:
            "Thể trạng ổn định, nhịp sinh học giảm nhẹ vào đầu giờ chiều.",
        },
      );
      setScoreLogs(logs || []);
    };

    loadProfileDetails();
  }, [isOpen, driver]);

  if (!isOpen || !driver) return null;

  const driverName = driver.user?.ho_ten || driver.ho_ten || "Tài xế";
  const currentScore = driver.diem_uy_tin ?? driver.uy_tin ?? 100;

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      icon={UserCheck}
      maxWidth="max-w-2xl"
      title={`Hồ sơ Tài xế: ${driverName}`}
      subtitle={`Bằng ${driver.hang_bang_lai || "FC"} • Điểm uy tín hiện tại: ${currentScore}/100`}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Đóng
        </Button>
      }
    >
      <div className="space-y-5">
        {/* THANH CHUYỂN TAB */}
        <div className="border-brand-border-dark flex border-b">
          <button
            type="button"
            onClick={() => setActiveTab("BIOMETRIC")}
            className={`mr-6 flex cursor-pointer items-center gap-2 border-b-2 pb-3 text-xs font-bold transition-all ${
              activeTab === "BIOMETRIC"
                ? "border-brand-primary text-brand-blue-sky"
                : "text-brand-text-muted hover:text-brand-text-primary border-transparent"
            }`}
          >
            <HeartPulse className="h-4 w-4" />
            <span>Hồ sơ Sinh học (30 ngày)</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("SCORE_LOGS")}
            className={`flex cursor-pointer items-center gap-2 border-b-2 pb-3 text-xs font-bold transition-all ${
              activeTab === "SCORE_LOGS"
                ? "border-brand-primary text-brand-blue-sky"
                : "text-brand-text-muted hover:text-brand-text-primary border-transparent"
            }`}
          >
            <History className="h-4 w-4" />
            <span>Sổ cái Điểm Uy tín ({scoreLogs.length})</span>
          </button>
        </div>

        {/* NỘI DUNG TAB */}
        {activeTab === "BIOMETRIC" ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
              <div className="border-brand-border-dark bg-brand-dark/70 rounded-2xl border p-4">
                <div className="text-alert-warning flex items-center gap-1.5 font-bold">
                  <Clock className="h-4 w-4" />
                  <span>Khung giờ yếu sinh học</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {bioProfile?.khung_gio_yeu_sinh_hoc?.map((slot, idx) => (
                    <span
                      key={idx}
                      className="border-alert-warning/30 bg-alert-warning/15 text-alert-warning rounded-lg border px-2.5 py-1 font-bold"
                    >
                      {slot}
                    </span>
                  ))}
                </div>
                <p className="text-brand-text-subtle mt-2 text-[10px]">
                  Hệ thống sẽ cảnh báo khi phân ca giao thoa với khung giờ này
                </p>
              </div>

              <div className="border-brand-border-dark bg-brand-dark/70 rounded-2xl border p-4">
                <div className="text-brand-blue-sky flex items-center gap-1.5 font-bold">
                  <Activity className="h-4 w-4" />
                  <span>Thời gian lái tối đa khuyến nghị</span>
                </div>
                <p className="text-brand-text-primary mt-2 text-2xl font-black">
                  {bioProfile?.thoi_gian_lai_toi_da_khuyen_nghi
                    ? `${(bioProfile.thoi_gian_lai_toi_da_khuyen_nghi / 60).toFixed(1)} Giờ`
                    : "4.0 Giờ"}
                </p>
                <p className="text-brand-text-subtle mt-1 text-[10px]">
                  Thời gian lái trung bình trước khi ngáp:{" "}
                  <b>
                    {bioProfile?.thoi_gian_lai_trung_binh_truoc_met_moi || 180}{" "}
                    phút
                  </b>
                </p>
              </div>
            </div>

            <div className="border-brand-border-active/30 bg-brand-blue-night/20 rounded-2xl border p-4">
              <p className="text-brand-blue-sky text-[11px] font-bold tracking-wider uppercase">
                Đánh giá tổng quan AI &amp; Khuyến nghị phân ca
              </p>
              <p className="text-brand-text-secondary mt-1.5 text-xs leading-relaxed">
                {bioProfile?.danh_gia_chung ||
                  "Thời gian nghỉ tối thiểu 10 giờ giữa hai ca làm việc liên tiếp."}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {scoreLogs.length === 0 ? (
              <div className="text-brand-text-subtle py-12 text-center text-xs">
                Chưa có giao dịch biến động điểm nào được ghi nhận.
              </div>
            ) : (
              scoreLogs.map((log) => {
                const isDeduction = log.so_diem_thay_doi < 0;
                return (
                  <div
                    key={log.id}
                    className="border-brand-border-dark bg-brand-dark/70 flex items-center justify-between rounded-2xl border p-3.5"
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={`flex h-8 w-8 items-center justify-center rounded-xl border ${
                          isDeduction
                            ? "border-alert-danger/40 bg-alert-danger/15 text-alert-danger"
                            : "border-alert-safe/40 bg-alert-safe/15 text-alert-safe"
                        }`}
                      >
                        {isDeduction ? (
                          <ArrowDownRight className="h-4 w-4" />
                        ) : (
                          <ArrowUpRight className="h-4 w-4" />
                        )}
                      </div>
                      <div>
                        <p className="text-brand-text-primary text-xs font-bold">
                          {log.ly_do}
                        </p>
                        <p className="text-brand-text-subtle text-[10px]">
                          {formatFullDateTime(log.created_at)} • Chuyến #
                          {log.trip_id || "501"}
                        </p>
                      </div>
                    </div>

                    <div className="text-right">
                      <span
                        className={`text-sm font-black ${
                          isDeduction ? "text-alert-danger" : "text-alert-safe"
                        }`}
                      >
                        {log.so_diem_thay_doi > 0
                          ? `+${log.so_diem_thay_doi}`
                          : log.so_diem_thay_doi}
                      </span>
                      <p className="text-brand-text-subtle text-[10px]">
                        {log.diem_truoc} &rarr; <b>{log.diem_sau}</b>
                      </p>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </BaseModal>
  );
}
