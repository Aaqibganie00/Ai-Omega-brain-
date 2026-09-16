"""
ORCHESTRATION LAYER — WORKER IMPLEMENTATIONS
----------------------------------------------------
Every worker function has the signature:
    (subtask, shared_state, tool_registry, provider) -> WorkerResult

None of these reimplement logic that already exists:
  - CodingWorker wraps agent.CodingAgentWorker (Phase 3) unmodified.
  - DebuggingWorker wraps repair.AutonomousTaskExecutor (Phase 4) unmodified.
  - ReviewWorker wraps repair.Critic (Phase 4) unmodified.
  - VerificationWorker wraps repair.TaskVerifier (Phase 4) unmodified.
PlannerWorker, ResearchWorker, TestingWorker are new but intentionally
thin - TestingWorker literally just calls the existing run_tests tool.
"""

import time
from tools import ToolCall, Permission
from agent.coding_worker import CodingAgentWorker, DEFAULT_MAX_TOOL_TURNS
from repair import AutonomousTaskExecutor, Critic, TaskVerifier
from providers import AIRequest
from .schemas import WorkerResult, WorkerStatus, WorkerDefinition


def _timed(fn):
    def wrapper(subtask, shared_state, tool_registry, provider):
        start = time.time()
        result = fn(subtask, shared_state, tool_registry, provider)
        result.execution_time = time.time() - start
        return result
    return wrapper


@_timed
def planner_worker(subtask, shared_state, tool_registry, provider):
    resp = provider.generate(AIRequest.simple(f"Plan: {subtask.description}"))
    return WorkerResult(worker_id="planner_worker", task_id=subtask.subtask_id, status=WorkerStatus.COMPLETED.value, output=resp.content)


@_timed
def research_worker(subtask, shared_state, tool_registry, provider):
    resp = provider.generate(AIRequest.simple(f"Research: {subtask.description}"))
    return WorkerResult(worker_id="research_worker", task_id=subtask.subtask_id, status=WorkerStatus.COMPLETED.value, output=resp.content)


@_timed
def coding_worker_adapter(subtask, shared_state, tool_registry, provider):
    worker = CodingAgentWorker(
        provider=provider, tool_registry=tool_registry,
        max_tool_turns=getattr(shared_state, "max_tool_turns", None) or DEFAULT_MAX_TOOL_TURNS,
    )
    session_result = worker.execute_task(subtask.description, task_id=subtask.subtask_id)
    files_changed = sorted({
        c["arguments"].get("path") for c in session_result.tool_calls_made
        if c["success"] and c["tool_name"] in ("write_file", "edit_file") and c["arguments"].get("path")
    })
    status = WorkerStatus.COMPLETED.value if session_result.status == "COMPLETED" else WorkerStatus.FAILED.value
    errors = [] if status == WorkerStatus.COMPLETED.value else [session_result.status]
    return WorkerResult(worker_id="coding_worker", task_id=subtask.subtask_id, status=status,
                         output=session_result.final_response, files_changed=files_changed,
                         tools_used=[c["tool_name"] for c in session_result.tool_calls_made], errors=errors)


@_timed
def testing_worker(subtask, shared_state, tool_registry, provider):
    call = ToolCall(tool_name="run_tests", arguments={})
    result = tool_registry.execute(call)
    shared_state.record_test_result(result.output or {})
    passed = bool((result.output or {}).get("passed"))
    status = WorkerStatus.COMPLETED.value if passed else WorkerStatus.FAILED.value
    errors = [] if passed else [(result.output or {}).get("stderr", "tests failed")[:300]]
    return WorkerResult(worker_id="testing_worker", task_id=subtask.subtask_id, status=status,
                         output=f"tests passed={passed}", tools_used=["run_tests"], errors=errors)


@_timed
def debugging_worker(subtask, shared_state, tool_registry, provider):
    executor = AutonomousTaskExecutor(provider=provider, tool_registry=tool_registry, max_repair_attempts=2)
    exec_result = executor.run(subtask.description, task_id=subtask.subtask_id)
    # Post-repair test evidence enters the shared state so downstream gates
    # see the POST-REPAIR truth (not the pre-repair failure).
    if exec_result.final_test_result:
        shared_state.record_test_result(exec_result.final_test_result)
    status = WorkerStatus.COMPLETED.value if exec_result.final_state == "COMPLETED" else WorkerStatus.FAILED.value
    errors = [] if status == WorkerStatus.COMPLETED.value else [exec_result.status_reason]
    return WorkerResult(worker_id="debugging_worker", task_id=subtask.subtask_id, status=status,
                         output=exec_result.status_reason, files_changed=exec_result.files_changed_total, errors=errors)


# P0-1: entry points the build/run worker knows how to execute. Deterministic
# convention: the coding flow is instructed (and tested) to produce one of
# these at the sandbox root. Anything else = honest "no runnable artifact".
BUILD_RUN_ENTRY_CANDIDATES = ("main.py", "app.py", "run.py", "game.py")
BUILD_RUN_TIMEOUT_SECONDS = 20


