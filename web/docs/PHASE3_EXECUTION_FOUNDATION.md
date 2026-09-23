# Phase 3 execution foundation

Phase 3 is integrated into the canonical Prisma schema. `AgentRun` and `ExecutionRecord` are persisted with owner-scoped project/task relations. The execution boundary remains fail-closed: no shell, filesystem, network, sandbox, model, GitHub, or autonomous execution is enabled.

`agent-runtime.ts` defines provider-neutral agents and an explicit state machine. `execution.ts` defines bounded execution policy, a rejecting executor, and execution transitions. `authorization.ts` validates project/task/agent-run relationships. `runtime-persistence.ts` persists only authorized records and emits typed agent lifecycle events. `tools-runtime.ts` exposes disabled metadata-only tool definitions. `verification-runtime.ts` evaluates supplied evidence only. `orchestrator-foundation.ts` represents Plan → Execute → Build → Test → Verify without running it.

Before deployment, run `npx prisma validate`, `npx prisma generate`, `npx prisma migrate deploy`, `npm run typecheck`, `npm test`, `npm run lint`, and `npm run build` in an environment with PostgreSQL configured. Runtime validation was not performed by this change.
