# KILL CRITIC - PHASE 2 MILESTONE VERIFICATION REPORT

Date: 2026-09-15 | Branch: arena/01a08426-ai-omega-brain | Spec: "REAL USER REQUEST -> REAL GEMINI -> VALIDATED REQUIREMENTS -> EXISTING OMEGA BRAIN PIPELINE"

Verdict: **PASS** on every item that can be proven offline. The single unprovable item (live Gemini service) is explicitly marked UNVERIFIED; nothing is faked.

---

## 1. Files created

| File | Lines | Purpose |
|---|---|---|
| `planning/requirement_intake.py` | 264 | Isolated intake trust boundary (leaf module) |
| `tests/test_requirement_intake.py` | 346 | 22 tests: A-N directive cases incl. offline E2E |
| `tests/test_main_cli.py` | 85 | 6 subprocess CLI tests |
| `PHASE2_MILESTONE_VERIFICATION.md` | - | This report |

## 2. Files modified

| File | Change |
|---|---|
| `main.py` | Rewritten (179 lines): argv/stdin request lane + `benchmark` preserved verbatim. OmegaCore lane confined to `run_benchmark()`; request lane never touches it. |

Nothing else modified. Zero deletions. No extra files were needed (section 20 stop conditions not triggered: no schema conflict, ExecutionSession accepts the normalized objective unchanged, GeminiProvider interface compatible, security untouched, benchmark untouched, P0 unweakened).

## 3. Exact architecture flow

```
python main.py "Build me a Python calculator app ..."   (or one-line stdin)
  -> _validate_raw_request (<=1000 chars, no control chars)
  -> build_intake_request  : AIRequest, ZERO ToolDefinitions, strict system
                             prompt, max_tokens=512, temperature=0
  -> GeminiProvider.generate  (typed errors propagate raw; never faked)
  -> extract_and_validate   : tool_calls -> IntakeError (HARD boundary)
                              -> 4000-char cap -> single-fence-only strip
                              -> json.loads -> dict -> required keys
                              -> types -> enum -> length caps
                              -> control-char reject -> language=="python"
  -> RequirementObject; normalized_objective built BY OUR CODE:
     f"Build {deliverable_type}: {summary}"
  -> workspace/<slug>-<uuid8>/ sandbox (slug regex-only, uuid suffix;
     user text can never pick a filesystem path)
  -> ToolRegistry(base_dir=sandbox) + ProjectMemory(sandbox/pm.json)
  -> ExecutionSession.run(normalized_objective)   [EXISTING lane]
     -> PlanningIntegration -> PlanValidator -> PlannedOrchestrator
     -> Orchestrator -> workers -> ToolRegistry/PermissionManager/
        classifier/sandbox/audit -> real files -> real tests ->
        real build/run -> failure detection -> repair -> retest ->
        verification -> QualityGate
  -> exit 0 = COMPLETED+APPROVED | 1 = honest non-completion |
     2 = IntakeError/provider error (nothing created, no fallback)
```

`python main.py benchmark` = legacy OmegaCore demo, behaviorally unchanged.

## 4. Requirement schema

`RequirementObject`: raw_request (str, required, <=1000), normalized_objective (str, code-constructed only), summary (str, 10-300, single line, control chars rejected), deliverable_type (enum: app|game|script|research), features (list[str], 1-8 items, each 3-80), constraints (list[str], 0-5 items, each <=100), language (must equal "python" for this MVP; anything else fails honestly). Unknown model JSON keys are ignored; missing required keys are rejected.

## 5. Gemini validation behavior

