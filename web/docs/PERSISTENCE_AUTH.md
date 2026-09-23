# Phase 2 uses PostgreSQL through Prisma.

```bash
cp .env.example .env
npm install
npx prisma validate
npx prisma generate
npm run db:migrate
npm run typecheck
npm test
npm run lint
npm run build
```

`DATABASE_URL` must point to a PostgreSQL database. `NEXTAUTH_URL` and a long random `NEXTAUTH_SECRET` are required for authentication. The committed migration is `prisma/migrations/0001_init/migration.sql`; deployments should use `npx prisma migrate deploy`.

The integration suite is skipped when `DATABASE_URL` is absent and runs real Prisma persistence tests when a test database is configured. It does not replace database-backed tests with mocks.
