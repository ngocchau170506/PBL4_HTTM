// backend/src/config/__mocks__/redis.js
import { jest } from '@jest/globals';

const mockRedisStore = new Map();

const redisClient = {
    get: jest.fn(key => {
        const value = mockRedisStore.get(key);
        return Promise.resolve(value ? value : null);
    }),
    set: jest.fn((key, value, ...args) => {
        mockRedisStore.set(key, value);
        return Promise.resolve('OK');
    }),
    del: jest.fn(key => {
        mockRedisStore.delete(key);
        return Promise.resolve(1);
    }),
    scanStream: jest.fn(({ match }) => {
        const EventEmitter = require('events');
        const emitter = new EventEmitter();

        const patternString = match.replace(/\*/g, '.*');
        const pattern = new RegExp(`^${patternString}$`);
        
        const matchingKeys = [];
        for (const key of mockRedisStore.keys()) {
            if (pattern.test(key)) {
                matchingKeys.push(key);
            }
        }
        
        process.nextTick(() => {
            if (matchingKeys.length > 0) {
                emitter.emit('data', matchingKeys);
            }
            emitter.emit('end');
        });

        return emitter;
    }),
    pipeline: jest.fn(() => ({
        del: jest.fn(),
        exec: jest.fn(() => {
            return Promise.resolve([]);
        }),
    })),
    __clear: () => {
        mockRedisStore.clear();
    },
    __getStore: () => mockRedisStore,
};

const redisStatus = {
  isReady: false,
};

export { redisClient, redisStatus };
