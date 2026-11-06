import { Plus, UserPlus } from "lucide-react";
import { useState } from "react";
import toast from "react-hot-toast";
import BaseModal from "../../../components/common/BaseModal";
import Button from "../../../components/common/Button";
import InputField from "../../../components/common/InputField";
import SelectField from "../../../components/common/SelectField";

const LICENSE_OPTIONS = [
  { value: "FC", label: "Hạng FC (Container)" },
  { value: "C", label: "Hạng C (Xe tải nặng)" },
  { value: "D", label: "Hạng D (Xe khách)" },
  { value: "E", label: "Hạng E (Xe giường nằm)" },
];

export default function AddDriverModal({ isOpen, onClose, onAddDriver }) {
  const [formData, setFormData] = useState({
    ho_ten: "",
    so_dien_thoai: "",
    hang_bang_lai: "FC",
    so_bang_lai: "",
  });

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = (e) => {
    if (e) e.preventDefault();
    if (!formData.ho_ten.trim()) {
      toast.error("Vui lòng nhập họ tên tài xế!");
      return;
    }

    const newDriver = {
      id: Date.now(),
      user_id: Date.now(),
      diem_uy_tin: 100,
      cap_canh_bao: "NORMAL",
      hang_bang_lai: formData.hang_bang_lai,
      so_bang_lai:
        formData.so_bang_lai ||
        `FC-${Math.floor(100000 + Math.random() * 900000)}`,
      user: {
        id: Date.now(),
        ho_ten: formData.ho_ten.trim(),
        so_dien_thoai: formData.so_dien_thoai || "0905000999",
      },
    };

    onAddDriver(newDriver);
    toast.success(
      `Thêm thành công tài xế [${newDriver.user.ho_ten}] vào hệ thống!`,
    );

    setFormData({
      ho_ten: "",
      so_dien_thoai: "",
      hang_bang_lai: "FC",
      so_bang_lai: "",
    });
    onClose();
  };

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      icon={UserPlus}
      maxWidth="max-w-md"
      title="Thêm Tài Xế Mới"
      subtitle="Khởi tạo hồ sơ sinh trắc học & sổ cái điểm uy tín (100/100)"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Hủy
          </Button>
          <Button variant="primary" onClick={handleSubmit}>
            <Plus className="h-4 w-4" />
            <span>Tạo hồ sơ</span>
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <InputField
          label="Họ và tên"
          name="ho_ten"
          required
          placeholder="VD: Nguyễn Văn An"
          value={formData.ho_ten}
          onChange={handleChange}
        />
        <div className="grid grid-cols-2 gap-3">
          <InputField
            label="Số điện thoại"
            name="so_dien_thoai"
            placeholder="VD: 0905123456"
            value={formData.so_dien_thoai}
            onChange={handleChange}
          />
          <SelectField
            label="Hạng bằng lái"
            name="hang_bang_lai"
            value={formData.hang_bang_lai}
            onChange={handleChange}
            options={LICENSE_OPTIONS}
          />
        </div>
        <InputField
          label="Số giấy phép lái xe"
          name="so_bang_lai"
          placeholder="VD: FC-790012345"
          value={formData.so_bang_lai}
          onChange={handleChange}
        />
      </form>
    </BaseModal>
  );
}
