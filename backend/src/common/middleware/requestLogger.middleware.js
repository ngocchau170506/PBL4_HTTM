import logger from '../utils/logger.js';

export const requestLoggerMiddleware = (req, res, next) => {
	const path = req.originalUrl || req.url;
	if (path === '/health' || path === '/api/health') {
		return next();
	}

	const start = process.hrtime.bigint();
	res.on('finish', () => {
		const durationMs = Number(process.hrtime.bigint() - start) / 1e6;
		const line = `${req.method} ${path} -> ${res.statusCode} (${durationMs.toFixed(1)}ms)`;
		if (res.statusCode >= 500) {
			logger.error(line);
		} else if (res.statusCode >= 400) {
			logger.warn(line);
		} else {
			logger.info(line);
		}
	});

	next();
};
