FROM node:22-slim

WORKDIR /app

# OpenSSL & CA certs for Prisma PostgreSQL TLS, curl for Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY package*.json ./
RUN npm ci --omit=dev

COPY . .

RUN sed -i 's/\r$//' docker-entrypoint.sh && \
    cp docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint.sh && \
    mkdir -p /app/uploads/videos && \
    npx prisma generate

EXPOSE 3000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
