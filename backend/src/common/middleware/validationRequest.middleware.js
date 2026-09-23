import { StatusCodes } from 'http-status-codes';
import { ZodError } from 'zod';

import { OptionalException } from '../exceptions/index.js';

export const validateRequestMiddleware = (schema) => {
	return (req, _res, next) => {
		try {
			for (const key in schema) {
				const zodSchema = schema[key];

				if (!zodSchema) continue;

				const value = req[key];
				//Gán lại giá trị đã clean vào req
				if (Array.isArray(zodSchema)) {
					zodSchema.forEach((s) => s.parse(value));
				} else {
					const parsedValue = zodSchema.parse(value);
					Object.defineProperty(req, key, {
						value: parsedValue,
						writable: true,
						enumerable: true,
						configurable: true
					});
				}
			}

			next();
		} catch (err) {
			if (err instanceof ZodError) {
				const errors = {};
				err.issues.forEach((e) => {
					const field = e.path.join('.');
					if (!errors[field]) {
						errors[field] = e.message;
					}
				});
				console.warn('[Zod Validation Error]', errors, req.body);
				// Ném lỗi ra để errorHandler bắt
				return next(
					new OptionalException(
						StatusCodes.UNPROCESSABLE_ENTITY,
						'Validation failed',
						errors,
					),
				);
			}

			return next(err);
		}
	};
};
