#!/bin/sh
set -e

mkdir -p /app/uploads/videos

if [ "${AUTO_DB_PUSH:-true}" = "true" ]; then
  echo "[backend] Syncing Prisma schema (prisma db push)..."
  npx prisma db push --skip-generate
fi

if [ "${AUTO_SEED_IF_EMPTY:-true}" = "true" ]; then
  USER_COUNT=$(node -e "import('@prisma/client').then(async ({ PrismaClient }) => { const p = new PrismaClient(); try { console.log(await p.user.count()); } catch { console.log(-1); } finally { await p.\$disconnect(); } });" 2>/dev/null || echo "-1")
  if [ "$USER_COUNT" = "0" ]; then
    echo "[backend] Empty database detected — running seed.js..."
    node seed.js
  fi
fi

exec npm run start
