#!/bin/sh
set -e
cd /app
export DATABASE_URL="${DATABASE_URL:-file:./prisma/data/dev.db}"
npx prisma db push --skip-generate
npx prisma db seed
exec npm run start
