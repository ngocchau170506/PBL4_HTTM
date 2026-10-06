import pkg from '@prisma/client';
const { PrismaClient } = pkg;
import dotenv from 'dotenv';

dotenv.config();

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
  };
  prisma = new Proxy({}, {
    get: (_, prop) => {
      if (prop === '$queryRaw' || prop === '$executeRaw') return async () => [];
      if (prop === '$connect' || prop === '$disconnect') return async () => {};
      return noOp;
    },
  });
}

export const connection = async () => {
    try {
        if (prisma.$queryRaw) {
            await prisma.$queryRaw`SELECT 1`;
        }
        console.log('🚀 Prisma v6 Connected Successfully to PostgreSQL!');
    } catch (error) {
        console.warn('⚠️ Database connection offline — proceeding with mock data');
    }
};

export default prisma;