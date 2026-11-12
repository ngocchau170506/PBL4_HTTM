import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import dataService from "../../services/dataService";
import { useFleetStore } from "../../store/useFleetStore";
import AddVehicleModal from "./components/AddVehicleModal";
import DeviceHealthModal from "./components/DeviceHealthModal";
import VehiclesFilterBar from "./components/VehiclesFilterBar";
import VehiclesKpiCards from "./components/VehiclesKpiCards";
import VehiclesTable from "./components/VehiclesTable";

export default function VehiclesPage() {
  // Lắng nghe thêm storeVehicles từ Zustand để tự động re-render khi Socket.io báo có thay đổi từ Mobile App!
  const storeVehicles = useFleetStore((state) => state.vehicles);
  const syncFleetData = useFleetStore((state) => state.syncFleetData);

  const [vehicles, setVehicles] = useState([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [selectedVehicleForModal, setSelectedVehicleForModal] = useState(null);
  const [showAddModal, setShowAddModal] = useState(false);

  const loadVehicles = async () => {
    try {
      const data = await dataService.getVehicles();
      setVehicles([...(data || [])]);
    } catch {
      toast.error("Không thể tải danh sách phương tiện!");
    }
  };

  useEffect(() => {
    loadVehicles();
  }, [storeVehicles]);

  const handleToggleMaintenance = async (vehicle) => {
    const isMaintenance =
      vehicle.trang_thai === "MAINTENANCE" ||
      vehicle.trang_thai === "BAO_DUONG";
    // Đồng bộ mã trạng thái AVAILABLE / ACTIVE chuẩn với bộ lọc
    const newStatus = isMaintenance ? "AVAILABLE" : "MAINTENANCE";
    const plate = vehicle.bien_so_xe || vehicle.bien_so;
    await dataService.updateVehicleStatus(vehicle.id, newStatus);
    await loadVehicles();
    await syncFleetData();
    if (newStatus === "MAINTENANCE") {
      toast.success(
        `Đã chuyển xe [${plate}] sang BẢO DƯỠNG (Tự động khóa nhận ca theo SRS 4.1)!`,
        { icon: "🔧" },
      );
    } else {
      toast.success(
        `Xe [${plate}] đã hoàn thành bảo dưỡng và sẵn sàng nhận ca!`,
        { icon: "✅" },
      );
    }
  };

  const handleAddVehicle = async (newVehicle) => {
    await dataService.createVehicle(newVehicle);
    await loadVehicles();
    await syncFleetData();
  };

  const filteredVehicles = vehicles.filter((v) => {
    const plate = (v.bien_so_xe || v.bien_so || "").toLowerCase();
    const model = (v.loai_xe || "").toLowerCase();
    const matchSearch =
      plate.includes(searchTerm.toLowerCase()) ||
      model.includes(searchTerm.toLowerCase());
    if (statusFilter === "ALL") return matchSearch;
    if (statusFilter === "AVAILABLE") {
      return (
        matchSearch &&
        (v.trang_thai === "AVAILABLE" || v.trang_thai === "ACTIVE")
      );
    }
    return matchSearch && v.trang_thai === statusFilter;
  });

  return (
    <div className="animate-fade-in-up space-y-6 font-sans">
      <div>
        <h1 className="text-brand-text-primary text-2xl font-black tracking-tight">
          Quản trị Đội xe &amp; Thiết bị IoT Edge
        </h1>
        <p className="text-brand-text-muted text-xs">
          Kiểm soát tình trạng phần cứng Raspberry Pi 4, cảm biến MQ-135 và phân
          ca an toàn
        </p>
      </div>

      <VehiclesKpiCards vehicles={vehicles} />

      <VehiclesFilterBar
        searchTerm={searchTerm}
        setSearchTerm={setSearchTerm}
        statusFilter={statusFilter}
        setStatusFilter={setStatusFilter}
        onOpenAddModal={() => setShowAddModal(true)}
      />

      <VehiclesTable
        vehicles={filteredVehicles}
        onOpenDeviceModal={(v) => setSelectedVehicleForModal(v)}
        onToggleMaintenance={handleToggleMaintenance}
      />

      <DeviceHealthModal
        isOpen={Boolean(selectedVehicleForModal)}
        onClose={() => setSelectedVehicleForModal(null)}
        vehicle={selectedVehicleForModal}
      />

      <AddVehicleModal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        onAddVehicle={handleAddVehicle}
      />
    </div>
  );
}
