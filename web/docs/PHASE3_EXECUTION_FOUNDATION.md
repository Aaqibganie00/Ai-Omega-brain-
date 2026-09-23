# Phase 3 execution foundation

Phase 3 adds provider-neutral server-side contracts without enabling arbitrary execution.

- `lib/agent-runtime.ts`: generic agent input/output, registry, and explicitly blocked foundation agents.
- `lib/execution.ts`: controlled executor request/result contracts, limits, and fail-closed executor.
- `lib/verification-runtime.ts`: build/test verification interfaces that require execution evidence.
- `lib/runtime-persistence.ts`: owner-scoped persistence for agent runs and execution records.
- `lib/orchestrator-foundation.ts`: Plan → Execute → Build → Test → Verify plan representation.

The Prisma additions are represented in `prisma/phase3-schema-additions.prisma` and the migration SQL at `prisma/migrations/0002_execution_foundation/migration.sql`. The canonical Prisma schema remains unchanged in this phase because the additions are intentionally isolated until migration/runtime validation is available. No public execution API is exposed. `RejectingExecutor` fails closed, network is disabled by default, and every persistence operation requires the authenticated project owner.

This phase does not add model APIs, shell execution, filesystem access, sandboxing, GitHub automation, self-repair, or autonomous coding.