@_timed
def build_run_worker(subtask, shared_state, tool_registry, provider):
    """Executes the built artifact through the EXISTING run_command tool —
    full ToolRegistry permission/audit/sandbox/classification/timeout path,
    no second execution mechanism. Evidence is structured in
    shared_state.run_results for the Quality Gate."""
    listing = tool_registry.execute(ToolCall(tool_name="list_files", arguments={"path": ".", "recursive": False}))
    names = set()
    if listing.ok and isinstance(listing.output, dict):
        # list_files returns {"path": ..., "entries": [relative strings, ...]};
        # directories carry a trailing "/".
        for entry in listing.output.get("entries", []):
            name = str(entry).rstrip("/")
            if "/" not in name:  # project root only
                names.add(name)

    entry = next((c for c in BUILD_RUN_ENTRY_CANDIDATES if c in names), None)
    if entry is None:
        run_result = {
            "command": None, "entry_point": None, "ok": False, "returncode": None,
            "stdout": "", "stderr": "",
            "error": f"no runnable entry point found at project root (expected one of {BUILD_RUN_ENTRY_CANDIDATES})",
        }
        shared_state.record_run_result(run_result)
        return WorkerResult(worker_id="build_run_worker", task_id=subtask.subtask_id,
                             status=WorkerStatus.FAILED.value, output=run_result["error"],
                             tools_used=["list_files"], errors=[run_result["error"]])

    # -B: never consult a stale .pyc for the entry point or its imports -
    # a same-mtime/same-size repair edit must be visible to the immediate
    # re-run. Same stale-bytecode immunity as the run_tests default command.
    command = f"python3 -B {entry}"
    try:
        execution = tool_registry.execute(ToolCall(tool_name="run_command", arguments={
            "command": command, "cwd": ".", "timeout": BUILD_RUN_TIMEOUT_SECONDS,
        }))
        if execution.ok and isinstance(execution.output, dict):
            rc = execution.output.get("returncode")
            stdout = (execution.output.get("stdout") or "")[:1000]
            stderr = (execution.output.get("stderr") or "")[:1000]
            ok = rc == 0
            run_result = {"command": command, "entry_point": entry, "ok": ok,
                          "returncode": rc, "stdout": stdout, "stderr": stderr, "error": None}
        else:
            run_result = {"command": command, "entry_point": entry, "ok": False,
                          "returncode": None, "stdout": "",
                          "stderr": (execution.error or "run_command execution failed")[:1000],
                          "error": execution.error or "run_command execution failed"}
    except TimeoutError as e:
        # The tool's own timeout boundary fired: a hanging process is a failed
        # run-check (cannot distinguish "healthy long-running server" - MVP
        # semantics, documented).
        run_result = {"command": command, "entry_point": entry, "ok": False,
                      "returncode": None, "stdout": "", "stderr": str(e)[:1000],
                      "error": f"run timed out ({BUILD_RUN_TIMEOUT_SECONDS}s)"}

    shared_state.record_run_result(run_result)
    if run_result["ok"]:
        return WorkerResult(worker_id="build_run_worker", task_id=subtask.subtask_id,
                             status=WorkerStatus.COMPLETED.value,
                             output=f"artifact executed: {command} (rc=0)",
                             tools_used=["list_files", "run_command"], errors=[])
    return WorkerResult(worker_id="build_run_worker", task_id=subtask.subtask_id,
                         status=WorkerStatus.FAILED.value,
                         output=f"artifact execution failed: {run_result['error'] or run_result['stderr'][:200]}",
                         tools_used=["list_files", "run_command"],
                         errors=[run_result["error"] or f"exit code {run_result['returncode']}"])


@_timed
def review_worker(subtask, shared_state, tool_registry, provider):
    test_result = shared_state.test_results[-1] if shared_state.test_results else {"passed": False}
    critic_result = Critic().review(shared_state.objective, sorted(shared_state.files_changed), test_result)
    status = WorkerStatus.COMPLETED.value if critic_result.approved else WorkerStatus.FAILED.value
    return WorkerResult(worker_id="review_worker", task_id=subtask.subtask_id, status=status,
                         output=f"approved={critic_result.approved}", errors=critic_result.issues)


@_timed
def verification_worker(subtask, shared_state, tool_registry, provider):
    test_result = shared_state.test_results[-1] if shared_state.test_results else {"passed": False}
    required_files = sorted(shared_state.files_changed)
    verification = TaskVerifier().verify(tool_registry, required_files, test_result)
    status = WorkerStatus.COMPLETED.value if verification.passed else WorkerStatus.FAILED.value
    return WorkerResult(worker_id="verification_worker", task_id=subtask.subtask_id, status=status,
                         output=verification.detail, tools_used=["read_file"] * len(required_files))


def build_default_registry():
    from .worker_registry import WorkerRegistry
    registry = WorkerRegistry()
    registry.register(WorkerDefinition("planning", {"planning"}, {Permission.READ}, "Produces a short plan.", planner_worker))
    registry.register(WorkerDefinition("research", {"research"}, {Permission.READ}, "Gathers/summarizes information.", research_worker))
    registry.register(WorkerDefinition("coding", {"coding"}, {Permission.READ, Permission.WRITE}, "Implements code via the existing CodingAgentWorker.", coding_worker_adapter))
    registry.register(WorkerDefinition("testing", {"testing"}, {Permission.READ, Permission.EXECUTE}, "Runs the test suite via the existing run_tests tool.", testing_worker))
    registry.register(WorkerDefinition("build_run", {"build_run"}, {Permission.READ, Permission.EXECUTE}, "Executes the built artifact via the existing run_command tool.", build_run_worker))
    registry.register(WorkerDefinition("debugging", {"debugging"}, {Permission.READ, Permission.WRITE, Permission.EXECUTE}, "Repairs failures via the existing Phase 4 executor.", debugging_worker))
    registry.register(WorkerDefinition("review", {"review"}, {Permission.READ}, "Reviews results via the existing Critic.", review_worker))
    registry.register(WorkerDefinition("verification", {"verification"}, {Permission.READ}, "Verifies via the existing TaskVerifier.", verification_worker))
    return registry
