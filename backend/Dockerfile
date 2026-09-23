FROM node:22-alpine

WORKDIR /app

# copy package first để cache npm install
COPY package*.json ./

RUN npm ci --omit=dev

# copy toàn bộ source
COPY . .

# Prisma generate (chỉ chạy build image)
RUN npx prisma generate

EXPOSE 3000

# Production: chạy node trực tiếp (không dùng nodemon)
CMD ["npm", "run", "start"]