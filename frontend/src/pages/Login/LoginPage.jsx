import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import toast from "react-hot-toast";
import InputField from "../../components/common/InputField";
import Button from "../../components/common/Button";
import mockUsers from "../../mock/mockUsers.json";

function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();

  const [credentials, setCredentials] = useState({
    username: "",
    password: "",
  });
  const [isLoading, setIsLoading] = useState(false);

  const handleChange = (e) => {
    setCredentials({ ...credentials, [e.target.name]: e.target.value });
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setIsLoading(true);
    const toastId = toast.loading("Đang kiểm tra thông tin...");
    try {
      await new Promise((resolve) => setTimeout(resolve, 1000));

      const foundUser = mockUsers.find(
        (u) =>
          u.username === credentials.username &&
          u.password === credentials.password,
      );

      if (foundUser) {
        const userData = {
          id: foundUser.id,
          name: foundUser.ho_ten,
          role: foundUser.role,
        };

        login(userData, foundUser.token);
        toast.success(`Chào mừng ${foundUser.ho_ten}!`, { id: toastId });

        navigate("/");
      } else {
        toast.error("Sai tên đăng nhập hoặc mật khẩu!", { id: toastId });
      }
    } catch (error) {
      console.error("Chi tiết lỗi:", error);
      toast.error("Lỗi đăng nhập", { id: toastId });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="bg-brand-light flex min-h-screen flex-col font-sans">
      <header className="flex h-20 shrink-0 items-center justify-between bg-white px-8 shadow-sm">
        <h1
          className="text-brand-blue cursor-pointer text-2xl font-bold"
          onClick={() => navigate("/")}
        >
          IoT Driver Safety
        </h1>
      </header>

      <div className="flex flex-1 items-center justify-center px-4 py-8">
        <div className="border-brand-blue w-full max-w-md rounded-3xl border-t-8 bg-white p-10 shadow-xl">
          <div className="mb-8 text-center">
            <h2 className="mb-2 text-3xl font-bold text-gray-800">
              Chào mừng!
            </h2>
            <p className="text-gray-500">Đăng nhập hệ thống giám sát an toàn</p>
          </div>

          <form onSubmit={handleLogin} className="space-y-6">
            <InputField
              label="Tài khoản"
              type="text"
              name="username"
              value={credentials.username}
              onChange={handleChange}
              placeholder="Nhập tên đăng nhập..."
              required
              disabled={isLoading}
            />
            <InputField
              label="Mật khẩu"
              type="password"
              name="password"
              value={credentials.password}
              onChange={handleChange}
              placeholder="Nhập mật khẩu..."
              required
              disabled={isLoading}
            />
            <Button type="submit" isLoading={isLoading}>
              Đăng nhập
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
export default LoginPage;
