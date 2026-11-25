const isTest = () => process.env.NODE_ENV === 'test';
const stamp = () => new Date().toISOString();

export const info = (...params) => {
	if (!isTest()) {
		console.log(`[${stamp()}] [INFO]`, ...params);
	}
};

export const warn = (...params) => {
	if (!isTest()) {
		console.warn(`[${stamp()}] [WARN]`, ...params);
	}
};

export const error = (...params) => {
	if (!isTest()) {
		console.error(`[${stamp()}] [ERROR]`, ...params);
	}
};

export default {
	info,
	warn,
	error,
};
