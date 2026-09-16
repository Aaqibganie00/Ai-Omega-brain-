"""
EXECUTION LAYER (PHASE 10) — EXECUTION SESSION
----------------------------------------------------
Coordinates ONE end-to-end execution as a single session-scoped context:

    REQUEST
      -> CONTROLS BIND  (permission policy / limits / tool snapshot, recorded)
      -> PLAN + CONTEXT/LEARNING + EXECUTE + VERIFY + QUALITY GATE
           (delegated ENTIRELY to learning.LearningEnabledOrchestrator,
            which itself delegates to planning.PlannedOrchestrator ->
            orchestration.Orchestrator -> QualityGate - none of these are
            reimplemented or replaced here)
      -> CHECKPOINT (SessionCheckpoint embedding the existing Phase-7 plan
           checkpoint; persisted through the existing ProjectMemory)
      -> FINAL RESULT (structured ExecutionSessionResult)

Deliberate non-goals (kept out of scope to avoid a new architecture):
  - no re-planning, re-execution, re-verification or re-gating here
  - no second permission system: the session holds the EXISTING
    ToolRegistry instance (the single permission/audit pipeline) and only
    READS its policy config for the controls snapshot
  - no execution resume from checkpoints (restore = state reconstruction)

Deterministic execution controls: the session constructs the
LearningEnabledOrchestrator with the SAME tool_registry (so the same
permission policy applies everywhere) and the SAME ResourceLimits object,
which propagates into Orchestrator enforcement - including the (Phase 10)
worker_timeout_seconds deadline. tool_restrictions are the registry's
registered tools; per-worker allowed-tool narrowing remains the existing
worker-level behavior (CodingAgentWorker defaults), read and recorded
here, not redefined.
"""

import time
import uuid

from orchestration.schemas import ResourceLimits
from learning import LearningEnabledOrchestrator
from agent.events import AgentEventLog

from .schemas import SessionState, SessionControls, ExecutionSessionResult
from .checkpoint import SessionCheckpointManager


