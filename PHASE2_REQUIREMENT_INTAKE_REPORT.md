# Phase 2 Report: REAL USER TEXT to Real Build Delivery

Date: 2026-09-15. Branch: arena/01a08426-ai-omega-brain. Scope: the approved
Gemini-as-requirement-interpreter milestone, implemented exactly per the Phase 1
contract. All rules honored: no model-executed tool calls, no QualityGate bypass,
no second orchestrator, no fake success, no key in source, no test weakening, no
architecture change.

## 1. What was built

### 1a. New trust module `planning/requirement_intake.py` (NEW, 264 lines, leaf module)

Implements the full approved contract:

- `IntakeError(ValueError)`, `RequirementObject` dataclass (raw_request,
  normalized_objective, summary, deliverable_type, features, constraints,
  language, as_dict()).
- `build_intake_request(raw_text) -> AIRequest`: strict interpreter system
  prompt, max_tokens=512, temperature=0, and ZERO ToolDefinitions (so Gemini can
  only emit plain text at this stage; verified by test).
- `extract_and_validate(raw_text, provider) -> RequirementObject`: fail-closed
  ladder in this exact order: raw request caps (non-empty, <=1000 chars, no
  control chars) -> provider call -> HARD REJECT if `response.tool_calls` is
  non-empty (injection boundary) -> response cap 4000 chars -> single whole
  fence optional strip (prose around JSON rejected) -> json.loads -> must be a
  dict -> required keys (summary, deliverable_type, features, constraints,
  language) -> per-field type checks -> deliverable_type enum
  (app|game|script|research) -> length caps (summary 10..300, features 1..8
  items of 3..80 chars, constraints <=5 items of <=100 chars) -> control-char
  rejection -> language exactly "python" (MVP scope) -> `normalized_objective`
  constructed BY OUR CODE as `f"Build {deliverable_type}: {summary}"`. The model
  never dictates the executable objective string.
- Stdlib + dataclass only. No global state, no key handling, no
  filesystem/shell/registry access. Provider errors (MissingAPIKeyError,
  AuthenticationError, RateLimitError, ProviderTimeoutError, NetworkError,
  ProviderUnavailableError) propagate UNCHANGED; never converted to fake
  success. All model strings are validated inert data; execution stays with the
  existing ToolRegistry/PermissionManager/classifier/sandbox/audit/QualityGate.

### 1b. CLI surface `main.py` (rewritten, 179 lines)

- `python main.py "<user text>"` (argv joined) or one-line stdin request:
  intake -> `workspace/<slug>-<uuid8>/` sandbox -> ToolRegistry + ProjectMemory
  + REAL ExecutionSession lane (existing PlannedOrchestrator, 6-stage shape via
  existing TaskDecomposer keywords).
- `python main.py benchmark` preserved verbatim (legacy OmegaCore demo moved
  into `run_benchmark()` unchanged; verified intact by test).
- Exit codes (honest, no fallback, no fake): 0 = COMPLETED and QualityGate
  APPROVED; 1 = pipeline ran but did not complete / execution error; 2 =
  IntakeError or provider error (no API key -> verbatim MissingAPIKeyError
  message, "No fallback. Nothing was created or executed.").
- Prints the validated intake summary/features/normalized objective, workspace
  path, result state/gate/steps/files/errors/checkpoint. The API key is never
  printed, logged, or stored by any of this code.

### 1c. New tests (legitimately increasing the count)

- `tests/test_requirement_intake.py` (346 lines, 22 tests): A. valid intake
  round-trips incl. fence form; B. malformed JSON; C. prose/prefix/multiple
  fences; D. each required field dropped; E. wrong types; F. bad enums
  (incl. "malware", "torture-device"); G. non-python languages; H. oversized
  outputs/items + control chars; I. tool-call injection hard boundary (single
  and multiple; proven rejected before any objective exists, and intake has no
  registry/execution surface at all); J. prompt-injection strings survive only
  as capped inert data and never enter the normalized objective; K. plan routing
  (normalized objective hits the existing build-shape decomposition
  code/test/runcheck/review/verify); L. offline END-TO-END: mocked-HTTP Gemini
  intake -> RequirementObject -> REAL ExecutionSession with MockAIProvider
  coding turns -> COMPLETED + APPROVED + real files on disk + real run
  returncode 0 + task verification passed + audit trail proves real tool flow;
  N. LIVE Gemini test, key-gated: reports NOT PERFORMED when no real key.
