"""
SELF-CORRECTION LAYER — AUTONOMOUS TASK EXECUTOR
-------------------------------------------------------
Orchestrates: (optional creation via the existing CodingAgentWorker from
Phase 3) -> TEST -> ANALYZE -> FIX -> RETEST -> CRITIC -> VERIFY, bounded
by MAX_REPAIR_ATTEMPTS, MAX_EXECUTION_TIME, and loop detection on repeated
identical failures. State is tracked explicitly via TaskState at every
step - never inferred from log text.

    Task -> Planner(existing) -> Executor(this) -> Tester -> FailureAnalyzer
         -> Fixer -> Retester -> Critic -> Verifier -> Final Result

Every tool interaction still goes through the existing, unmodified
ToolRegistry (or ScopedToolRegistry wrapping it during repair) - this file
never touches the filesystem directly.
"""

import time
import uuid

from tools import ToolCall
from agent.events import AgentEventLog
from .schemas import TaskState, ExecutionResult, RepairAttempt
from .failure_analyzer import FailureAnalyzer
from .fixer import DebuggerFixerWorker
from .critic import Critic
from .task_verifier import TaskVerifier

DEFAULT_MAX_REPAIR_ATTEMPTS = 3
DEFAULT_MAX_EXECUTION_SECONDS = 120
DEFAULT_MAX_TOOL_TURNS_PER_REPAIR = 4