class ExecutionSession:
    def __init__(self, provider, tool_registry, project_memory, limits=None, session_id=None):
        if project_memory is None:
            raise ValueError("ExecutionSession requires a ProjectMemory instance (checkpoint persistence).")
        self.session_id = session_id or f"ses-{uuid.uuid4().hex[:10]}"
        self.provider = provider
        self.tool_registry = tool_registry
        self.project_memory = project_memory
        self.limits = limits or ResourceLimits()
        # The whole existing stack is constructed with the SAME registry and
        # the SAME limits object - controls propagate by construction, not by
        # copying values into parallel systems.
        self.orchestrator = LearningEnabledOrchestrator(
            provider=provider, tool_registry=tool_registry,
            project_memory=project_memory, limits=self.limits,
        )
        self.checkpoints = SessionCheckpointManager(project_memory)
        self.state = SessionState.CREATED.value

    def _bind_controls(self) -> dict:
        """Snapshot the controls this session runs under. Read-only."""
        granted = sorted(p.value for p in self.tool_registry.permissions.config.granted)
        limits = {
            "max_concurrent_workers": self.limits.max_concurrent_workers,
            "max_total_workers": self.limits.max_total_workers,
            "max_task_depth": self.limits.max_task_depth,
            "worker_timeout_seconds": self.limits.worker_timeout_seconds,
            # P0-5: the per-coding-worker tool-turn budget that flows from
            # these limits into ToolUseSession.max_tool_turns.
            "max_tool_turns": self.limits.max_tool_turns,
        }
        return {
            "permission_policy": {"granted": granted},
            "limits": limits,
            "tool_restrictions": {"registered_tools": self.tool_registry.available_tools()},
        }

    def restore_checkpoint(self) -> tuple:
        """Reconstruct this session's persisted checkpoint, if any.
        Returns (SessionCheckpoint, []) or (None, reasons). Read-only."""
        return self.checkpoints.restore(self.session_id)

    def run(self, objective, task_id=None) -> ExecutionSessionResult:
        task_id = task_id or uuid.uuid4().hex[:12]
        log = AgentEventLog()
        start = time.time()
        self.state = SessionState.RUNNING.value
        log.emit("SESSION_STARTED", task_id, worker_id=self.session_id, detail=objective[:200])

        controls = self._bind_controls()
        log.emit("SESSION_CONTROLS_BOUND", task_id, worker_id=self.session_id,
                 detail=f"permissions={controls['permission_policy']['granted']} limits={controls['limits']}")

        learn_result, errors = None, []
        try:
            learn_result = self.orchestrator.run(objective, task_id=task_id)
        except Exception as e:
            self.state = SessionState.ERROR.value
            errors.append(f"{type(e).__name__}: {e}")
            log.emit("SESSION_ERROR", task_id, worker_id=self.session_id, detail=str(e))

        return self._finalize(objective, task_id, controls, learn_result, errors, log, start)

    # ------------------------------------------------------------------

    def _finalize(self, objective, task_id, controls, learn_result, errors, log, start):
        plan_result = (learn_result or {}).get("plan_result") or {}
        plan_status = plan_result.get("status")              # COMPLETED | FAILED | REJECTED
        if learn_result is None:
            state = SessionState.ERROR.value
        else:
            state = {
                "COMPLETED": SessionState.COMPLETED.value,
                "REJECTED": SessionState.REJECTED.value,
            }.get(plan_status, SessionState.FAILED.value)
        self.state = state

        orch = plan_result.get("orchestration_result")
        aggregation = (orch.aggregation if orch else {}) or {}
        validation = plan_result.get("validation")

        verification = aggregation.get("task_verification") or {}
        integrity = aggregation.get("test_integrity") or {}
        quality_decision = plan_result.get("quality_decision")

        errors = list(errors)
        errors.extend(str(e) for e in aggregation.get("errors", [])[:10])
        if validation is not None and not validation.valid:
            errors.extend(f"plan validation: {issue.detail}" for issue in validation.issues)

        orch_events = (orch.events if orch else [])
        recovery = {
            "failed_workers": aggregation.get("failed_workers", []),
            "blocked_workers": aggregation.get("blocked_workers", []),
            "timed_out": [e.get("detail") for e in orch_events if e.get("event") == "WORKER_TIMEOUT"],
            "plan_validation_valid": (validation.valid if validation else None),
        }

        plan = plan_result.get("plan")
        plan_summary = {
            "plan_id": plan.plan_id if plan else None,
            "status": plan_status,
            "step_count": len(plan.steps) if plan else 0,
            "complexity": plan.estimated_complexity if plan else None,
        }

        learning = {
            "evidence_confidence": learn_result.get("evidence_confidence") if learn_result else None,
            "experience_id": learn_result.get("experience_id") if learn_result else None,
            "retrieved_experience_count": len(learn_result.get("retrieved_experience") or []) if learn_result else 0,
            "validation_reasons": learn_result.get("validation_reasons", []) if learn_result else [],
        }

        context_dict = {}
        if learn_result and learn_result.get("context") is not None:
            ctx = learn_result["context"]
            context_dict = {
                "task_type": ctx.task_type,
                "relevant_experience_ids": [e["experience_id"] for e in ctx.relevant_experiences],
                "evidence_confidence": ctx.evidence_confidence,
                "truncated": ctx.truncated,
                "previous_failures": len(ctx.previous_failures),
            }

        # --- checkpoint: create (embedding the existing Phase-7 plan checkpoint),
        #     then persist through the existing ProjectMemory mechanism.
        checkpoint = self.checkpoints.create(
            session_id=self.session_id, task_id=task_id, state=state,
            controls=controls,
            plan_checkpoint=plan_result.get("checkpoint"),
            quality_decision=quality_decision,
            evidence={
                "execution_status": orch.status if orch else None,
                "verification_passed": verification.get("passed"),
                "integrity_ok": integrity.get("integrity_ok"),
                "errors": errors[:5],
            },
        )
        persisted, persist_reasons = self.checkpoints.persist(checkpoint)
        if persisted:
            log.emit("CHECKPOINT_PERSISTED", task_id, worker_id=self.session_id, detail=checkpoint.checkpoint_id)
        else:  # session-created checkpoints validate by construction; only reachable on a real defect
            log.emit("CHECKPOINT_REJECTED", task_id, detail="; ".join(persist_reasons))
            errors.extend(f"checkpoint persist rejected: {r}" for r in persist_reasons)

        log.emit(
            "SESSION_COMPLETED" if state == SessionState.COMPLETED.value else "SESSION_FAILED",
            task_id, worker_id=self.session_id,
            success=(state == SessionState.COMPLETED.value),
            detail=f"state={state} quality={quality_decision}",
        )

        self._record_memory(task_id, objective, state, quality_decision, checkpoint)

        return ExecutionSessionResult(
            session_id=self.session_id, task_id=task_id, request=objective, state=state,
            controls=SessionControls(**{
                "permission_policy": controls["permission_policy"],
                "limits": controls["limits"],
                "tool_restrictions": controls["tool_restrictions"],
            }),
            plan_summary=plan_summary, learning=learning, context=context_dict,
            execution_status=(orch.status if orch else None),
            verification=verification, quality_decision=quality_decision, integrity=integrity,
            errors=errors, recovery=recovery,
            checkpoint_id=checkpoint.checkpoint_id,
            events=log.as_dicts(),
            duration_seconds=time.time() - start,
            raw_result=learn_result,
        )

    def _record_memory(self, task_id, objective, state, quality_decision, checkpoint):
        """Structured, non-sensitive session summary - never raw model
        conversations, never credentials. Same ProjectMemory pattern as
        every prior phase."""
        self.project_memory.remember(
            key=f"execution_session:{task_id}",
            value={
                "session_id": self.session_id, "task_id": task_id,
                "state": state, "quality_decision": quality_decision,
                "checkpoint_id": checkpoint.checkpoint_id,
                "objective": objective[:200],
            },
            verified=(state == SessionState.COMPLETED.value),
            source="execution_session",
        )
