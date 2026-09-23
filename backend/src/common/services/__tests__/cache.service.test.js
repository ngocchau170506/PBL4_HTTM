// backend/src/common/services/__tests__/cache.service.test.js

import { redisClient, redisStatus } from '../../../config/redis.js';
import * as cacheService from '../cache.service.js';

// Mock the redis client
jest.mock('../../../config/redis.js');

// Mock the logger to prevent console noise
jest.mock('../../utils/logger.js', () => ({
    warn: jest.fn(),
    error: jest.fn(),
    info: jest.fn(),
}));


describe('Cache Service', () => {

    beforeEach(() => {
        // Clear the mock store and reset mock function stats before each test
        if (redisClient.__clear) redisClient.__clear();
        jest.clearAllMocks();
        // For most tests, we assume Redis is ready.
        redisStatus.isReady = true;

        // Reset metrics
        cacheService.cacheMetrics.hits = 0;
        cacheService.cacheMetrics.misses = 0;
        cacheService.cacheMetrics.errors = 0;
    });

    const ENV = process.env.NODE_ENV || 'development';
    const CACHE_PREFIX = `cache:${ENV}:`;

    describe('when Redis is not ready', () => {
        beforeEach(() => {
            redisStatus.isReady = false;
        });

        it('get() should return null', async () => {
            const result = await cacheService.get('any-key');
            expect(result).toBeNull();
            expect(redisClient.get).not.toHaveBeenCalled();
        });

        it('set() should do nothing', async () => {
            await cacheService.set('any-key', { a: 1 }, 100);
            expect(redisClient.set).not.toHaveBeenCalled();
        });

        it('del() should do nothing', async () => {
            await cacheService.del('any-key');
            expect(redisClient.del).not.toHaveBeenCalled();
        });

        it('invalidate() should do nothing', async () => {
            await cacheService.invalidate('any-pattern:*');
            expect(redisClient.scanStream).not.toHaveBeenCalled();
        });
    });

    describe('get', () => {
        it('should return a parsed value if key exists', async () => {
            const key = 'test-key';
            const value = { data: 'my-data' };
            redisClient.get.mockResolvedValue(JSON.stringify(value));

            const result = await cacheService.get(key);

            expect(redisClient.get).toHaveBeenCalledWith(`${CACHE_PREFIX}${key}`);
            expect(result).toEqual(value);
            expect(cacheService.cacheMetrics.hits).toBe(1);
            expect(cacheService.cacheMetrics.misses).toBe(0);
        });

        it('should return null if key does not exist', async () => {
            const key = 'non-existent-key';
            redisClient.get.mockResolvedValue(null);

            const result = await cacheService.get(key);

            expect(result).toBeNull();
            expect(cacheService.cacheMetrics.hits).toBe(0);
            expect(cacheService.cacheMetrics.misses).toBe(1);
        });

        it('should return null and log an error on timeout', async () => {
            const key = 'timeout-key';
            redisClient.get.mockImplementation(() => new Promise(resolve => setTimeout(() => resolve('{}'), 1000))); // Exceeds 500ms timeout

            const result = await cacheService.get(key);

            expect(result).toBeNull();
            expect(cacheService.cacheMetrics.errors).toBe(1);
        });
    });

    describe('set', () => {
        it('should stringify and set a value with TTL', async () => {
            const key = 'set-key';
            const value = { a: 1 };
            const ttl = 300;

            await cacheService.set(key, value, ttl);

            expect(redisClient.set).toHaveBeenCalledWith(
                `${CACHE_PREFIX}${key}`,
                JSON.stringify(value),
                'EX',
                ttl
            );
        });
    });

    describe('del', () => {
        it('should delete a key', async () => {
            const key = 'del-key';
            await cacheService.del(key);
            expect(redisClient.del).toHaveBeenCalledWith(`${CACHE_PREFIX}${key}`);
        });
    });

    describe('invalidate', () => {
        it('should scan for keys and delete them using a pipeline', async () => {
            const pattern = 'rooms:*';
            const prefixedPattern = `${CACHE_PREFIX}${pattern}`;

            // Populate mock store
            const store = redisClient.__getStore();
            store.set(`${CACHE_PREFIX}rooms:1`, '{"data":"room1"}');
            store.set(`${CACHE_PREFIX}rooms:2`, '{"data":"room2"}');
            store.set(`${CACHE_PREFIX}users:1`, '{"data":"user1"}');

            const pipeline = {
                del: jest.fn(),
                exec: jest.fn().mockResolvedValue([
                    [null, 1],
                    [null, 1]
                ]),
            };
            redisClient.pipeline.mockReturnValue(pipeline);

            await cacheService.invalidate(pattern);

            expect(redisClient.scanStream).toHaveBeenCalledWith({
                match: prefixedPattern,
                count: 100,
            });

            // Check that the pipeline was used to delete the correct keys
            expect(pipeline.del).toHaveBeenCalledTimes(2);
            expect(pipeline.del).toHaveBeenCalledWith(`${CACHE_PREFIX}rooms:1`);
            expect(pipeline.del).toHaveBeenCalledWith(`${CACHE_PREFIX}rooms:2`);
            expect(pipeline.del).not.toHaveBeenCalledWith(`${CACHE_PREFIX}users:1`);
            expect(pipeline.exec).toHaveBeenCalledTimes(1);
        });

        it('should not execute pipeline if no keys match', async () => {
            const pattern = 'non-existent:*';
            redisClient.__getStore().clear();

            const pipeline = {
                del: jest.fn(),
                exec: jest.fn(),
            };
            redisClient.pipeline.mockReturnValue(pipeline);

            await cacheService.invalidate(pattern);

            expect(pipeline.exec).not.toHaveBeenCalled();
        });
    });
});
