import React, { createContext, useState, useContext, useEffect } from 'react';

// Tạo Context để lưu trữ thông tin đăng nhập
const AuthContext = createContext();

// Tạo Provider để bao bọc các Component khác
export const AuthProvider = ({ children }) => {
    // Lấy thông tin user từ localStorage nếu có
    const [user, setUser] = useState(() => {
        const savedUser = localStorage.getItem('user');
        return savedUser ? JSON.parse(savedUser) : null;
    });

    // Hàm Đăng nhập: Lưu vào localStorage và lưu vào trình duyệt
    const login = (userData, token) => {
        localStorage.setItem('user', JSON.stringify(userData));
        localStorage.setItem('token', token);
        setUser(userData); // Cập nhật lên AuthContext để các Component khác biết đã đăng nhập rồi, và có thông tin user để hiển thị tên, avatar...
    };

    // Hàm Đăng xuất: Xóa thông tin đăng nhập khỏi localStorage và trình duyệt
    const logout = () => {
        localStorage.removeItem('user');
        localStorage.removeItem('token');
        setUser(null); // Xóa trên AuthContext
    };

    // Truyền dữ liệu xuống cho các Component con nghe
    const value = { user, login, logout };

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    );
};

// Thay vì viết useContext(AuthContext) dài dòng, chỉ cần gọi useAuth()
export const useAuth = () => {
    return useContext(AuthContext);
};