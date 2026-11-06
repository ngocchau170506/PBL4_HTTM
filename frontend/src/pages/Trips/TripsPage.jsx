import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import dataService from "../../services/dataService";
import CreateShiftModal from "./components/CreateShiftModal";
import RealTripsTable from "./components/RealTripsTable";
import ShiftDetailModal from "./components/ShiftDetailModal";
import ShiftsTable from "./components/ShiftsTable";
import TripsHeader from "./components/TripsHeader";

export default function TripsPage() {
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
    setShifts([...shiftList]);
    setTrips([...tripList]);
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleAddShift = async (newShift) => {
    await dataService.createShift(newShift);
    await loadData();
  };

  const handleUpdateShift = async (shiftId, updatedData) => {
    try {
      await dataService.updateShift(shiftId, updatedData);
      await loadData();
      toast.success(`Đã cập nhật thành công Ca #${shiftId}!`, { icon: "✅" });
    } catch (err) {
      toast.error(err.message || "Không thể cập nhật ca!");
    }
  };

  const handleDeleteShift = async (shiftId) => {
    try {
      await dataService.deleteShift(shiftId);
      await loadData();
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
