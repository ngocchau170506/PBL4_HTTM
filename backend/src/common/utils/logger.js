// Simple logger utility for now
// In a real application, you would use a more robust logger like Winston or Pino

export const info = (...params) => {
    if (process.env.NODE_ENV !== 'test') {
        console.log('[INFO]', ...params);
    }
};

export const error = (...params) => {
    if (process.env.NODE_ENV !== 'test') {
        console.error('[ERROR]', ...params);
    }
};

export const warn = (...params) => {
    if (process.env.NODE_ENV !== 'test') {
        console.warn('[WARN]', ...params);
    }
};

export default {
    info,
    error,
    warn,
};