Interpreter-only role: request declares zero tools; system prompt forbids markdown, prose, tool calls, execution, shell proposals, verification claims; output token budget 512, temperature 0. Response: any `tool_calls` -> IntakeError before parsing; >4000 chars rejected; only a whole-content single ` ```json ` fence is tolerated (prose around JSON rejected); strict JSON dict; fail-closed on every rule. Provider errors (MissingAPIKeyError, AuthenticationError 401/403, RateLimitError 429, ProviderTimeoutError, NetworkError, ProviderUnavailableError) propagate unchanged and exit honestly (code 2).

## 6. Security boundaries (verified, not just asserted)

- Intake hard boundary: tool-call-bearing response rejected; ToolRegistry never reached; intake module has no registry/shell/filesystem surface at all.
- Prompt injection containment: `curl https://...`, `rm -rf /`, `ignore previous instructions` survive only as capped inert strings; none enters the code-built normalized objective; execution remains governed by ToolRegistry/PermissionManager/classifier/sandbox/audit/QualityGate.
- Live probes (this session): python exec allowed with EXECUTE (PASS); curl denied without NETWORK (PASS); destructive denied (PASS); write_file path escape denied (PASS); run_command denied without EXECUTE (PASS).
- Suite-level: test-integrity weakening -> BLOCKED, never revised (`test_blocked_integrity_never_triggers_revision`); QualityGate bypass impossible (quality+hardening suites green).
- API key never printed/logged/stored/committed anywhere in this work.

## 7-9. Test counts and full results (before -> after)

| Suite | Before | After | Result |
|---|---|---|---|
| unittest discover | 403 | **431** (403+28 new) | PASS: 431 ran, 0 failures, 1 key-gated skip |
| pytest | 403 | **430 passed, 1 skipped, 13 subtests** | PASS |
| P0 battery (test_p0_build_run_repair) | 23 | 23 | PASS 23/23 |
| security hardening suite | 19 | 19 | PASS 19/19 |
| compileall | - | exit 0, no errors | PASS |
| import sweep (9 packages + main + intake) | - | ALL OK | PASS |

Test count legitimately increased by exactly the 28 new tests (22 intake + 6 CLI). No pre-existing test was modified.

## 10. Mock-Gemini + REAL END-TO-END OFFLINE result

`test_request_flows_into_real_execution_session_and_completes` (test L): mocked-HTTP Gemini intake -> RequirementObject (`Build app: calculator application with basic arithmetic`) -> REAL ExecutionSession (tool_turn budgets, planner, orchestrator) with existing MockAIProvider coding turns -> real calculator.py/main.py/test_calculator.py on disk -> real unittest run -> real build/run returncode 0 -> verification passed -> **COMPLETED + APPROVED**, audit trail proving real tool flow. PASS.

## 11. P0 BLACK-BOX REGRESSION (directive section 15, exact scenario)

Run out-of-test-suite against the REAL Orchestrator with the directive fixture (`subtract: return a * b`):

- Chronology: `WORKER_FAILED -> REPAIR_DISPATCHED -> REPAIR_RESOLVED -> REPAIR_RETRY_STARTED -> REPAIR_RETRY_RESOLVED`
- Test sequence: `[False, True, True]` (defect fails, fixed passes twice)
- Repair delivered through ToolRegistry `edit_file`; disk source contains `return a - b`
- Final run: `python3 -B main.py`, rc=0, `APP-OUT 2` (stale-bytecode-safe `-B` preserved)
- Failure preserved in `failed_workers`, tracked in `recovered_failures`; 3 write/edit audit entries
- Result: COMPLETED + APPROVED. **PASS.**

## 12. LIVE GEMINI result

**NOT PERFORMED - API KEY NOT AVAILABLE.** `GEMINI_API_KEY` absent in this environment. Key-gated test `TestLiveGeminiIntake` exists, skips honestly, and is NOT counted as pass. The no-key CLI path is covered by passing honesty tests (exit 2, verbatim MissingAPIKeyError message, zero workspace creation). Classification per directive: intake validation = REAL (offline, deterministic); Gemini HTTP = MOCKED; provider coding turns in E2E = existing offline scripted provider; tool execution/subprocess/gate = REAL; live Gemini service = UNVERIFIED.

## 13. Known limitations / remaining blockers

1. Live Gemini round-trip unproven without a key (by design, honestly reported).
2. Gemini interprets requirements only; coding turns still use the existing offline provider in this environment.
3. Only `python` deliverables in MVP scope; other languages rejected honestly.
4. `research` deliverable type normalizes to a build-shaped objective (routed by existing decomposer keywords); coherent but build-shaped.
5. Dependency manager, servers, Docker, GUIs, packaging, cloud, streaming: explicitly out of scope, untouched.
6. This milestone does NOT prove "build any app"; it proves natural-language entry into the real pipeline through validated intake.

