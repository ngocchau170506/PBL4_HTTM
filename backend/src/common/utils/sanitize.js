/**
 * Loại bỏ passwordHash và các trường nhạy cảm khỏi object User trước khi trả về API/Socket.
 */
export const sanitizeUser = (user) => {
	if (!user || typeof user !== 'object') return null;
	const { passwordHash, ...safeUser } = user;
	return safeUser;
};

export const sanitizeDriverRelation = (driver) => {
	if (!driver || typeof driver !== 'object') return null;
	return {
		...driver,
		user: driver.user ? sanitizeUser(driver.user) : null,
	};
};
