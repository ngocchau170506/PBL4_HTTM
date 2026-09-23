import { z } from 'zod';

export const registerSchema = {
	body: z.object({
		email: z.string().email('Email không hợp lệ'),
		password: z.string().min(6, 'Mật khẩu phải có ít nhất 6 ký tự'),
		userName: z.string().min(2, 'Tên phải có ít nhất 2 ký tự'),
	}),
};

export const loginSchema = {
	body: z.object({
		email: z.string().email(),
		password: z.string().min(6),
	}),
};

export const verifyEmailSchema = {
	body: z.object({
		token: z.string().min(1, 'Token không được để trống'),
	}),
};

export const forgotPasswordSchema = {
	body: z.object({
		email: z.string().email('Email không hợp lệ'),
	}),
};

export const resetPasswordSchema = {
	body: z.object({
		token: z.string().min(1, 'Token không được để trống'),
		password: z.string().min(6, 'Mật khẩu phải có ít nhất 6 ký tự'),
	}),
};

export const resendVerificationSchema = {
	body: z.object({
		email: z.string().email('Email không hợp lệ').optional(),
		token: z.string().optional(),
	}).refine(data => data.email || data.token, {
		message: 'Vui lòng cung cấp email hoặc token xác thực.',
		path: ['email'],
	}),
};