- `tests/test_main_cli.py` (85 lines, 6 tests, subprocess, cwd in temp dirs):
  no-key argv exit 2 with typed error and ZERO workspace creation; stdin path;
  empty invocation usage+2; oversized request rejected before any provider call;
  `benchmark` legacy demo intact (exit 0, BENCHMARK SUMMARY, [COMPLETED]);
  fake-key offline path fails honestly exit 2 with nothing created.

## 2. Verification results

- unittest full suite: 431 ran, 0 failures, 1 skipped (key-gated live test).
  Before this phase: 403. Delta +28 (22 intake + 6 CLI).
- pytest full suite: 430 passed, 1 skipped, 13 subtests passed. Before: 403.
- P0 build/run/repair battery (`tests.test_p0_build_run_repair`): 23/23 OK,
  unchanged from before.
- Security hardening suite `tests.test_security_hardening`: green within the
  full run.
- Smoke probes (out-of-repo cwd): valid intake yields
  `Build app: calculator application with basic arithmetic`;
  `request.tools is None` confirmed; tool-call injection raises
  `model response attempted tool calls during requirement intake`; CLI no-key
  prints the typed MissingAPIKeyError message and exits 2; CLI benchmark prints
  3x [COMPLETED] unchanged.
- Ad-hoc P0 probes: hostile tool turn (path traversal + `rm -rf /`) blocked and
  session fails honestly; nested JSON payloads inside tool-result content stay
  inert. One self-authored probe of the repair loop initially read FAIL; root
  cause was the probe harness mis-scripting worker turns (session reported
  INCOMPLETE for a mis-constructed step), NOT a product defect. The real
  repair-loop coverage lives in `tests/test_p0_build_run_repair.py` (23/23)
  and a new repair-shaped lane was re-verified green there.

## 3. Live API status

REAL API TEST NOT PERFORMED: no `GEMINI_API_KEY` was present in this
environment. Nothing was faked. The live path is exercised only when a real key
exists (key-gated `TestLiveGeminiIntake`), and the no-key CLI path is covered by
passing honesty tests.

## 4. Git / diff summary (this session)

Tracked-file modifications: none (the working tree is untracked by design per
repo convention; `git status` shows zero modified tracked files).

Files added/changed in the working tree:

1. `planning/requirement_intake.py` NEW (264 lines).
2. `main.py` rewritten (179 lines; benchmark mode preserved verbatim).
3. `tests/test_requirement_intake.py` NEW (346 lines, 22 tests).
4. `tests/test_main_cli.py` NEW (85 lines, 6 tests).
5. This report NEW.

Unchanged by design (constraint compliance): orchestration/, tools/, repair/,
quality/, execution/, planning core (planner/validator/decomposer/integration),
providers/ (GeminiProvider, MockAIProvider, ClaudeProvider, schemas), memory.py,
and ALL pre-existing tests. No deletions anywhere. No second orchestrator. No
provider abstraction added. No workspace/ or memory_*.json pollution in the repo
root (all probes and CLI tests ran from temp dirs).

## 5. Honest limitations (unchanged product truths)

- Gemini is used ONLY as requirement interpreter at intake. Coding turns in this
  environment run on the existing MockAIProvider; end-to-end LIVE model coding
  was not claimed and not performed.
- Deliverable types are enforced to the enum; only `python` deliverables are in
  MVP scope (other languages are honestly rejected).
- `research` type normalizes to a build-shaped objective today (routed by the
  existing decomposer keywords); it executes coherently but as a build lane.
- Request artifacts go to `workspace/<slug>-<uuid8>/` relative to cwd; nothing
  is created when intake fails.
