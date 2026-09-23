import * as cacheService from '../services/cache.service.js';
import { redisStatus } from '../../config/redis.js';
import logger from '../utils/logger.js';

// --- TTL Configuration from Environment ---
const GUEST_ROOMS_TTL = parseInt(process.env.CACHE_TTL_GUEST_ROOMS || '180');
const ROOM_DETAIL_TTL = parseInt(process.env.CACHE_TTL_ROOM_DETAIL || '1800');
const SHOULD_LOG_CACHE = process.env.LOG_CACHE_ACTIVITY === 'true';

const logCache = (level, message) => {
    if (SHOULD_LOG_CACHE) {
        logger[level](message);
    }
};

export const cacheGuestRooms = async (req, res, next) => {
    if (!redisStatus.isReady || req.headers.authorization) {
        return next();
    }

    const page = req.query.page || '1';
    const limit = req.query.limit || '10';
    
    const isDefaultQuery = (page === '1') && 
                           (limit === '10') &&
                           Object.keys(req.query).every(k => ['page', 'limit'].includes(k));

    if (!isDefaultQuery) {
        return next();
    }

    const cacheKey = `rooms:list:page-${page}:limit-${limit}`;

    try {
        const cachedData = await cacheService.get(cacheKey);
        if (cachedData) {
            logCache('info', `Cache HIT for key: ${cacheKey}`);
            return res.status(200).json(cachedData);
        }

        logCache('info', `Cache MISS for key: ${cacheKey}`);
        
        const originalJson = res.json;
        res.json = function (data) {
            if (res.statusCode >= 200 && res.statusCode < 300) {
                cacheService.set(cacheKey, data, GUEST_ROOMS_TTL).catch(err => {
                    logger.error(`Failed to cache response for key: ${cacheKey}`, err);
                });
            }
            return originalJson.apply(res, arguments);
        };

        next();
    } catch (error) {
        logger.error(`Cache middleware error for key: ${cacheKey}`, error);
        next();
    }
};

export const cacheRoomDetail = async (req, res, next) => {
    if (!redisStatus.isReady || req.headers.authorization) {
        return next();
    }

    const { id } = req.params;
    if (!id) {
        return next();
    }

    const cacheKey = `room:detail:${id}`;

    try {
        const cachedData = await cacheService.get(cacheKey);
        if (cachedData) {
            logCache('info', `Cache HIT for key: ${cacheKey}`);
            return res.status(200).json(cachedData);
        }

        logCache('info', `Cache MISS for key: ${cacheKey}`);

        const originalJson = res.json;
        res.json = function (data) {
            if (res.statusCode >= 200 && res.statusCode < 300) {
                cacheService.set(cacheKey, data, ROOM_DETAIL_TTL).catch(err => {
                    logger.error(`Failed to cache response for key: ${cacheKey}`, err);
                });
            }
            return originalJson.apply(res, arguments);
        };
        
        next();
    } catch (error) {
        logger.error(`Cache middleware error for key: ${cacheKey}`, error);
        next();
    }
};
