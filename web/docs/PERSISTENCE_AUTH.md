# Phase 2: persistence and authentication

The web app now uses Prisma with PostgreSQL. Set `DATABASE_URL`, `NEXTAUTH_URL`, and `NEXTAUTH_SECRET` from `.env.example`, then run:

```bash
npm install
npm run db:generate
npm run db:push
npm run dev
```

Email/password signup is available at `POST /api/auth/signup`; sign-in/sign-out use NextAuth's credentials provider at `/api/auth/*`. The dashboard APIs require a server-side NextAuth session. Passwords are bcrypt-hashed and no password or provider secret is returned to clients.

Every project query includes `ownerId`. Project deletion cascades tasks, memory, and activity events through Prisma relations. Task creation validates input, verifies project ownership, persists the task, and records `task.created`.

The implementation intentionally does not add model calls, autonomous execution, terminal access, sandboxing, vector memory, GitHub automation, or paid infrastructure.
