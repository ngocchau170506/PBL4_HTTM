import axios from 'axios';

const axiosClient = axios.create({
    baseURL: '',
    headers: {
        'Content-Type': 'application/json',
    },
});

// KHI GỬI YÊU CẦU ĐI (REQUEST INTERCEPTOR)
// Trước khi gửi bất kỳ request nào xuống Backend, sẽ chặn lại để nhét Token.
axiosClient.interceptors.request.use(
    (config) => {
        const token = localStorage.getItem('token'); // Lấy Token từ localStorage
        if (token) {
            // Dán Token vào Headers Authorization theo chuẩn Bearer
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => {
        return Promise.reject(error);
    }
);

// KHI NHẬN KẾT QUẢ VỀ (RESPONSE INTERCEPTOR)
// Kiểm tra xem response từ Backend trả về có bị lỗi không
axiosClient.interceptors.response.use(
    (response) => {
        // Nếu thành công (mã 200), trả về dữ liệu (data) luôn cho nhẹ, không cần lấy cả gói TO
        return response.data;
    },
    (error) => {
        // Nếu Backend báo lỗi 401 (Hết hạn Token / Chưa đăng nhập)
        if (error.response && error.response.status === 401) {
            console.log('Token hết hạn hoặc không hợp lệ!');
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            window.location.href = '/login'; // Tự động đá ra Login
        }
        return Promise.reject(error);
    }
);

export default axiosClient;