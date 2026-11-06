import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileVideo,
  Scale,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import toast from "react-hot-toast";
import Button from "../../../components/common/Button";
import StatusBadge from "../../../components/common/StatusBadge";
import { formatFullDateTime } from "../../../utils/formatDateTime";

export default function DisputesSection({
  disputes = [],
  currentUser,
  onResolveDispute,
}) {
  const [selectedDispute, setSelectedDispute] = useState(null);
  const [responseNote, setResponseNote] = useState("");

  const handleDecision = async (decision) => {
    if (!responseNote.trim()) {
      toast.error("Bắt buộc nhập nội dung phản hồi cho tài xế!");
      return;
    }
    await onResolveDispute({
      disputeId: selectedDispute.id,
      decision,
      responseNote: responseNote.trim(),
    });
    setResponseNote("");
    setSelectedDispute(null);
  };

  const violation = selectedDispute?.violation || {};
  const videoUrl = violation.video_url || violation.duong_dan_video;
  const penaltyPoints = violation.diem_bi_tru ?? 5;
  const isSameReviewer =
    violation.reviewed_by &&
    Number(violation.reviewed_by) === Number(currentUser?.id) &&
    currentUser?.role?.toUpperCase() !== "ADMIN";

  return (
    <>
      <div className="card-glass border-brand-border rounded-3xl border p-6 shadow-xl">
        <div className="border-brand-border-dark mb-4 flex flex-col justify-between gap-2 border-b pb-4 sm:flex-row sm:items-center">
          <div className="flex items-center gap-2.5">
            <div className="border-brand-primary/40 bg-brand-blue-night text-brand-blue-sky flex h-9 w-9 items-center justify-center rounded-xl border">
              <Scale className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-brand-text-primary text-sm font-bold">
                Danh sách Đơn Khiếu nại từ Tài xế
              </h3>
              <p className="text-brand-text-muted text-[11px]">
                Áp dụng nguyên tắc Phân quyền chéo: Người duyệt phạt ban đầu
                không được tự xử lý khiếu nại
              </p>
            </div>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-500/30 bg-emerald-950/30 px-3 py-1.5 text-xs font-bold text-emerald-400">
            <ShieldCheck className="h-4 w-4" /> Four-Eyes Check
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-brand-border-dark bg-brand-dark/70 text-brand-text-muted border-b text-xs font-bold tracking-wider uppercase">
                <th className="rounded-tl-2xl px-4 py-4">Mã Đơn</th>
                <th className="px-4 py-4">Tài xế khiếu nại</th>
                <th className="px-4 py-4">Vi phạm liên quan</th>
                <th className="px-4 py-4">Lý do & Nội dung giải trình</th>
                <th className="px-4 py-4 text-center">Điểm tranh chấp</th>
                <th className="px-4 py-4">Trạng thái</th>
                <th className="rounded-tr-2xl px-4 py-4 text-right">
                  Thao tác
                </th>
              </tr>
            </thead>
            <tbody className="divide-brand-border-dark/60 divide-y">
              {disputes.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className="text-brand-text-subtle py-12 text-center text-xs"
                  >
                    Hiện không có đơn khiếu nại nào
                  </td>
                </tr>
              ) : (
                disputes.map((d) => {
                  const driverName =
                    d.driver?.user?.ho_ten || d.driver?.ho_ten || "Tài xế";
                  const vCode = d.violation?.ma_loai || "VIOLATION";
                  const pts = d.violation?.diem_bi_tru ?? 5;
                  return (
                    <tr
                      key={d.id}
                      className="hover:bg-brand-surface/70 transition-colors"
                    >
                      <td className="text-brand-blue-sky px-4 py-4 text-xs font-bold">
                        #KN-{d.id}
                        <div className="text-brand-text-subtle text-[10px]">
                          {formatFullDateTime(d.khieu_nai_luc)}
                        </div>
                      </td>
                      <td className="px-4 py-4">
                        <p className="text-brand-text-primary text-xs font-bold">
                          {driverName}
                        </p>
                        <p className="text-brand-text-subtle text-[11px]">
                          Điểm hiện tại:{" "}
                          <b className="text-brand-blue-sky">
                            {d.driver?.diem_uy_tin ?? 58}/100
                          </b>
                        </p>
                      </td>
                      <td className="px-4 py-4 text-xs">
                        <span className="text-brand-text-primary font-bold">
                          #{d.violation_id} ({vCode})
                        </span>
                        {/* <p className="text-brand-text-subtle text-[11px]">
                          Nguồn phạt:{" "}
                          {d.violation?.nguon_quyet_dinh || "AI / SYSTEM"}
                        </p> */}
                      </td>
                      <td className="max-w-xs px-4 py-4 text-xs">
                        <span className="rounded border border-amber-500/30 bg-amber-950/40 px-1.5 py-0.5 text-[10px] font-bold text-amber-300">
                          {d.ten_ly_do || d.ma_ly_do}
                        </span>
                      </td>
                      <td className="px-4 py-4 text-center">
                        <span className="text-alert-warning font-mono text-xs font-black">
                          +{pts} điểm
                        </span>
                      </td>
                      <td className="px-4 py-4 text-center">
                        <div className="flex flex-col items-center gap-1">
                          <StatusBadge code={d.trang_thai} />
                        </div>
                      </td>
                      <td className="px-4 py-4 text-right">
                        <button
                          onClick={() => {
                            setSelectedDispute(d);
                            setResponseNote(d.phan_hoi || "");
                          }}
                          className="border-brand-border-active/40 bg-brand-surface/90 text-brand-blue-sky hover:border-brand-primary hover:bg-brand-primary inline-flex cursor-pointer items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-bold transition-all hover:text-white"
                        >
                          <Scale className="h-3.5 w-3.5" />
                          <span>
                            {d.trang_thai === "OPEN"
                              ? "Xử lý & Hoàn điểm"
                              : "Xem chi tiết"}
                          </span>
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* MODAL ĐỐI SOÁT & GIẢI QUYẾT KHIẾU NẠI */}
      {selectedDispute && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-md">
          <div className="card-glass border-brand-border animate-fade-in-up w-full max-w-2xl overflow-hidden rounded-3xl border shadow-2xl">
            <div className="border-brand-border-dark bg-brand-dark/80 flex items-center justify-between border-b px-6 py-4">
              <div>
                <h3 className="text-brand-text-primary text-base font-bold">
                  Giải quyết Khiếu nại #KN-{selectedDispute.id} (Vi phạm #
                  {selectedDispute.violation_id})
                </h3>
                <p className="text-brand-text-muted text-xs">
                  Tài xế:{" "}
                  {selectedDispute.driver?.user?.ho_ten || "Phạm Hoàng Nam"} •
                  Yêu cầu hoàn: <b>+{penaltyPoints} điểm uy tín</b>
                </p>
                <p className="text-brand-text-muted text-xs">
                  Nguồn phạt:{" "}
                  {selectedDispute.violation?.nguon_quyet_dinh || "AI / SYSTEM"}{" "}
                  • Loại vi phạm:{" "}
                  {selectedDispute.violation?.ma_loai || "VIOLATION"}
                </p>
              </div>
              <button
                onClick={() => setSelectedDispute(null)}
                className="text-brand-text-muted cursor-pointer rounded-full p-1 hover:text-white"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="max-h-[72vh] space-y-4 overflow-y-auto p-6 text-xs">
              {videoUrl ? (
                <div className="border-brand-border-dark relative overflow-hidden rounded-2xl border bg-black">
                  <video
                    src={videoUrl}
                    controls
                    autoPlay
                    muted
                    className="max-h-60 w-full object-contain"
                  />
                  <div className="border-brand-border/60 bg-brand-dark/90 absolute top-3 left-3 flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[10px] font-bold text-white">
                    <FileVideo className="text-brand-blue-sky h-3.5 w-3.5" />
                    <span>Bằng chứng Video gốc (10s)</span>
                  </div>
                </div>
              ) : (
                <div className="border-brand-border-dark bg-brand-dark/70 rounded-2xl border p-4 text-center">
                  <Clock className="text-brand-blue-sky mx-auto h-7 w-7" />
                  <p className="text-brand-text-primary mt-1.5 font-bold">
                    Vi phạm cảm biến / Quy tắc hệ thống (Không có video)
                  </p>
                  <p className="text-brand-text-muted mt-0.5 text-[11px]">
                    {violation.mo_ta}
                  </p>
                </div>
              )}

              <div className="rounded-2xl border border-amber-500/30 bg-amber-950/20 p-4">
                <p className="text-[11px] font-bold tracking-wider text-amber-400 uppercase">
                  Giải trình từ Tài xế ({selectedDispute.ten_ly_do}):
                </p>
                <p className="text-brand-text-primary mt-1.5 text-xs leading-relaxed italic">
                  &ldquo;{selectedDispute.noi_dung_giai_trinh}&rdquo;
                </p>
              </div>

              {isSameReviewer && (
                <div className="flex items-start gap-2.5 rounded-2xl border border-rose-500/40 bg-rose-950/30 p-3.5 text-rose-300">
                  <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400" />
                  <div>
                    <p className="font-bold">
                      Cảnh báo Phân quyền chéo (Four-Eyes Principle)
                    </p>
                    <p className="mt-0.5 text-[11px]">
                      Bạn là người đã duyệt phạt vi phạm này ban đầu nên hệ
                      thống khóa quyền tự giải quyết khiếu nại. Vui lòng chuyển
                      cho Admin hoặc Điều phối viên khác!
                    </p>
                  </div>
                </div>
              )}

              <div>
                <label className="text-brand-text-primary mb-1.5 block font-bold">
                  Phản hồi chính thức của Ban Điều phối
                  <span className="inline-block text-red-500"> *</span>
                </label>
                <textarea
                  rows={2}
                  disabled={selectedDispute.trang_thai !== "OPEN"}
                  value={responseNote}
                  onChange={(e) => setResponseNote(e.target.value)}
                  placeholder="VD: Đã kiểm tra lại góc quay camera, xác nhận tài xế chỉnh tai nghe bộ đàm. Đồng ý hoàn +5 điểm..."
                  className="border-brand-border bg-brand-dark text-brand-text-primary focus:border-brand-primary w-full rounded-xl border p-3 text-xs outline-none disabled:opacity-60"
                />
              </div>
            </div>

            <div className="border-brand-border-dark bg-brand-dark/90 flex items-center justify-between border-t px-6 py-4">
              <Button
                variant="secondary"
                onClick={() => setSelectedDispute(null)}
              >
                Đóng
              </Button>
              {selectedDispute.trang_thai === "OPEN" ? (
                <div className="flex gap-2.5">
                  <Button
                    variant="danger"
                    disabled={isSameReviewer}
                    onClick={() => handleDecision("REJECTED")}
                  >
                    <XCircle className="h-4 w-4" />
                    <span>Bác bỏ khiếu nại</span>
                  </Button>
                  <Button
                    variant="primary"
                    disabled={isSameReviewer}
                    onClick={() => handleDecision("APPROVED")}
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    <span>Chấp thuận & Hoàn +{penaltyPoints}đ</span>
                  </Button>
                </div>
              ) : (
                <span className="text-brand-text-subtle text-xs italic">
                  Đơn khiếu nại đã đóng
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
