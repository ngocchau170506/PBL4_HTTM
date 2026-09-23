import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
//import { API_URLS } from '../api/api';
//import axiosClient from '../api/axiosClient';
//import { useAuth } from '../contexts/AuthContext';
import toast from 'react-hot-toast';
import InputField from "../../components/common/InputField";
import Button from "../../components/common/Button";

function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation(); // Dùng để biết đang ở trang nào
  const { login } = useAuth();

  const [credentials, setCredentials] = useState({ email: '', password: '' });
  const [isLoading, setIsLoading] = useState(false);

  const handleChange = (e) => {
    setCredentials({ ...credentials, [e.target.name]: e.target.value });
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setIsLoading(true);
    const toastId = toast.loading("Đang đăng nhập...");
    try {
      // Mock tạm đăng nhập thành công vì chưa có API
      const mockUser = { name: 'Admin', role: 'MANAGER' };
      login(mockUser, 'fake-jwt-token'); 
      toast.success("Đăng nhập thành công!", { id: toastId });
      
      setTimeout(() => {
         navigate('/'); // Đẩy thẳng về Dashboard
      }, 1000);
    } catch (error) {
      toast.error("Lỗi đăng nhập", { id: toastId });
    } finally {
      setIsLoading(false);
    }
};

return (
    <div className="min-h-screen flex flex-col bg-cream font-sans">
      <header className="h-20 bg-white shadow-sm flex items-center justify-between px-8 shrink-0">
        <h1 className="text-2xl font-bold text-olive cursor-pointer" onClick={() => navigate('/')}>
            Driver Safety System
        </h1>
      </header>
      
      <div className="flex-1 flex items-center justify-center px-4 py-8">
        <div className="bg-white p-10 rounded-3xl shadow-xl w-full max-w-md border-t-8 border-olive">
          <div className="text-center mb-8">
            <h2 className="text-3xl font-bold text-gray-800 mb-2">Chào mừng!</h2>
            <p className="text-gray-500">Đăng nhập hệ thống giám sát an toàn</p>
          </div>
          
          <form onSubmit={handleLogin} className="space-y-6">
            <InputField label="Tài khoản" type="text" name="username" placeholder="Nhập tên đăng nhập..." required disabled={isLoading} />
            <InputField label="Mật khẩu" type="password" name="password" placeholder="Nhập mật khẩu..." required disabled={isLoading} />
            <Button type="submit" isLoading={isLoading}>Đăng nhập</Button>
          </form>
        </div>
      </div>
    </div>
  );
}
export default LoginPage;