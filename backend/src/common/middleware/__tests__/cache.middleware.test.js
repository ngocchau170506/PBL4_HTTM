// backend/src/common/middleware/__tests__/cache.middleware.test.js

import request from 'supertest';
import express from 'express';
import * as cacheMiddleware from '../cache.middleware.js';
import { redisClient, redisStatus } from '../../../config/redis.js';
import * as cacheService from '../../services/cache.service.js';

// Mock the redis client and the logger
jest.mock('../../../config/redis.js');
jest.mock('../../utils/logger.js', () => ({
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
}));

// --- Test App Setup ---
const app = express();

const dummyController = (req, res) => {
    res.status(200).json({ id: req.params.id || 'list', timestamp: Date.now() });
};

app.get('/rooms', cacheMiddleware.cacheGuestRooms, dummyController);
app.get('/rooms/:id', cacheMiddleware.cacheRoomDetail, dummyController);

// --- Tests ---
describe('Cache Middleware', () => {

    beforeEach(() => {
        if (redisClient.__clear) redisClient.__clear();
        jest.clearAllMocks();
        // Assume Redis is ready for most tests
        redisStatus.isReady = true;
        cacheService.cacheMetrics.hits = 0;
        cacheService.cacheMetrics.misses = 0;
    });
    
    const ENV = process.env.NODE_ENV || 'development';
    const CACHE_PREFIX = `cache:${ENV}:`;

    describe('when Redis is not ready', () => {
        beforeEach(() => {
            redisStatus.isReady = false;
        });

        it('should bypass cacheGuestRooms and call next', async () => {
            const response = await request(app).get('/rooms');
            expect(response.status).toBe(200);
            expect(cacheService.cacheMetrics.hits).toBe(0);
            expect(cacheService.cacheMetrics.misses).toBe(0);
            // Ensure nothing was cached
            const store = redisClient.__getStore();
            expect(store.size).toBe(0);
        });

        it('should bypass cacheRoomDetail and call next', async () => {
            const response = await request(app).get('/rooms/123');
            expect(response.status).toBe(200);
            expect(cacheService.cacheMetrics.hits).toBe(0);
            expect(cacheService.cacheMetrics.misses).toBe(0);
            const store = redisClient.__getStore();
            expect(store.size).toBe(0);
        });
    });

    describe('cacheGuestRooms', () => {
        const defaultQueryKey = 'rooms:list:page-1:limit-10';

        it('should cache the response on first visit (cache miss)', async () => {
            const response = await request(app).get('/rooms');

            expect(response.status).toBe(200);
            expect(response.body.id).toBe('list');
            expect(cacheService.cacheMetrics.misses).toBe(1);
            expect(cacheService.cacheMetrics.hits).toBe(0);

            // Check if the value was set in cache
            const cachedValue = await redisClient.get(`${CACHE_PREFIX}${defaultQueryKey}`);
            expect(cachedValue).not.toBeNull();
            expect(JSON.parse(cachedValue).id).toBe('list');
        });

        it('should serve from cache on second visit (cache hit)', async () => {
            // First call to populate cache
            const firstResponse = await request(app).get('/rooms');
            const firstTimestamp = firstResponse.body.timestamp;

            // Second call
            const secondResponse = await request(app).get('/rooms');
            
            expect(secondResponse.status).toBe(200);
            expect(secondResponse.body.id).toBe('list');
            // Timestamp should be the same as the cached one
            expect(secondResponse.body.timestamp).toBe(firstTimestamp);
            // This time it's a hit
            expect(cacheService.cacheMetrics.misses).toBe(1); // from first call
            expect(cacheService.cacheMetrics.hits).toBe(1); // from second call
        });

        it('should bypass cache for authenticated users', async () => {
            await request(app).get('/rooms').set('Authorization', 'Bearer token');

            expect(cacheService.cacheMetrics.misses).toBe(0);
            expect(cacheService.cacheMetrics.hits).toBe(0);
            const cachedValue = await redisClient.get(`${CACHE_PREFIX}${defaultQueryKey}`);
            expect(cachedValue).toBeNull();
        });

        it('should bypass cache for non-default queries', async () => {
            await request(app).get('/rooms?page=2');
            
            expect(cacheService.cacheMetrics.misses).toBe(0);
            const cachedValue = await redisClient.get(`${CACHE_PREFIX}${defaultQueryKey}`);
            expect(cachedValue).toBeNull();
        });
    });

    describe('cacheRoomDetail', () => {
        const roomId = '123';
        const roomCacheKey = `room:detail:${roomId}`;

        it('should cache the response on first visit', async () => {
            const response = await request(app).get(`/rooms/${roomId}`);
            
            expect(response.status).toBe(200);
            expect(response.body.id).toBe(roomId);
            expect(cacheService.cacheMetrics.misses).toBe(1);

            const cachedValue = await redisClient.get(`${CACHE_PREFIX}${roomCacheKey}`);
            expect(cachedValue).not.toBeNull();
        });

        it('should serve from cache on second visit', async () => {
            const firstRes = await request(app).get(`/rooms/${roomId}`);
            const secondRes = await request(app).get(`/rooms/${roomId}`);

            expect(secondRes.status).toBe(200);
            expect(secondRes.body.timestamp).toBe(firstRes.body.timestamp);
            expect(cacheService.cacheMetrics.hits).toBe(1);
        });

        it('should bypass cache for authenticated users', async () => {
            await request(app).get(`/rooms/${roomId}`).set('Authorization', 'Bearer token');
            
            expect(cacheService.cacheMetrics.misses).toBe(0);
            const cachedValue = await redisClient.get(`${CACHE_PREFIX}${roomCacheKey}`);
            expect(cachedValue).toBeNull();
        });
    });
});
