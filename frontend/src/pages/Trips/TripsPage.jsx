import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import dataService from "../../services/dataService";
import { useFleetStore } from "../../store/useFleetStore";
import CreateShiftModal from "./components/CreateShiftModal";
import RealTripsTable from "./components/RealTripsTable";
import ShiftDetailModal from "./components/ShiftDetailModal";
import ShiftsTable from "./components/ShiftsTable";
import TripsHeader from "./components/TripsHeader";

export default function TripsPage() {
  // Lắng nghe storeVehicles / violations từ Zustand (mỗi khi Socket.io bắn sự kiện từ Mobile, syncFleetData sẽ chạy và kích hoạt loadData)
  const storeVehicles = useFleetStore((state) => state.vehicles);
  const syncFleetData = useFleetStore((state) => state.syncFleetData);

  const [activeTab, setActiveTab] = useState("SHIFTS");
  const [shifts, setShifts] = useState([]);
  const [trips, setTrips] = useState([]);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedShift, setSelectedShift] = useState(null);

  const loadData = async () => {
    const [shiftList, tripList] = await Promise.all([
      dataService.getShifts(),
      dataService.getTrips(),
    ]);
    setShifts([...(shiftList || [])]);
    setTrips([...(tripList || [])]);
  };

  useEffect(() => {
    loadData();
  }, [storeVehicles]);

  const handleAddShift = async (newShift) => {
    try {
      await dataService.createShift(newShift);
      await loadData();
      await syncFleetData();
      toast.success("Đã tạo ca phân công mới thành công!", { icon: "📅" });
    } catch (err) {
      toast.error(err.message || "Không thể tạo ca phân công!");
    }
  };

  const handleUpdateShift = async (shiftId, updatedData) => {
    try {
      await dataService.updateShift(shiftId, updatedData);
      await loadData();
      await syncFleetData();
      toast.success(`Đã cập nhật thành công Ca #${shiftId}!`, { icon: "✅" });
    } catch (err) {
      toast.error(err.message || "Không thể cập nhật ca!");
    }
  };

  const handleDeleteShift = async (shiftId) => {
    try {
      await dataService.deleteShift(shiftId);
      await loadData();
      await syncFleetData();
      toast.success(`Đã xóa/hủy Ca #${shiftId} khỏi lịch phân công!`, {
        icon: "🗑️",
      });
    } catch (err) {
      toast.error(err.message || "Không thể xóa ca!");
    }
  };

  return (
    <div className="animate-fade-in-up space-y-6 font-sans">
      <TripsHeader
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        shiftCount={shifts.length}
        tripCount={trips.length}
        onOpenCreateModal={() => setShowCreateModal(true)}
      />

      {activeTab === "SHIFTS" ? (
        <ShiftsTable
          shifts={shifts}
          onSelectShift={(shift) => setSelectedShift(shift)}
          onSwitchToTripTab={() => setActiveTab("TRIPS")}
        />
      ) : (
        <RealTripsTable trips={trips} />
      )}

      <CreateShiftModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onAddShift={handleAddShift}
        shifts={shifts}
      />

      <ShiftDetailModal
        isOpen={Boolean(selectedShift)}
        onClose={() => setSelectedShift(null)}
        shift={selectedShift}
        existingShifts={shifts}
        onUpdateShift={handleUpdateShift}
        onDeleteShift={handleDeleteShift}
        onSwitchToTripTab={() => setActiveTab("TRIPS")}
      />
    </div>
  );
}
