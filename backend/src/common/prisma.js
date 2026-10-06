import { PrismaClient } from '@prisma/client';

let prisma;
try {
  prisma = new PrismaClient();
} catch {
  console.warn('[AI Studio] Database not connected — using mock');
  const noOp = {
    findMany: async () => [],
    findFirst: async () => null,
    findUnique: async () => null,
    create: async (d) => d?.data ?? {},
    update: async (d) => d?.data ?? {},
    delete: async () => ({}),
    count: async () => 0,
    aggregate: async () => ({}),
    groupBy: async () => [],
  };
  prisma = new Proxy({}, {
    get: (_, prop) => {
      if (prop === '$queryRaw' || prop === '$executeRaw') return async () => [];
      if (prop === '$connect' || prop === '$disconnect') return async () => {};
      return noOp;
    },
  });
}

export default prisma;
