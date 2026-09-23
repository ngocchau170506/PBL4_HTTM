import pkg from '@prisma/client';
const { PrismaClient } = pkg;
import dotenv from 'dotenv';

dotenv.config();

// Các đoạn code bên dưới (khởi tạo prisma, hàm connection...) GIỮ NGUYÊN 100%
const prisma = new PrismaClient();

export const connection = async () => {
    try {
        await prisma.$queryRaw`SELECT 1`;
        console.log('🚀 Prisma v6 Connected Successfully to PostgreSQL!');
    } catch (error) {
        console.error('❌ Error connecting to Database:', error);
        process.exit(1);
    }
};

export default prisma;