import { redisClient, redisStatus } from '../../config/redis.js';
import logger from '../utils/logger.js';

const ENV = process.env.NODE_ENV || 'development';
const CACHE_PREFIX = `cache:${ENV}:`;
const CACHE_TIMEOUT_MS = 500; // 500ms timeout for Redis operations

// --- Cache Metrics ---
export const cacheMetrics = {
  hits: 0,
  misses: 0,
  errors: 0,
  getHitRate() {
    const total = this.hits + this.misses;
    return total > 0 ? (this.hits / total * 100).toFixed(2) + '%' : '0%';
  }
};

/**
 * Internal get function
 */
const _get = async (key) => {
  const value = await redisClient.get(`${CACHE_PREFIX}${key}`);
  return value ? JSON.parse(value) : null;
}

/**
 * Get a value from cache with timeout and fallback.
 * @param {string} key
 * @returns {Promise<any>}
 */
export const get = async (key) => {
  if (!redisStatus.isReady) {
    return null;
  }
  try {
    const value = await Promise.race([
      _get(key),
      new Promise((_, reject) => 
        setTimeout(() => reject(new Error('Cache timeout')), CACHE_TIMEOUT_MS)
      ),
    ]);
    
    if (value) {
      cacheMetrics.hits++;
    } else {
      cacheMetrics.misses++;
    }
    return value;
  } catch (error) {
    cacheMetrics.errors++;
    logger.warn(`Cache fetch failed for key: ${key}, falling back to DB. Reason: ${error.message}`);
    return null; // Fallback to DB
  }
};

/**
 * Set a value in cache with a TTL
 * @param {string} key
 * @param {any} value
 * @param {number} ttlSeconds - Time to live in seconds
 * @returns {Promise<void>}
 */
export const set = async (key, value, ttlSeconds) => {
  if (!redisStatus.isReady) {
    return;
  }
  try {
    const prefixedKey = `${CACHE_PREFIX}${key}`;
    const stringValue = JSON.stringify(value);
    await redisClient.set(prefixedKey, stringValue, 'EX', ttlSeconds);
  } catch (error) {
    logger.error(`Error setting cache for key: ${key}`, error);
  }
};

/**
 * Delete a value from cache
 * @param {string} key
 * @returns {Promise<void>}
 */
export const del = async (key) => {
  if (!redisStatus.isReady) {
    return;
  }
  try {
    await redisClient.del(`${CACHE_PREFIX}${key}`);
  } catch (error) {
    logger.error(`Error deleting cache for key: ${key}`, error);
  }
};

/**
 * Invalidate cache by a pattern. This is now safe from race conditions.
 * @param {string} pattern
 * @returns {Promise<void>}
 */
export const invalidate = (pattern) => {
  if (!redisStatus.isReady) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    try {
      const stream = redisClient.scanStream({
        match: `${CACHE_PREFIX}${pattern}`,
        count: 100,
      });

      const keys = [];
      stream.on('data', (resultKeys) => {
        keys.push(...resultKeys);
      });

      stream.on('end', async () => {
        try {
          if (keys.length > 0) {
            const pipeline = redisClient.pipeline();
            keys.forEach(key => pipeline.del(key));
            await pipeline.exec();
            logger.info(`Invalidated ${keys.length} keys for pattern: ${pattern}`);
            cacheMetrics.invalidations = (cacheMetrics.invalidations || 0) + keys.length;
          }
          resolve();
        } catch (pipelineError) {
          reject(pipelineError);
        }
      });

      stream.on('error', reject);
    } catch (error) {
      reject(error);
    }
  });
};
