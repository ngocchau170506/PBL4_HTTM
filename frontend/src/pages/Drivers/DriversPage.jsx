import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import dataService from "../../services/dataService";
import AddDriverModal from "./components/AddDriverModal";
import DriverProfileModal from "./components/DriverProfileModal";
import DriversFilterBar from "./components/DriversFilterBar";
import DriversKpiCards from "./components/DriversKpiCards";
import DriversTable from "./components/DriversTable";

export default function DriversPage() {
  const [drivers, setDrivers] = useState([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  const [selectedDriverForProfile, setSelectedDriverForProfile] =
    useState(null);
  const [showAddModal, setShowAddModal] = useState(false);

  const fetchDrivers = async () => {
    const data = await dataService.getDrivers();
    setDrivers([...data]);
  };

  useEffect(() => {
    fetchDrivers();
  }, []);

  const handleToggleDriverStatus = async (driver) => {
    const score =
      driver.diem_uy_tin !== undefined
        ? driver.diem_uy_tin
        : driver.uy_tin || 100;
    const isSuspended = score <= 20 || driver.cap_canh_bao === "SUSPENDED_RED";
    const newStatus = isSuspended ? "NORMAL" : "SUSPENDED_RED";
    const driverName = driver.user?.ho_ten || driver.ho_ten;

    try {
      await dataService.updateDriverStatus(
        driver.id,
        newStatus,
        isSuspended ? "Mở khóa nhận ca" : "Đình chỉ công tác theo SRS 4.1",
      );
      await fetchDrivers();

      if (isSuspended) {
        toast.success(`Đã mở khóa nhận ca cho tài xế ${driverName}!`, {
          icon: "🔓",
        });
      } else {
        toast.error(
          `Đã đình chỉ công tác tài xế ${driverName} (Không xếp ca theo SRS 4.1)!`,
          { icon: "🔒" },
        );
      }
    } catch (err) {
      toast.error(err.message || "Không thể cập nhật trạng thái tài xế!");
    }
  };

  const handleAddDriver = async (newDriver) => {
    try {
      await dataService.createDriver(newDriver);
      await fetchDrivers();
    } catch (err) {
      toast.error(err.message || "Không thể tạo hồ sơ tài xế!");
      throw err;
    }
  };

  const filteredDrivers = drivers.filter((d) => {
    const name = (d.user?.ho_ten || d.ho_ten || "").toLowerCase();
    const phone = (d.user?.so_dien_thoai || d.sdt || "").toLowerCase();
    const license = (d.so_bang_lai || "").toLowerCase();
    const query = searchTerm.toLowerCase();
    const matchSearch =
      name.includes(query) || phone.includes(query) || license.includes(query);
    const score = d.diem_uy_tin !== undefined ? d.diem_uy_tin : d.uy_tin || 100;
    if (statusFilter === "ALL") return matchSearch;
    if (statusFilter === "NORMAL") return matchSearch && score > 65;
    if (statusFilter === "WARNING_YELLOW")
      return matchSearch && score <= 65 && score > 20;
    if (statusFilter === "SUSPENDED_RED") return matchSearch && score <= 20;
    return matchSearch;
  });

  return (
    <div className="animate-fade-in-up space-y-6 font-sans">
      <div>
        <h1 className="text-brand-text-primary text-2xl font-black tracking-tight">
          Quản trị Tài xế & Điểm Uy tín
        </h1>
        <p className="text-brand-text-muted text-xs">
          Kiểm soát hồ sơ sinh học 30 ngày, đối soát cảnh báo đình chỉ
        </p>
      </div>

      <DriversKpiCards drivers={drivers} />

      <DriversFilterBar
        searchTerm={searchTerm}
        setSearchTerm={setSearchTerm}
        statusFilter={statusFilter}
        setStatusFilter={setStatusFilter}
        onOpenAddModal={() => setShowAddModal(true)}
      />

      <DriversTable
        drivers={filteredDrivers}
        onOpenProfileModal={(d) => setSelectedDriverForProfile(d)}
        onToggleDriverStatus={handleToggleDriverStatus}
      />

      <DriverProfileModal
        isOpen={Boolean(selectedDriverForProfile)}
        onClose={() => setSelectedDriverForProfile(null)}
        driver={selectedDriverForProfile}
      />
      <AddDriverModal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        onAddDriver={handleAddDriver}
      />
    </div>
  );
}
