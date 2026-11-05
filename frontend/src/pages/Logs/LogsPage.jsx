import { Scale, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { useAuth } from "../../context/AuthContext";
import dataService from "../../services/dataService";
import { useFleetStore } from "../../store/useFleetStore";
import DisputesSection from "./components/DisputesSection";
import LogsFilterBar from "./components/LogsFilterBar";
import LogsKpiStats from "./components/LogsKpiStats";
import LogsTable from "./components/LogsTable";
import ViolationReviewModal from "./components/ViolationReviewModal";

export default function LogsPage() {
  const { user } = useAuth();
  // Quản lý state vi phạm & khiếu nại qua Zustand theo đúng SRS 4.1 (FR_3.2)
  const { violations, disputes, syncFleetData, globalSearchQuery } =
    useFleetStore();

  const [activeTab, setActiveTab] = useState("VIOLATIONS");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [typeFilter, setTypeFilter] = useState("ALL");
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedViolation, setSelectedViolation] = useState(null);

  useEffect(() => {
    syncFleetData();
  }, [syncFleetData]);

  const handleConfirmReview = async (violationId, decision, notes) => {
    try {
      await dataService.reviewViolation({
        violationId,
        decision,
        notes,
        reviewerName: user?.name || "Trần Điều Phối",
        reviewerId: user?.id || 2,
      });
      await syncFleetData();
      if (decision === "CONFIRMED") {
        toast.success(
          `Xác nhận vi phạm #${violationId} & tự động cập nhật sổ cái điểm.`,
          { icon: "✅" },
        );
      } else {
        toast.success(`Đã đánh dấu báo động giả cho sự kiện #${violationId}.`, {
          icon: "🛡️",
        });
      }
    } catch (err) {
      toast.error(err.message || "Có lỗi xảy ra khi duyệt.");
    }
  };

  const handleResolveDispute = async ({
    disputeId,
    decision,
    responseNote,
  }) => {
    try {
      await dataService.resolveDispute({
        disputeId,
        decision,
        responseNote,
        resolverId: user?.id || 1,
        resolverName: user?.name || "Nguyễn Quản Trị",
      });
      await syncFleetData();
      if (decision === "APPROVED") {
        toast.success(
          `Đã chấp thuận khiếu nại #KN-${disputeId} & hoàn điểm tự động vào Sổ cái!`,
          { icon: "🎉", duration: 4000 },
        );
      } else {
        toast.success(
          `Đã bác bỏ khiếu nại #KN-${disputeId} & giữ nguyên mức phạt.`,
          { icon: "🔒" },
        );
      }
    } catch (err) {
      toast.error(err.message || "Không thể xử lý khiếu nại!");
    }
  };

  const openDisputesCount = disputes.filter(
    (d) => d.trang_thai === "OPEN",
  ).length;

  const effectiveQuery = (searchTerm || globalSearchQuery || "").toLowerCase();

  const filteredViolations = violations.filter((v) => {
    const currentStatus = v.trang_thai_xac_thuc || v.trang_thai;
    const currentType = v.ma_loai || v.loai_vi_pham || "";
    const driverName = (
      v.driver?.user?.ho_ten ||
      v.driver_name ||
      ""
    ).toLowerCase();
    const plate = (
      v.bien_so_xe ||
      v.vehicle?.bien_so_xe ||
      v.vehicle_plate ||
      v.trip?.vehicle?.bien_so_xe ||
      ""
    ).toLowerCase();

    if (statusFilter !== "ALL") {
      if (
        statusFilter === "CONFIRMED" &&
        currentStatus !== "CONFIRMED" &&
        currentStatus !== "MANUALLY_CONFIRMED"
      )
        return false;
      if (
        statusFilter === "REJECTED" &&
        currentStatus !== "REJECTED" &&
        currentStatus !== "MANUALLY_REJECTED" &&
        currentStatus !== "RESOLVED_SAFE"
      )
        return false;
      if (
        statusFilter === "PENDING_MANUAL_REVIEW" &&
        currentStatus !== "PENDING_MANUAL_REVIEW"
      )
        return false;
    }

    if (typeFilter !== "ALL" && !currentType.includes(typeFilter)) {
      return false;
    }

    if (
      effectiveQuery &&
      !driverName.includes(effectiveQuery) &&
      !plate.includes(effectiveQuery) &&
      !String(v.id).includes(effectiveQuery)
    ) {
      return false;
    }
    return true;
  });

  return (
    <div className="animate-fade-in-up space-y-6 font-sans">
      <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
        <div>
          <h1 className="text-brand-text-primary text-2xl font-black tracking-tight">
            Xác thực Vi phạm AI & Giải quyết Khiếu nại
          </h1>
          <p className="text-brand-text-muted text-xs">
            Quản lý State thời gian thực qua Zustand Store & Socket.io (SRS 4.1
            • FR_3.2)
          </p>
        </div>

        <div className="card-glass border-brand-border flex items-center gap-1 self-start rounded-2xl border p-1 shadow-md md:self-auto">
          <button
            onClick={() => setActiveTab("VIOLATIONS")}
            className={`flex cursor-pointer items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold transition-all ${
              activeTab === "VIOLATIONS"
                ? "bg-brand-primary text-white shadow-md shadow-blue-500/25"
                : "text-brand-text-muted hover:text-white"
            }`}
          >
            <ShieldAlert className="h-4 w-4" />
            <span>Thẩm định Vi phạm ({violations.length})</span>
          </button>
          <button
            onClick={() => setActiveTab("DISPUTES")}
            className={`relative flex cursor-pointer items-center gap-2 rounded-xl px-4 py-2 text-xs font-bold transition-all ${
              activeTab === "DISPUTES"
                ? "bg-brand-primary text-white shadow-md shadow-blue-500/25"
                : "text-brand-text-muted hover:text-white"
            }`}
          >
            <Scale className="h-4 w-4" />
            <span>Giải quyết Khiếu nại ({disputes.length})</span>
            {openDisputesCount > 0 && (
              <span className="bg-alert-warning text-brand-deep py-0.2 ml-0.5 rounded-full px-1.5 text-[10px] font-black">
                {openDisputesCount} mới
              </span>
            )}
          </button>
        </div>
      </div>

      <LogsKpiStats violations={violations} />

      {activeTab === "VIOLATIONS" ? (
        <>
          <LogsFilterBar
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            typeFilter={typeFilter}
            setTypeFilter={setTypeFilter}
            searchTerm={searchTerm}
            setSearchTerm={setSearchTerm}
          />

          <LogsTable
            violations={filteredViolations}
            onSelectViolation={(v) => setSelectedViolation(v)}
          />
        </>
      ) : (
        <DisputesSection
          disputes={disputes}
          currentUser={user}
          onResolveDispute={handleResolveDispute}
        />
      )}

      <ViolationReviewModal
        isOpen={Boolean(selectedViolation)}
        onClose={() => setSelectedViolation(null)}
        violation={selectedViolation}
        onConfirmReview={handleConfirmReview}
      />
    </div>
  );
}