class AutonomousTaskExecutor:
    def __init__(self, provider, tool_registry, project_memory=None,
                 max_repair_attempts=DEFAULT_MAX_REPAIR_ATTEMPTS,
                 max_tool_turns_per_repair=DEFAULT_MAX_TOOL_TURNS_PER_REPAIR,
                 max_execution_seconds=DEFAULT_MAX_EXECUTION_SECONDS):
        self.provider = provider
        self.tool_registry = tool_registry
        self.project_memory = project_memory
        self.max_repair_attempts = max_repair_attempts
        self.max_tool_turns_per_repair = max_tool_turns_per_repair
        self.max_execution_seconds = max_execution_seconds
        self.analyzer = FailureAnalyzer()
        self.critic = Critic()
        self.verifier = TaskVerifier()

    def run(self, objective: str, task_id: str = None, creation_worker=None,
            test_command: str = None, test_path: str = ".", required_files: list = None) -> ExecutionResult:
        task_id = task_id or uuid.uuid4().hex[:12]
        log = AgentEventLog()
        start_time = time.time()

        state = TaskState.CREATED
        log.emit("TASK_STARTED", task_id, detail=objective[:200])

        state = TaskState.PLANNING
        state = TaskState.EXECUTING
        files_changed_total = []
        if creation_worker is not None:
            try:
                creation_result = creation_worker.execute_task(objective, task_id=f"{task_id}-create")
                files_changed_total = sorted({
                    c["arguments"].get("path") for c in creation_result.tool_calls_made
                    if c["success"] and c["tool_name"] in ("write_file", "edit_file", "create_project") and c["arguments"].get("path")
                })
            except Exception as e:
                log.emit("ERROR", task_id, detail=f"creation phase failed: {e}")
                return self._finalize(task_id, TaskState.FAILED_FINAL, [], [], {}, None, None, log,
                                       f"creation phase raised: {e}")

        attempts = []
        seen_failure_signatures = {}
        attempt_num = 0
        test_result = {}
        critic_result = None
        verification = None

        while True:
            if time.time() - start_time > self.max_execution_seconds:
                log.emit("REPAIR_LIMIT_REACHED", task_id, detail="max_execution_seconds exceeded")
                return self._finalize(task_id, TaskState.LIMIT_REACHED, attempts, files_changed_total,
                                       test_result, critic_result, verification, log, "execution timeout")

            state = TaskState.TESTING if attempt_num == 0 else TaskState.RETESTING
            log.emit("RETEST_STARTED", task_id, detail=f"attempt={attempt_num}")
            test_result = self._run_tests(test_command, test_path)
            log.emit("RETEST_RESULT", task_id, success=test_result.get("passed"))

            if test_result.get("passed"):
                state = TaskState.VERIFYING
                log.emit("CRITIC_STARTED", task_id)
                critic_result = self.critic.review(objective, files_changed_total, test_result, attempts_made=attempt_num)
                log.emit("CRITIC_RESULT", task_id, success=critic_result.approved,
                         detail="; ".join(critic_result.issues) if critic_result.issues else None)

                log.emit("VERIFICATION_STARTED", task_id)
                verification = self.verifier.verify(self.tool_registry, required_files or [], test_result)
                log.emit("VERIFICATION_RESULT", task_id, success=verification.passed,
                         detail=verification.detail)

                if critic_result.approved and verification.passed:
                    state = TaskState.COMPLETED
                    return self._finalize(task_id, state, attempts, files_changed_total,
                                           test_result, critic_result, verification, log, "completed successfully")
                # tests passed but critic/verifier rejected — treat as a
                # failure requiring another repair round, synthesizing a
                # test_result-shaped record so the analyzer has something
                # concrete (not a fabricated guess) to work from.
                rejection_reason = "; ".join(critic_result.issues + [verification.detail])
                synthetic_result = {
                    "passed": False, "returncode": 1,
                    "stdout": test_result.get("stdout", ""),
                    "stderr": f"Critic/Verifier rejected result: {rejection_reason}",
                }
            else:
                synthetic_result = test_result

            attempt_num += 1
            if attempt_num > self.max_repair_attempts:
                log.emit("REPAIR_LIMIT_REACHED", task_id, detail=f"max_repair_attempts={self.max_repair_attempts}")
                return self._finalize(task_id, TaskState.FAILED_FINAL, attempts, files_changed_total,
                                       test_result, critic_result, verification, log,
                                       f"repair budget exhausted after {self.max_repair_attempts} attempts")

            state = TaskState.ANALYZING
            log.emit("REPAIR_STARTED", task_id, detail=f"attempt={attempt_num}")
            analysis = self.analyzer.analyze(synthetic_result, {"objective": objective}, tool_registry=self.tool_registry)
            log.emit("FAILURE_ANALYZED", task_id, detail=f"{analysis.failure_type}: {analysis.root_cause[:150]}")

            signature = (analysis.failure_type, analysis.root_cause)
            seen_failure_signatures[signature] = seen_failure_signatures.get(signature, 0) + 1
            if seen_failure_signatures[signature] >= 3:
                log.emit("REPAIR_LIMIT_REACHED", task_id,
                         detail=f"loop detected: identical failure signature seen {seen_failure_signatures[signature]}x")
                return self._finalize(task_id, TaskState.FAILED_FINAL, attempts, files_changed_total,
                                       test_result, critic_result, verification, log,
                                       "loop detected: repeated identical failure without progress")

            state = TaskState.FIXING
            previous_summary = attempts[-1].root_cause if attempts else None
            fixer = DebuggerFixerWorker(
                provider=self.provider, tool_registry=self.tool_registry,
                max_tool_turns=self.max_tool_turns_per_repair,
            )
            try:
                fix_result = fixer.propose_fix(objective, analysis, task_id=f"{task_id}-repair-{attempt_num}",
                                                previous_attempt_summary=previous_summary)
            except Exception as e:
                log.emit("ERROR", task_id, detail=f"fixer raised: {e}")
                return self._finalize(task_id, TaskState.FAILED_FINAL, attempts, files_changed_total,
                                       test_result, critic_result, verification, log, f"fixer error: {e}")

            log.emit("FIX_PROPOSED", task_id, detail=fix_result.final_response[:200] if fix_result.final_response else None)
            files_changed_this = sorted({
                c["arguments"].get("path") for c in fix_result.tool_calls_made
                if c["success"] and c["tool_name"] in ("write_file", "edit_file") and c["arguments"].get("path")
            })
            files_changed_total = sorted(set(files_changed_total) | set(files_changed_this))
            log.emit("FIX_APPLIED", task_id, detail=f"files_changed={files_changed_this}")

            attempts.append(RepairAttempt(
                attempt_number=attempt_num, failure_type=analysis.failure_type, root_cause=analysis.root_cause,
                files_changed=files_changed_this, test_passed=False, state_after=state.value,
            ))
            # loop back to RETESTING at top of while

    def _run_tests(self, test_command, test_path) -> dict:
        call = ToolCall(tool_name="run_tests", arguments={"path": test_path, **({"test_command": test_command} if test_command else {})})
        result = self.tool_registry.execute(call)
        if not result.ok:
            # tool-system-level failure (e.g. timeout) - not a test failure,
            # but must not crash the loop; represent it as a failing result.
            return {"passed": False, "returncode": -1, "stdout": "", "stderr": result.error or "run_tests tool failed"}
        return result.output

    def _finalize(self, task_id, state, attempts, files_changed_total, test_result,
                   critic_result, verification, log, reason) -> ExecutionResult:
        result = ExecutionResult(
            task_id=task_id, final_state=state.value, attempts=attempts,
            files_changed_total=files_changed_total, final_test_result=test_result,
            critic=critic_result, verification=verification, events=log.as_dicts(),
            status_reason=reason,
        )
        if self.project_memory is not None:
            self._record_memory(result)
        return result

    def _record_memory(self, result: ExecutionResult):
        """Structured, non-sensitive repair history - never raw model
        conversations, never API keys."""
        summary = {
            "task_id": result.task_id,
            "final_state": result.final_state,
            "status_reason": result.status_reason,
            "attempts": [
                {
                    "attempt_number": a.attempt_number, "failure_type": a.failure_type,
                    "root_cause": a.root_cause[:300], "files_changed": a.files_changed,
                    "state_after": a.state_after,
                } for a in result.attempts
            ],
            "files_changed_total": result.files_changed_total,
            "final_test_passed": bool(result.final_test_result.get("passed")),
            "critic_approved": result.critic.approved if result.critic else None,
            "verification_passed": result.verification.passed if result.verification else None,
        }
        self.project_memory.remember(
            key=f"repair_session:{result.task_id}", value=summary,
            verified=(result.final_state == "COMPLETED"), source="autonomous_executor",
        )
