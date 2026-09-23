import Redis from 'ioredis';
import logger from '../common/utils/logger.js';

const connection = {
  host: process.env.REDIS_HOST || 'localhost',
  port: process.env.REDIS_PORT || 6379,
  password: process.env.REDIS_PASSWORD || undefined,
  maxRetriesPerRequest: 3,
  retryStrategy(times) {
    return Math.min(times * 50, 2000);
  },
  enableOfflineQueue: false, 
};

const redisClient = new Redis(connection);

const redisStatus = {
  isReady: false,
};

// --- Connection Event Handling ---
let hasLoggedError = false;

redisClient.on('ready', () => {
  if (!redisStatus.isReady) {
    logger.info('✅ Connected to Redis successfully!');
    redisStatus.isReady = true;
    hasLoggedError = false; // Reset on successful connection
  }
});

redisClient.on('error', (err) => {
  // Only log the first error to avoid spamming the console
  if (!hasLoggedError) {
    logger.error(`❌ Could not connect to Redis: ${err.message}. Caching is disabled.`);
    hasLoggedError = true;
  }
  redisStatus.isReady = false;
});

redisClient.on('close', () => {
  if (redisStatus.isReady) { // Only log if it was previously connected
    logger.warn('Redis connection closed. Caching is disabled.');
  }
  redisStatus.isReady = false;
});

redisClient.on('connect', () => {
    // This event fires before 'ready'. We can log it for debugging if needed.
    // logger.info('Redis connection initiated.');
});


export { redisClient, redisStatus };
