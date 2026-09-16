"""
ORCHESTRATION LAYER — ORCHESTRATOR
-----------------------------------------
Ties everything together:

    objective -> TaskDecomposer -> SubTasks -> TaskGraph (Phase 1, reused)
        -> ready_batch() each round -> WorkerSelector -> bounded
        ThreadPoolExecutor -> WorkerResult -> SharedTaskState
        -> ResultAggregator -> Critic/TaskVerifier (Phase 4, reused)
        -> QualityGate (Phase 5, reused) -> OrchestrationResult

Real bounded concurrency via concurrent.futures.ThreadPoolExecutor - not
simulated. MAX_TOTAL_WORKERS and MAX_TASK_DEPTH are hard, tested limits
enforced in code, not just documented as a suggestion.

WORKER TIMEOUT ENFORCEMENT (Phase 10): worker_timeout_seconds is enforced
at the future-collection boundary of each parallel batch (the safest
existing execution boundary). On expiry the worker is given an explicit
LIMIT_REACHED WorkerResult (never silently successful), the dependent
graph fails, and a WORKER_TIMEOUT event is emitted. DOCUMENTED LIMITATION:
pure-Python threads cannot be hard-killed safely, so a timed-out worker
thread is NOT force-terminated - its result is abandoned (never recorded
into shared state) and the orphaned thread may finish later in the
background. Deliberately no process-killing here. Set
worker_timeout_seconds=None to disable the deadline entirely.
"""

import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from task_planner import Task, TaskGraph
from agent.events import AgentEventLog
from repair import Critic
from repair.task_verifier import TaskVerifier
from quality.gate import QualityGate
from quality.schemas import CriticResultV2, Severity, RequirementCheck, RequirementStatus
from quality.test_integrity import TestIntegrityChecker

from .schemas import WorkerStatus, ResourceLimits, OrchestrationResult, WorkerMessage, WorkerResult, SubTask
from .shared_state import SharedTaskState
from .decomposer import TaskDecomposer
from .selector import WorkerSelector
from .aggregator import ResultAggregator
from .workers import build_default_registry


def _bridge_to_critic_v2(base_result):
    severity_map = {"blocking": Severity.BLOCKING.value, "minor": Severity.LOW.value, "none": Severity.INFO.value}
    return CriticResultV2(
        approved=base_result.approved,
        issues=[{"issue": i, "severity": severity_map.get(base_result.severity, Severity.MEDIUM.value)} for i in base_result.issues],
        severity=severity_map.get(base_result.severity, Severity.INFO.value),
        evidence=base_result.evidence, confidence={"model_confidence": None, "evidence_confidence": 1.0},
        recommendations=base_result.recommendations,
    )


