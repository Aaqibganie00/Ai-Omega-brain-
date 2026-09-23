# Environment

No provider key is required for the foundation. Keep `.env*` files out of Git.

| Variable | Purpose | Default |
|---|---|---|
| `AUTH_DISABLED` | Explicitly exercise missing-session behavior | unset |
| `DATABASE_URL` | Reserved PostgreSQL adapter connection | unset |
| `OPENAI_API_KEY` | Future server-only provider | unset |
| `ANTHROPIC_API_KEY` | Future server-only provider | unset |
| `GOOGLE_API_KEY` | Future server-only provider | unset |

Only server modules may read provider credentials. Never prefix secrets with `NEXT_PUBLIC_`.
