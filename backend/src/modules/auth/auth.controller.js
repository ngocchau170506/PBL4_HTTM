import * as authService from './auth.service.js';

export const handleRegister = async (req, res, next) => {
  try {
    const user = await authService.registerUser(req.body);
    res.status(201).json({ success: true, data: user });
  } catch (error) {
    next(error);
  }
};

export const handleLogin = async (req, res, next) => {
  try {
    const { username, password } = req.body;
    const result = await authService.loginUser({ username, password });

    res.cookie('accessToken', result.token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'strict',
      maxAge: 7 * 24 * 60 * 60 * 1000,
    });

    res.json({ success: true, data: result, ...result });
  } catch (error) {
    next(error);
  }
};

export const handleGetMe = async (req, res, next) => {
  try {
    const user = await authService.getUserProfile(req.user.id);
    res.json({ success: true, data: user, user });
  } catch (error) {
    next(error);
  }
};

export const handleGetUsers = async (req, res, next) => {
  try {
    const users = await authService.getAllUsers();
    res.json({ success: true, data: users, users });
  } catch (error) {
    next(error);
  }
};

export const handleUpdateUser = async (req, res, next) => {
  try {
    const { userId } = req.params;
    const updated = await authService.updateUser(userId, req.body, req.user.id);
    res.json({ success: true, data: updated });
  } catch (error) {
    next(error);
  }
};

export const handleGetConfig = async (req, res, next) => {
  try {
    const config = await authService.getSystemConfig();
    res.json({ success: true, data: config, config });
  } catch (error) {
    next(error);
  }
};

export const handleUpdateConfig = async (req, res, next) => {
  try {
    const updated = await authService.updateSystemConfig(req.body, req.user.id);
    res.json({ success: true, data: updated, config: updated });
  } catch (error) {
    next(error);
  }
};
