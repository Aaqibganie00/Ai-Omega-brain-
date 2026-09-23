# BLINDBRAIN AI

BLINDBRAIN AI is a foundation for creating digital products through explicit requirement understanding, planning, agent selection, model/tool selection, execution, testing, debugging, critic review, and verified delivery. It is not a chatbot wrapper.

## Existing system

The repository already contains a Python Omega Brain runtime with a DAG planner, provider abstraction, sandboxed tool registry, agent tool-use loop, project memory, repair, learning, quality gates, and 400+ standard-library tests. That runtime is preserved.

## Web foundation

The first TypeScript web milestone lives in `web/` and uses Next.js App Router, React, TypeScript strict mode, Tailwind, Zod validation, and provider-neutral domain interfaces. The current persistence adapter is intentionally an in-memory development repository; replace it with PostgreSQL behind the same repository boundary before production deployment.

```bash
cd web
npm install
npm run typecheck
npm test
npm run dev
```

The dashboard is available at `http://localhost:3000`. API keys are server-only environment configuration; no provider is called by the foundation UI. Set `AUTH_DISABLED=true` to exercise the explicit unauthenticated error path. The default local session is a development adapter, not production authentication.

## Architecture

- `app/`: Next.js routes and dashboard UI.
- `lib/domain.ts`: validated project/task/memory/event contracts.
- `lib/projects.ts`: ownership-aware repository boundary (in-memory only for v1).
- `lib/auth.ts`: server session boundary, ready for Auth.js/managed identity.
- `lib/agents.ts`: generic agent contract and registry.
- `lib/models.ts`: model-provider interface and explicit mock adapter.
- `lib/tools.ts`: risk-labelled tool registry; restricted execution fails closed.
- `lib/orchestrator.ts`: planning-only orchestration boundary.
- `lib/verification.ts`: evidence-based verification; unexecuted work is incomplete.
- `lib/events.ts`: structured event vocabulary for activity streams.

See `docs/ARCHITECTURE.md`, `docs/ENVIRONMENT.md`, `docs/AGENTS.md`, `docs/MODELS.md`, and `docs/TOOLS.md`.

## Honest scope

This milestone does not execute arbitrary code, call a real model, persist to PostgreSQL, or provide production authentication. Those are deliberately isolated follow-up adapters requiring sandboxing, migrations, secrets management, and deployment validation.