## 14. Git diff summary

Working-tree convention (tree untracked per repo state): `git status` shows zero modified tracked files. Added/changed in working tree: `planning/requirement_intake.py` (new), `main.py` (rewritten), `tests/test_requirement_intake.py` (new), `tests/test_main_cli.py` (new), this report + the earlier Phase 2 report. Untouched: orchestration/, tools/, repair/, quality/, execution/, planning core, providers/, memory.py, all pre-existing tests. No workspace/ or memory_*.json pollution in repo root (all probes ran from temp dirs).

---

## Per-test status (new suites, 28 tests)

**tests/test_requirement_intake.py (21 PASS, 1 NOT-PERFORMED-key-gated):**
test_valid_model_output_creates_requirement_object PASS | test_normalized_objective_constructed_in_code_not_model PASS | test_intake_request_declares_zero_tool_definitions PASS | test_raw_request_validations PASS | test_single_whole_fence_form_accepted PASS | test_missing_api_key_raises_typed_error_not_fake PASS | test_B_malformed_json_rejected PASS | test_C_trailing_prose_after_json_rejected PASS | test_D_missing_required_fields_rejected PASS (5 subtests) | test_E_wrong_types_rejected PASS | test_F_invalid_deliverable_type_rejected PASS (4 subtests) | test_G_invalid_language_rejected_for_python_mvp PASS (4 subtests) | test_H_oversized_output_and_items_rejected PASS | test_control_characters_in_fields_rejected PASS | test_empty_content_rejected PASS | test_model_tool_calls_rejected_and_never_executed PASS | test_multiple_injected_calls_rejected PASS | test_malicious_strings_are_inert_validated_data PASS | test_normalized_objective_routes_into_existing_build_decomposition PASS | test_research_type_routes_into_research_shape PASS | test_request_flows_into_real_execution_session_and_completes PASS | test_live_returns_valid_requirement_object **NOT PERFORMED (no API key)**

**tests/test_main_cli.py (6 PASS):**
test_argv_request_without_key_exits_2_and_creates_no_workspace PASS | test_stdin_request_without_key_exits_2 PASS | test_empty_invocation_prints_usage_exit_2 PASS | test_oversized_request_rejected_before_any_provider_call PASS | test_benchmark_mode_still_runs_legacy_demo PASS | test_intake_error_path_reports_and_exits_2 PASS

**Pre-existing suites:** all 403 PASS (incl. P0 23/23, security 19/19). No failures hidden anywhere in this report.

## Definition of Done checklist

- [x] Natural-language request enters through main.py (argv + stdin; CLI probe exit-2 honest path verified)
- [x] benchmark mode works (subprocess probe: exit 0, 3x [COMPLETED])
- [x] Real GeminiProvider used when configured (created unconditionally in request lane; no-key failure proven)
- [x] Gemini output treated as untrusted (caps, control chars, fence-only, never executed)
- [x] Fail-closed validation (ladder order + per-case tests)
- [x] Gemini tool calls rejected during intake (I-suite, before any parse)
- [x] normalized_objective constructed by our code (test asserts prefix + exact form)
- [x] Valid request reaches existing ExecutionSession (test L, COMPLETED+APPROVED)
- [x] Planner/validator/orchestrator authoritative (untouched; K-suite routing verified)
- [x] ToolRegistry authoritative (audit trail asserted)
- [x] PermissionManager authoritative (5/5 live probes)
- [x] Sandbox authoritative (escape denied probe; per-request workspace)
- [x] QualityGate authoritative (APPROVED only on real verification; BLOCKED path tested)
- [x] Real files created (E2E + P0 black-box)
- [x] Real tests execute (test chronology False->True->True)
- [x] P0 build/run/repair functional (23/23)
- [x] P0 black-box regression passes (section 11)
- [x] Security regression passes (probes + 19/19 suite)
- [x] Full existing test suite passes (431/431, 430+1 pytest)
- [x] No fake Gemini success (N-classification honest; errors propagate raw)
- [x] Live Gemini marked UNVERIFIED (no key available)
- [x] No API key committed or hardcoded