class Orchestrator:
    def __init__(self, provider, tool_registry, worker_registry=None, project_memory=None, limits=None,
                 max_repair_dispatches=1):
        self.provider = provider
        self.tool_registry = tool_registry
        self.worker_registry = worker_registry or build_default_registry()
        self.project_memory = project_memory
        self.limits = limits or ResourceLimits()
        # P0-2: bounded in-graph repair dispatch. Each failed repairable
        # subtask may trigger at most this many debugging dispatches per run
        # (the repair loop itself stays bounded by AutonomousTaskExecutor's
        # own limits). 0 restores exact pre-P0-2 behavior.
        self.max_repair_dispatches = max_repair_dispatches
        self.decomposer = TaskDecomposer()
        self.selector = WorkerSelector(self.worker_registry)
        self.aggregator = ResultAggregator()

    def run(self, objective, task_id=None, depth=0, subtasks_override=None):
        task_id = task_id or uuid.uuid4().hex[:12]
        log = AgentEventLog()
        log.emit("ORCHESTRATION_STARTED", task_id, detail=f"depth={depth}")

        if depth > self.limits.max_task_depth:
            log.emit("WORKER_LIMIT_REACHED", task_id, detail=f"max_task_depth={self.limits.max_task_depth} exceeded")
            return OrchestrationResult(task_id=task_id, status="LIMIT_REACHED", events=log.as_dicts())

        subtasks = subtasks_override if subtasks_override is not None else self.decomposer.decompose(task_id, objective)
        for st in subtasks:
            st.depth = depth
        log.emit("TASK_DECOMPOSED", task_id, detail=f"{len(subtasks)} subtasks")

        shared_state = SharedTaskState(task_id=task_id, objective=objective, subtasks=subtasks)
        shared_state.status = "RUNNING"
        # P0-5: propagate the orchestration-level tool-turn budget to coding
        # workers via the existing shared-state seam (workers read it once;
        # direct session construction is unaffected).
        shared_state.max_tool_turns = self.limits.max_tool_turns

        graph = TaskGraph()
        for st in subtasks:
            graph.add(Task(id=st.subtask_id, description=st.description, worker_type=None, depends_on=list(st.dependencies)))

        total_workers_started = 0
        limit_hit = False
        messages = []
        perf = {"worker_times": {}, "concurrency_observed": 0}
        active_lock = threading.Lock()
        active_count = {"n": 0}

        def run_one(subtask):
            with active_lock:
                active_count["n"] += 1
                perf["concurrency_observed"] = max(perf["concurrency_observed"], active_count["n"])
            try:
                worker_type, reason = self.selector.select(subtask, self.tool_registry.permissions)
                if worker_type is None:
                    log.emit("WORKER_FAILED", task_id, detail=f"{subtask.subtask_id}: {reason}")
                    return subtask, WorkerResult(worker_id="none", task_id=subtask.subtask_id, status=WorkerStatus.FAILED.value, errors=[reason])

                definition = self.worker_registry.get(worker_type)
                log.emit("WORKER_SELECTED", task_id, detail=f"{subtask.subtask_id} -> {worker_type}")
                log.emit("WORKER_STARTED", task_id, tool_name=worker_type, detail=subtask.subtask_id)

                result = definition.factory(subtask, shared_state, self.tool_registry, self.provider)
                messages.append(WorkerMessage(sender=worker_type, receiver="orchestrator", task_id=subtask.subtask_id,
                                               message_type="RESULT", payload={"status": result.status}))

                if result.status == WorkerStatus.COMPLETED.value:
                    log.emit("WORKER_COMPLETED", task_id, tool_name=worker_type, detail=subtask.subtask_id, success=True)
                else:
                    log.emit("WORKER_FAILED", task_id, tool_name=worker_type, detail=subtask.subtask_id, success=False)

                perf["worker_times"][subtask.subtask_id] = result.execution_time
                return subtask, result
            finally:
                with active_lock:
                    active_count["n"] -= 1

        # Snapshot test files BEFORE any worker runs, so a genuine test-integrity
        # check (Phase 5's real TestIntegrityChecker, reused - not a stub) can
        # run after execution. Without this, a worker weakening/deleting a test
        # to force a false pass would never be caught by this orchestrator's
        # own Quality Gate call.
        integrity_checker = TestIntegrityChecker()
        tests_before_run = integrity_checker.snapshot(self.tool_registry)

        start_time = time.time()
        recovered_failures = set()

        with ThreadPoolExecutor(max_workers=self.limits.max_concurrent_workers) as pool:

            def drive_batch(batch):
                """Start one ready batch (respecting max_total_workers), collect
                results with the shared deadline, and record them. Returns
                False if the total-worker limit was hit."""
                nonlocal total_workers_started, limit_hit

                startable = []
                for t in batch:
                    if total_workers_started >= self.limits.max_total_workers:
                        log.emit("WORKER_LIMIT_REACHED", task_id, detail=f"max_total_workers={self.limits.max_total_workers}")
                        limit_hit = True
                        break
                    startable.append(t)
                    total_workers_started += 1

                if not startable:
                    return True

                subtasks_by_id = {st.subtask_id: st for st in subtasks}
                futures = {}
                for t in startable:
                    graph.mark(t.id, "running")
                    subtask = subtasks_by_id[t.id]
                    futures[pool.submit(run_one, subtask)] = t.id

                # Enforce worker_timeout_seconds at the future-collection
                # boundary. All workers in this batch started together, so a
                # shared deadline gives each worker its full budget. Workers
                # that finish early still return immediately
                # (result(timeout=...) does not add latency for done futures,
                # preserving existing non-timeout behavior).
                timeout_s = self.limits.worker_timeout_seconds
                batch_deadline = None if timeout_s is None else (time.time() + timeout_s)

                for future, tid in futures.items():
                    if batch_deadline is None:
                        subtask, result = future.result()
                    else:
                        remaining = batch_deadline - time.time()
                        if remaining <= 0:
                            subtask, result = self._timeout_result(tid, subtasks_by_id, log, task_id)
                        else:
                            try:
                                subtask, result = future.result(timeout=remaining)
                            except FutureTimeoutError:
                                subtask, result = self._timeout_result(tid, subtasks_by_id, log, task_id)

                    shared_state.record_result(subtask.subtask_id, result)
                    subtask.status = "done" if result.status == WorkerStatus.COMPLETED.value else "failed"
                    graph.mark(subtask.subtask_id, subtask.status)

                    if subtask.status == "failed":
                        log.emit("DEPENDENCY_WAIT", task_id, detail=f"{subtask.subtask_id} failed - dependents blocked")
                return True

            # Main scheduling loop (unchanged semantics: any failure stops
            # scheduling; dependents of failed nodes never become ready).
            while not graph.is_complete() and not graph.has_failed() and not limit_hit:
                batch = graph.ready_batch()
                if not batch:
                    break
                if not drive_batch(batch):
                    break

            # P0-2: bounded in-graph failure -> repair dispatch. A failed
            # coding/testing/build_run step triggers the EXISTING debugging
            # worker (which runs the existing, internally-bounded
            # AutonomousTaskExecutor) at most max_repair_dispatches times.
            # On verified repair completion the scheduler marks the repaired
            # node done (the original failed WorkerResult is NEVER rewritten)
            # so newly-unblocked dependents execute normally; everything is
            # gated and audited through the same run_one() path.
            repairs_dispatched = 0
            while repairs_dispatched < self.max_repair_dispatches and not limit_hit:
                target = self._select_repair_target(subtasks, shared_state, recovered_failures)
                if target is None:
                    break
                target_sid, failure_evidence = target
                if total_workers_started >= self.limits.max_total_workers:
                    log.emit("WORKER_LIMIT_REACHED", task_id, detail=f"max_total_workers={self.limits.max_total_workers}")
                    limit_hit = True
                    break

                repairs_dispatched += 1
                total_workers_started += 1
                dbg = SubTask(
                    f"{task_id}-repair-{repairs_dispatched}", task_id,
                    f"Repair the failed step '{target_sid}'. Failure evidence: {failure_evidence}",
                    {"debugging"},
                )
                subtasks.append(dbg)
                graph.add(Task(id=dbg.subtask_id, description=dbg.description, worker_type=None, depends_on=[]))
                graph.mark(dbg.subtask_id, "running")
                log.emit("REPAIR_DISPATCHED", task_id, detail=f"{target_sid} -> {dbg.subtask_id}: {failure_evidence[:200]}")

                subtask, result = run_one(dbg)
                shared_state.record_result(subtask.subtask_id, result)
                subtask.status = "done" if result.status == WorkerStatus.COMPLETED.value else "failed"
                graph.mark(subtask.subtask_id, subtask.status)
                perf["worker_times"][subtask.subtask_id] = result.execution_time

                if result.status == WorkerStatus.COMPLETED.value:
                    log.emit("REPAIR_RESOLVED", task_id, detail=f"{target_sid} repaired by {dbg.subtask_id}")
                    # Fresh-evidence retry: the repair session claims a fix,
                    # but the original step must be RE-EXECUTED so its
                    # test/run evidence lands AFTER the failing entries (the
                    # gate only trusts the latest measurements). The original
                    # failed WorkerResult stays FAILED in shared_state; the
                    # retry runs under a NEW id - history is never rewritten.
                    log.emit("REPAIR_RETRY_STARTED", task_id,
                             detail=f"re-executing repaired step {target_sid} for fresh evidence")
                    orig_st = next(s for s in subtasks if s.subtask_id == target_sid)
                    retry_id = f"{target_sid}-retry-1"
                    retry_st = SubTask(
                        subtask_id=retry_id,
                        parent_task_id=task_id,
                        description=f"Re-verify repaired step {target_sid}: {orig_st.description}",
                        required_capabilities=set(orig_st.required_capabilities),
                        dependencies=[],
                        priority=getattr(orig_st, "priority", 0),
                    )
                    graph.add(Task(id=retry_id, description=retry_st.description,
                                   worker_type=None, depends_on=[]))
                    graph.mark(retry_id, "running")
                    rsub, rres = run_one(retry_st)
                    shared_state.record_result(rsub.subtask_id, rres)
                    subtasks.append(retry_st)
                    rsub.status = "done" if rres.status == WorkerStatus.COMPLETED.value else "failed"
                    graph.mark(rsub.subtask_id, rsub.status)
                    perf["worker_times"][rsub.subtask_id] = rres.execution_time

                    if rres.status == WorkerStatus.COMPLETED.value:
                        recovered_failures.add(target_sid)
                        log.emit("REPAIR_RETRY_RESOLVED", task_id,
                                 detail=f"re-verification of {target_sid} passed under {rsub.subtask_id}")
                        # Scheduler resume only: dependents of the repaired
                        # step may now run. History preserved: the original
                        # failed WorkerResult stays; recovery is tracked in
                        # recovered_failures + events.
                        graph.mark(target_sid, "done")
                        while not graph.is_complete() and not limit_hit:
                            batch = graph.ready_batch()
                            if not batch:
                                break
                            if not drive_batch(batch):
                                break
                        # Any NEW failure during the resumed drive is not
                        # repaired a second time in this MVP (bounded).
                        if graph.has_failed() and repairs_dispatched >= self.max_repair_dispatches:
                            break
                    else:
                        # Retry failed: recovery did not survive re-verification.
                        log.emit("REPAIR_RETRY_FAILED", task_id,
                                 detail=f"re-verification of {target_sid} FAILED under {rsub.subtask_id}: "
                                        f"{'; '.join(rres.errors[:2])[:200]}")
                        log.emit("REPAIR_EXHAUSTED", task_id,
                                 detail=f"repair budget exhausted: unresolved failure remains at {target_sid}")
                else:
                    log.emit("REPAIR_EXHAUSTED", task_id,
                             detail=f"{target_sid} not repaired ({result.status}): {'; '.join(result.errors)[:200]}")

            for t in graph.tasks.values():
                if t.status == "pending":
                    reason = "blocked by failed dependency" if not limit_hit else "worker limit reached"
                    log.emit("DEPENDENCY_WAIT" if not limit_hit else "WORKER_LIMIT_REACHED", task_id, detail=f"{t.id}: {reason}")

        total_time = time.time() - start_time

        log.emit("RESULT_AGGREGATION_STARTED", task_id)
        aggregation = self.aggregator.aggregate(shared_state)
        # P0-2: recovery bookkeeping for upstream gates/acceptance checks
        # (never a rewrite of failed_worker history).
        aggregation["recovered_failures"] = sorted(recovered_failures)
        log.emit("RESULT_AGGREGATED", task_id, detail=f"success={len(aggregation['successful_workers'])} failed={len(aggregation['failed_workers'])}")

        final_test_result = shared_state.test_results[-1] if shared_state.test_results else {"passed": bool(aggregation["successful_workers"]) and not aggregation["failed_workers"]}
        base_critic = Critic().review(objective, aggregation["files_changed"], final_test_result)
        critic_v2 = _bridge_to_critic_v2(base_critic)
        verification = TaskVerifier().verify(self.tool_registry, aggregation["files_changed"], final_test_result)
        tests_after_run = integrity_checker.snapshot(self.tool_registry)
        integrity_result = integrity_checker.check(tests_before_run, tests_after_run)
        if not integrity_result.integrity_ok:
            log.emit("WORKER_FAILED", task_id, detail=f"test integrity violation: {integrity_result.suspicious}")

        # Phase 10 (item 6B): the TaskVerifier result is NO LONGER computed
        # and discarded - its per-check outcomes enter the Quality Gate as
        # real RequirementChecks, and the full verification + integrity
        # evidence is exposed on the aggregation for upstream layers
        # (planning final gate, learning evidence, execution sessions).
        verification_checks = [
            RequirementCheck(
                requirement=f"task verification: {check_name}",
                status=RequirementStatus.PASS.value if ok else RequirementStatus.FAIL.value,
                evidence=verification.detail,
                severity=Severity.HIGH.value,
            )
            for check_name, ok in verification.checks.items()
        ]
        aggregation["task_verification"] = {
            "passed": verification.passed, "checks": dict(verification.checks), "detail": verification.detail,
        }
        aggregation["test_integrity"] = {
            "integrity_ok": integrity_result.integrity_ok, "suspicious": list(integrity_result.suspicious),
        }

        # P0-1: build/run evidence feeds the Quality Gate as a real
        # requirement check. Only the LATEST run attempt counts (a successful
        # post-repair run supersedes an earlier failed attempt in ordering,
        # while all entries stay in the evidence trail).
        runs = aggregation.get("runs") or []
        if runs:
            last_run = runs[-1]
            verification_checks.append(RequirementCheck(
                requirement="build/run: artifact executes successfully",
                status=RequirementStatus.PASS.value if last_run.get("ok") else RequirementStatus.FAIL.value,
                evidence=(
                    f"command={last_run.get('command')} returncode={last_run.get('returncode')} "
                    f"error={last_run.get('error')} stderr={(last_run.get('stderr') or '')[:300]}"
                ),
                severity=Severity.HIGH.value,
            ))

        gate_decision, reasons = QualityGate().decide(
            requirement_checks=verification_checks, test_result=final_test_result, regression_result=None,
            file_audit=None, scope_result=None, integrity_result=integrity_result, critic_result=critic_v2,
        )

        # P0-2: failures that were verifiably repaired (and whose dependents
        # subsequently executed) no longer make the run fail. The aggregation
        # still lists them under failed_workers - recovery is tracked here in
        # recovered_failures, never by rewriting history.
        unresolved_failures = [sid for sid in aggregation["failed_workers"] if sid not in recovered_failures]

        if limit_hit:
            final_status = "LIMIT_REACHED"
        elif unresolved_failures or gate_decision.value != "APPROVED":
            final_status = "FAILED"
        else:
            final_status = "COMPLETED"

        shared_state.status = final_status
        if final_status == "COMPLETED":
            log.emit("ORCHESTRATION_COMPLETED", task_id)
        else:
            log.emit("ORCHESTRATION_FAILED", task_id, detail=f"gate={gate_decision.value}")

        result = OrchestrationResult(
            task_id=task_id, status=final_status, subtasks=subtasks,
            worker_results=dict(shared_state.worker_results), aggregation=aggregation,
            quality_decision=gate_decision.value, events=log.as_dicts(),
            performance={**perf, "total_time_seconds": total_time, "total_workers_started": total_workers_started,
                         "subtask_count": len(subtasks)},
        )

        if self.project_memory is not None:
            self._record_memory(result)
        return result

    _REPAIRABLE_CAPABILITIES = {"coding", "testing", "build_run"}

    def _select_repair_target(self, subtasks, shared_state, recovered_failures):
        """Pick the next failed, repairable subtask for a debugging dispatch.

        Repairable = FAILED worker status, repair-target capabilities,
        not already repaired, and not itself a repair dispatch. Returns
        (subtask_id, failure_evidence) or None."""
        for st in subtasks:
            if "-repair-" in st.subtask_id:
                continue
            if st.subtask_id in recovered_failures:
                continue
            result = shared_state.worker_results.get(st.subtask_id)
            if result is None or result.status != WorkerStatus.FAILED.value:
                continue
            if not (st.required_capabilities & self._REPAIRABLE_CAPABILITIES):
                continue
            evidence_bits = list(result.errors[:3])
            if shared_state.test_results:
                stderr = (shared_state.test_results[-1].get("stderr") or "")[:200]
                if stderr:
                    evidence_bits.append(f"test stderr: {stderr}")
            return st.subtask_id, " | ".join(evidence_bits)[:600] or f"step {st.subtask_id} failed"
        return None

    def _timeout_result(self, subtask_id, subtasks_by_id, log, task_id):
        """Build the explicit timeout failure for an over-time worker.
        The timed-out worker's result is never recorded as success - it is
        an explicit LIMIT_REACHED WorkerResult with the timeout as the error
        reason, and a WORKER_TIMEOUT event makes it observable. See the
        module docstring for the hard-cancellation limitation."""
        log.emit("WORKER_TIMEOUT", task_id,
                 detail=f"{subtask_id}: exceeded worker_timeout_seconds={self.limits.worker_timeout_seconds}s - result abandoned")
        result = WorkerResult(
            worker_id="worker-timeout-boundary", task_id=subtask_id,
            status=WorkerStatus.LIMIT_REACHED.value,
            errors=[
                f"worker exceeded worker_timeout_seconds={self.limits.worker_timeout_seconds}s; "
                "result abandoned (pure-Python threads cannot be hard-killed safely; the orphaned "
                "worker may finish later in the background but its output is discarded)"
            ],
        )
        return subtasks_by_id[subtask_id], result

    def _record_memory(self, result):
        summary = {
            "task_id": result.task_id, "status": result.status,
            "subtask_ids": [st.subtask_id for st in result.subtasks],
            "workers_used": sorted({r.worker_id for r in result.worker_results.values()}),
            "worker_results": {sid: {"status": r.status, "files_changed": r.files_changed} for sid, r in result.worker_results.items()},
            "dependencies": {st.subtask_id: st.dependencies for st in result.subtasks},
            "failures": result.aggregation.get("failed_workers", []),
            "final_result": result.status,
        }
        self.project_memory.remember(
            key=f"orchestration:{result.task_id}", value=summary,
            verified=(result.status == "COMPLETED"), source="orchestrator",
        )
