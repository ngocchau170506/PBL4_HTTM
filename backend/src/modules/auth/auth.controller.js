import * as authService from './auth.service.js';

export const handleRegister = async (req, res, next) => {
  try {
    const { username, password, hoTen, sdt, role, bangLai, vaiTro } = req.body;
    if (!username || !password || !hoTen) {
      return res.status(400).json({ message: 'Vui lòng cung cấp username, password và hoTen.' });
    }

    const newUser = await authService.registerUser({ username, password, hoTen, sdt, role, bangLai, vaiTro });
    return res.status(201).json({
      message: 'Đăng ký tài khoản thành công.',
      user: newUser,
    });
  } catch (error) {
    return res.status(400).json({ message: error.message });
  }
};

export const handleLogin = async (req, res, next) => {
  try {
    const { username, password } = req.body;
    if (!username || !password) {
      return res.status(400).json({ message: 'Vui lòng nhập tên đăng nhập và mật khẩu.' });
    }

    const { user, token } = await authService.loginUser({ username, password });

    res.cookie('accessToken', token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      maxAge: 24 * 60 * 60 * 1000,
    });

    return res.json({
      message: 'Đăng nhập thành công.',
      token,
      user,
    });
  } catch (error) {
    return res.status(401).json({ message: error.message });
  }
};

export const handleGetMe = async (req, res, next) => {
  try {
    const user = await authService.getUserProfile(req.user.id);
    return res.json({ user });
  } catch (error) {
    return res.status(400).json({ message: error.message });
  }
};
