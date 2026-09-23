import prisma from '../../config/database.js';

export const authRepository = {
	async findUserById(id) {
		return await prisma.user.findUnique({
			where: { id },
		});
	},

	async findUserByEmail(email) {
		return await prisma.user.findUnique({
			// Prisma generate model là User (PascalCase)
			where: { email },
		});
	},

	async createUser(data) {
		return await prisma.user.create({
			data: {
				email: data.email,
				passwordHash: data.passwordHash,
				userName: data.userName,
				avatar: data.avatar,
				isVerified: false,
				verifyToken: data.verifyToken,
				tokenExpires: data.tokenExpires,
			},
		});
	},

	async findUserByVerifyToken(token) {
		return await prisma.user.findFirst({
			where: { verifyToken: token },
		});
	},

	async verifyUser(userId) {
		return await prisma.user.update({
			where: { id: userId },
			data: {
				isVerified: true,
				verifyToken: null,
				tokenExpires: null,
			},
		});
	},

	async updateRefreshToken(userId, hashedRefreshToken) {
		return await prisma.user.update({
			where: { id: userId },
			data: { hashedRefreshToken: hashedRefreshToken },
		});
	},

	async findUserByGoogleId(googleId) {
		return await prisma.user.findUnique({
			where: { googleId },
		});
	},

	async updateGoogleId(userId, googleId) {
		return await prisma.user.update({
			where: { id: userId },
			data: { googleId, authProvider: 'google' },
		});
	},

	async createGoogleUser(data) {
		return await prisma.user.create({
			data: {
				email: data.email,
				passwordHash: null,
				userName: data.userName,
				avatar: data.avatar,
				isVerified: true, // Google đã xác thực email
				googleId: data.googleId,
				authProvider: 'google',
			},
		});
	},

	async updatePassword(userId, newPasswordHash) {
		return await prisma.user.update({
			where: { id: userId },
			data: { passwordHash: newPasswordHash },
		});
	},

	async updatePasswordResetToken(userId, token, expires) {
		return await prisma.user.update({
			where: { id: userId },
			data: {
				passwordResetToken: token,
				passwordResetExpires: expires,
			},
		});
	},

	async findUserByPasswordResetToken(token) {
		return await prisma.user.findFirst({
			where: {
				passwordResetToken: token,
				passwordResetExpires: {
					gt: new Date(),
				},
			},
		});
	},

	async resetPassword(userId, passwordHash) {
		return await prisma.user.update({
			where: { id: userId },
			data: {
				passwordHash,
				passwordResetToken: null,
				passwordResetExpires: null,
			},
		});
	},

	async updateVerifyToken(userId, token, expires) {
		return await prisma.user.update({
			where: { id: userId },
			data: {
				verifyToken: token,
				tokenExpires: expires,
			},
		});
	},
};
