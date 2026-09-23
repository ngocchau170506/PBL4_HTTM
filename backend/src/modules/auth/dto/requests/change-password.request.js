import { z } from 'zod';

export const changePasswordSchema = {
	body: z
		.object({
			oldPassword: z.string().min(1, 'Mật khẩu cũ không được để trống'),
			newPassword: z.string().min(6, 'Mật khẩu mới phải có ít nhất 6 ký tự'),
			confirmPassword: z.string().min(1, 'Vui lòng xác nhận mật khẩu mới'),
		})
		.refine((data) => data.newPassword === data.confirmPassword, {
			message: 'Mật khẩu xác nhận không khớp',
			path: ['confirmPassword'], // a field that the error will be attached to
		}),
};
