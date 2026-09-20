# Omega Brain — MVP

A real, runnable skeleton of the architecture described in the spec:
Omega Core → Task Planner (DAG) → Model Router → Specialized Workers →
Memory (working/project/execution) → Verification Engine → AI Heart →
Error Recovery Engine.

## Run it

```bash
python3 main.py
```

This runs the three benchmark tasks from the spec's own section 18:
1. "Create a simple web application"
2. "Create a small 3D game prototype"
3. "Analyze a complex technical problem and produce a verified solution"

Each request is decomposed into a dependency graph, executed batch by batch
(parallel where dependencies allow), routed to a specialized worker, checked
by the verification engine, and evaluated by the AI Heart before being
marked done. Failures trigger the error recovery engine instead of blind
retries.

## What's real vs. what's mocked

**Real (fully functional):**
- Task DAG with dependency resolution and parallel-batch scheduling
- Model/agent router with capability matching and performance-based selection
- Three memory layers (working, project/persisted, execution log)
- Verification engine that actually compiles generated Python and validates
  HTML shape (not a pretend "trust it" step)
- AI Heart as a real constraints/retry-budget checker, separate from
  technical correctness
- Error recovery engine that reads execution history and picks a strategy
  (retry / switch backend / escalate) rather than looping forever

**Mocked (clearly labeled in code, `workers.py`):**
- The actual "intelligence" of each worker. There's no external model API
  wired in here, so workers return deterministic template output instead of
  calling a real LLM. The `CodingWorker` does return two genuinely
  compilable snippets (a Flask backend, an HTML page) so the verification
  step has real material to check.
- The task planner is rule-based for 3 request categories, not an LLM. See
  `LLMTaskPlanner` stub in `task_planner.py` for where a real model call
  would plug in — same interface, drop-in replacement.

## What would be needed for the real thing

- API keys/credentials for one or more real model backends, wired into
  `workers.py` (`BaseWorker.run` → real API call)
- A sandboxed execution environment for running/testing generated code
  beyond syntax checks (this MVP only compiles, it doesn't execute)
- Real tool integrations (file system, git, browser, build systems) behind
  the `ModelBackend`-style permission boundary the spec calls for
- For 3D/game or Android output specifically: actual engine toolchains
  (Unity/Godot/Android SDK) invoked from a build worker — no engine is
  installed in this environment, so game/app output here is pseudocode,
  not a compiled project. This distinction matters and the code doesn't
  pretend otherwise.

## Provider configuration (environment variables, non-secret docs)

Real provider API keys are read from the environment only — never from
committed code, and keys never appear in errors, logs, events, or normalized
responses. Set these before constructing the provider (e.g.
`ClaudeProvider()` / `GeminiProvider()`); tests never need real keys.

**Claude** (`providers.ClaudeProvider`):

| Variable | Required | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes (real calls) | — |
| `ANTHROPIC_MODEL` | no | `claude-sonnet-4-6` |
| `ANTHROPIC_TIMEOUT_SECONDS` | no | `30` |
| `ANTHROPIC_MAX_TOKENS` | no | `1024` |
| `ANTHROPIC_TEMPERATURE` | no | unset |

**Gemini** (`providers.GeminiProvider` — REST `generateContent`, key travels
only in the `x-goog-api-key` header, never in the URL):

| Variable | Required | Default |
|---|---|---|
| `GEMINI_API_KEY` | yes (real calls) | — |
| `GEMINI_MODEL` | no | `gemini-3.8-flash` |
| `GEMINI_TIMEOUT_SECONDS` | no | `120.0` (must be > 0) |
| `GEMINI_MAX_TOKENS` | no | `2048` (must be a positive integer) |
| `GEMINI_TEMPERATURE` | no | unset (must be >= 0) |

Invalid numeric values fail fast at `GeminiConfig.from_env()`. Missing keys
raise `MissingAPIKeyError` naming the variable, never its value.

Transient Gemini failures - request timeouts, connection errors, HTTP 429 and
HTTP 5xx - are retried up to 3 times with exponential backoff (0.5s, 1s, 2s).
A `Retry-After` header is honored but capped at 8s. Deterministic failures
(401/403, malformed responses, safety-blocked prompts) fail on the first
attempt. Retry tuning lives on `GeminiProvider.MAX_ATTEMPTS`,
`INITIAL_BACKOFF_SECONDS`, `BACKOFF_MULTIPLIER` and `MAX_BACKOFF_SECONDS`.

## P0 core behaviors (orchestration truth)

- Build/run stage: "build a project" plans include a `runcheck` step (capability `build_run`). The build_run worker reads the project root via `list_files`, picks the first entry point of `main.py`, `app.py`, `run.py`, `game.py` at the sandbox root, and executes it as `python3 {entry}` through the existing `run_command` tool (20 second timeout, sandboxed, audited). The run must exit 0; its evidence (`command`, `returncode`, `stdout`, `stderr`, `ok`) is recorded in `SharedTaskState.run_results`, aggregated, and consumed by the Quality Gate as a real requirement check. Missing entry, denial, timeout, or non-zero exit produce an honest FAILED worker. Long-running servers are a known P2 limitation (they time out at 20 s).
- Failure repair: a FAILED coding/testing/build_run worker triggers at most one bound repair dispatch (`max_repair_dispatches=1`) through the existing `AutonomousTaskExecutor`, then a re-verification retry of the original step so fresh test/run evidence lands after the failing entries. The original failed result is never rewritten; recovery is tracked in `recovered_failures` and events (`REPAIR_DISPATCHED/RESOLVED/RETRY_*`, `REPAIR_EXHAUSTED`).
- run_command policy (docs truth): any shell command requires the `EXECUTE` permission. Documentation phrases like "READ commands work" mean plain shell commands work under the default registry policy, which grants `{READ, WRITE, EXECUTE}`. NETWORK-classified (e.g. `curl`) and DESTRUCTIVE-classified (e.g. `rm -rf`) commands are denied unless those permissions are granted explicitly. Dependency strategy for the MVP: stdlib projects only, or an explicitly permissioned install step (`NETWORK` grant required for package downloads).
- Bounded plan revision: on a final QualityGate REJECTED verdict, `PlannedOrchestrator` asks the existing `PlanReviser` for at most one revised plan (`max_revisions=1`), re-validates it, and executes it once. BLOCKED (integrity/security) verdicts never trigger a revision.
- Tool-turn budget: `ResourceLimits.max_tool_turns` (default 12 on the orchestration path) flows into the coding worker's tool-turn cap. The bare `ToolUseSession` default stays 6 and is settable via its constructor, so runaway loops remain bounded in every lane.

## File map

- `omega_core.py` — coordinator, ties everything together
- `task_planner.py` — request → task DAG
- `router.py` — model/agent selection abstraction
- `workers.py` — specialized worker implementations (mocked backends)
- `memory.py` — working / project / execution memory
- `verification.py` — verification engine + AI Heart
- `error_recovery.py` — failure-handling strategy selection
- `main.py` — runs the 3 benchmark tasks
