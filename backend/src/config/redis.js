// MOCKED — in-memory, data lost on container sleep
const store = new Map();
const redisClient = {
  get: async (k) => store.get(k) ?? null,
  set: async (k, v) => { store.set(k, v); return 'OK'; },
  del: async (k) => store.delete(k),
  incr: async (k) => { const n = (store.get(k) || 0) + 1; store.set(k, n); return n; },
  on: () => {},
  status: 'ready',
};

const redisStatus = {
  isReady: true,
};

export { redisClient, redisStatus };
