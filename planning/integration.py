"""
PLANNING LAYER — ORCHESTRATOR & QUALITY GATE INTEGRATION
------------------------------------------------------------------
    objective -> AdvancedPlanner -> PlanValidator -> PlanOptimizer
        -> (converted to SubTasks) -> orchestration.Orchestrator (Phase 6, reused)
        -> OrchestrationResult -> plan.acceptance_criteria fed into
        quality.QualityGate (Phase 5, reused) as RequirementChecks
        -> final decision

An invalid plan never reaches the orchestrator - execution is refused
before any worker runs.
"""

import uuid
from orchestration import Orchestrator
from orchestration.schemas import SubTask
from agent.events import AgentEventLog
from quality.gate import QualityGate
from quality.schemas import RequirementCheck, RequirementStatus, CriticResultV2, Severity
from quality.test_integrity import TestIntegrityResult

from .planner import AdvancedPlanner
from .validator import PlanValidator
from .optimizer import PlanOptimizer
from .checkpoint import CheckpointManager
from .revision import PlanReviser


class PlannedOrchestrator:
    def __init__(self, provider, tool_registry, project_memory=None, limits=None,
                 max_revisions=1):
        self.provider = provider
        self.tool_registry = tool_registry
        self.project_memory = project_memory
        self.planner = AdvancedPlanner()
        self.validator = PlanValidator()
        self.optimizer = PlanOptimizer()
        self.checkpoints = CheckpointManager()
        self.orchestrator = Orchestrator(provider=provider, tool_registry=tool_registry, project_memory=None, limits=limits)
        # P0-4: bounded top-level iteration through the EXISTING PlanReviser.
        # Default = exactly one revision per run (MVP). 0 disables.
        self.reviser = PlanReviser()
        self.max_revisions = max_revisions

    def run(self, objective, task_id=None, prior_knowledge=None):
        """prior_knowledge (optional): advisory-only guidance from the
        learning layer (Phase 8) - forwarded unchanged to AdvancedPlanner,
        which only ever appends it as informational plan.assumptions text.
        It is never read by PlanValidator, never touches ToolRegistry
        permissions, and never influences the Quality Gate decision."""
        task_id = task_id or uuid.uuid4().hex[:12]
        log = AgentEventLog()

        plan = self.planner.create_plan(objective, task_id=task_id, project_memory=self.project_memory, prior_knowledge=prior_knowledge)
        log.emit("PLAN_CREATED", task_id, detail=f"{len(plan.steps)} steps, complexity={plan.estimated_complexity}")

        if plan.ambiguity.ambiguous:
            log.emit("PLAN_AMBIGUITY_DETECTED", task_id, detail="; ".join(plan.ambiguity.missing_information))

        for risk in plan.risks:
            log.emit("PLAN_RISK_DETECTED", task_id, detail=f"{risk.category} ({risk.severity}): {risk.description}")

        log.emit("PLAN_VALIDATION_STARTED", task_id)
        validation = self.validator.validate(plan)
        if not validation.valid:
            log.emit("PLAN_REJECTED", task_id, detail="; ".join(i.detail for i in validation.issues))
            self._record_memory(plan, "REJECTED", log)
            return {
                "task_id": task_id, "status": "REJECTED", "plan": plan, "validation": validation,
                "orchestration_result": None, "events": log.as_dicts(),
            }
        log.emit("PLAN_VALIDATED", task_id)

        log.emit("PLAN_OPTIMIZATION_STARTED", task_id)
        optimized_plan = self.optimizer.optimize(plan)
        log.emit("PLAN_OPTIMIZED", task_id, detail=f"parallel_groups={len(getattr(optimized_plan, 'parallel_groups', []))}")

        # P0-4: bounded top-level iteration. A REJECTED (never BLOCKED - a
        # security/integrity block must not trigger retries) final gate feeds
        # the existing PlanReviser exactly once by default; the revised plan
        # is re-validated before it executes. No infinite loops: at most
        # self.max_revisions additional orchestration passes happen.
        revision_attempts = 0
        plan_diff = None
        current_plan = optimized_plan

        while True:
            subtasks = [
                SubTask(subtask_id=s.step_id, parent_task_id=task_id, description=s.description,
                        required_capabilities=set(s.capabilities), dependencies=list(s.dependencies), priority=s.priority)
                for s in current_plan.steps
            ]

            orchestration_result = self.orchestrator.run(objective, task_id=task_id, subtasks_override=subtasks)

            completed_steps = [sid for sid, r in orchestration_result.worker_results.items() if r.status == "COMPLETED"]
            # LIMIT_REACHED (e.g. a Phase-10 worker timeout) is a TERMINAL
            # failure state for a step, not a "remaining" one - the checkpoint
            # must record it as failed so restored state is accurate.
            failed_steps = [sid for sid, r in orchestration_result.worker_results.items() if r.status in ("FAILED", "LIMIT_REACHED")]
            checkpoint = self.checkpoints.create(current_plan, completed_steps, failed_steps,
                                                  evidence={"aggregation": orchestration_result.aggregation})
            log.emit("CHECKPOINT_CREATED", task_id, detail=checkpoint.checkpoint_id)

            for milestone in self.checkpoints.milestones_completed(current_plan, completed_steps):
                log.emit("MILESTONE_COMPLETED", task_id, detail=milestone.name)

            requirement_checks = self._acceptance_criteria_to_requirement_checks(
                current_plan.acceptance_criteria, orchestration_result,
            )
            # Phase 10 (item 6A/6B): feed the gate REAL available evidence from
            # the orchestration layer instead of unconditional None/stubs. The
            # TaskVerifier outcome computed during orchestration becomes a real
            # requirement check here, and the orchestration-level integrity
            # result replaces the previous always-ok stub. regression/scope/
            # file_audit remain None ONLY because the orchestration path takes
            # no per-test baseline / file-audit snapshot - the gate deliberately
            # skips those rules when not measured (documented, not stubbed).
            task_verification = (orchestration_result.aggregation or {}).get("task_verification") or {}
            if task_verification:
                requirement_checks.append(RequirementCheck(
                    requirement="task verification passed",
                    status=RequirementStatus.PASS.value if task_verification.get("passed") else RequirementStatus.FAIL.value,
                    evidence=task_verification.get("detail", ""),
                    severity=Severity.HIGH.value,
                ))
            # P0-1: build/run evidence enters the final gate as a real
            # requirement check (latest attempt decides; full trail preserved
            # in aggregation["runs"]).
            runs_evidence = (orchestration_result.aggregation or {}).get("runs") or []
            if runs_evidence:
                last_run = runs_evidence[-1]
                requirement_checks.append(RequirementCheck(
                    requirement="build/run: artifact executes successfully",
                    status=RequirementStatus.PASS.value if last_run.get("ok") else RequirementStatus.FAIL.value,
                    evidence=(
                        f"command={last_run.get('command')} returncode={last_run.get('returncode')} "
                        f"error={last_run.get('error')} stderr={(last_run.get('stderr') or '')[:300]}"
                    ),
                    severity=Severity.HIGH.value,
                ))
            integrity_evidence = (orchestration_result.aggregation or {}).get("test_integrity") or {}
            integrity_result = TestIntegrityResult(
                integrity_ok=integrity_evidence.get("integrity_ok", True),
                suspicious=integrity_evidence.get("suspicious", []),
            )
            base_critic_approved = orchestration_result.quality_decision == "APPROVED"
            critic_v2 = CriticResultV2(
                approved=base_critic_approved,
                severity=Severity.INFO.value if base_critic_approved else Severity.HIGH.value,
                issues=[] if base_critic_approved else [{"issue": "orchestration-level quality gate did not approve", "severity": Severity.HIGH.value}],
            )
            final_test_result = orchestration_result.aggregation.get("tests", [{}])[-1] if orchestration_result.aggregation.get("tests") else {"passed": False}
            final_decision, reasons = QualityGate().decide(
                requirement_checks=requirement_checks, test_result=final_test_result, regression_result=None,
                file_audit=None, scope_result=None, integrity_result=integrity_result, critic_result=critic_v2,
            )

            overall_status = "COMPLETED" if final_decision.value == "APPROVED" else "FAILED"

            # P0-4: stop on success, on budget exhaustion, or on any decision
            # other than REJECTED. A BLOCKED (integrity/security) verdict must
            # never be retried via plan revision.
            if overall_status == "COMPLETED" or revision_attempts >= self.max_revisions or final_decision.value != "REJECTED":
                break

            revision_attempts += 1
            revision_reason = reasons[0] if reasons else "quality gate rejected the plan"
            evidence = {
                "failed_step": failed_steps[0] if failed_steps else None,
                "test_result": final_test_result,
                "quality_decision": final_decision.value,
                "reasons": list(reasons)[:5],
            }
            log.emit("PLAN_REVISION_STARTED", task_id,
                     detail=f"attempt={revision_attempts}/{self.max_revisions}: {revision_reason[:200]}")
            revised_plan, plan_diff = self.reviser.revise(current_plan, evidence, reason=revision_reason)
            log.emit("PLAN_REVISION_CREATED", task_id,
                     detail=f"version={revised_plan.version} steps={len(revised_plan.steps)} (was {len(current_plan.steps)})")

            revision_validation = self.validator.validate(revised_plan)
            if not revision_validation.valid:
                # A revision is never trusted more than an original plan.
                log.emit("PLAN_REVISION_REJECTED", task_id,
                         detail="; ".join(i.detail for i in revision_validation.issues)[:300])
                break

            log.emit("PLAN_REVISION_VALIDATED", task_id, detail=f"version={revised_plan.version}")
            current_plan = revised_plan

        plan.status = overall_status
        current_plan.status = overall_status

        self._record_memory(current_plan, overall_status, log, orchestration_result)

        return {
            "task_id": task_id, "status": overall_status, "plan": current_plan,
            "validation": validation, "orchestration_result": orchestration_result,
            "checkpoint": checkpoint, "quality_decision": final_decision.value,
            "requirement_checks": requirement_checks, "events": log.as_dicts(),
            "plan_revision": {"attempts": revision_attempts, "max_revisions": self.max_revisions,
                              "plan_version": current_plan.version, "diff": plan_diff},
        }

    def _acceptance_criteria_to_requirement_checks(self, acceptance_criteria, orchestration_result):
        checks = []
        for criterion in acceptance_criteria:
            if "test" in criterion.lower():
                tests = orchestration_result.aggregation.get("tests", [])
                passed = tests[-1].get("passed") if tests else None
                status = RequirementStatus.PASS.value if passed else (RequirementStatus.FAIL.value if tests else RequirementStatus.UNKNOWN.value)
                checks.append(RequirementCheck(requirement=criterion, status=status, evidence=f"tests={tests[-1] if tests else 'none run'}"))
            elif "files" in criterion.lower() or "changed" in criterion.lower():
                files = orchestration_result.aggregation.get("files_changed", [])
                status = RequirementStatus.PASS.value if files else RequirementStatus.FAIL.value
                checks.append(RequirementCheck(requirement=criterion, status=status, evidence=f"files_changed={files}"))
            elif "steps complete" in criterion.lower():
                failed = orchestration_result.aggregation.get("failed_workers", [])
                recovered = set(orchestration_result.aggregation.get("recovered_failures", []))
                unresolved = [f for f in failed if f not in recovered]
                status = RequirementStatus.PASS.value if not unresolved else RequirementStatus.FAIL.value
                checks.append(RequirementCheck(
                    requirement=criterion, status=status,
                    evidence=f"failed_workers={failed} recovered={sorted(recovered)}"))
            elif "executes" in criterion.lower() or "build" in criterion.lower():
                runs = orchestration_result.aggregation.get("runs", [])
                last_run = runs[-1] if runs else {}
                ok = last_run.get("ok") if runs else None
                status = RequirementStatus.PASS.value if ok else (RequirementStatus.FAIL.value if runs else RequirementStatus.UNKNOWN.value)
                checks.append(RequirementCheck(requirement=criterion, status=status,
                                               evidence=f"last_run_command={last_run.get('command')} returncode={last_run.get('returncode')} attempts={len(runs)}"))
            else:
                checks.append(RequirementCheck(requirement=criterion, status=RequirementStatus.UNKNOWN.value,
                                                evidence="no automated check available for this criterion"))
        return checks

    def _record_memory(self, plan, outcome, log, orchestration_result=None):
        if self.project_memory is None:
            return
        summary = {
            "plan_id": plan.plan_id, "task_id": plan.task_id, "version": plan.version,
            "assumptions": plan.assumptions, "requirements": plan.requirements,
            "outcome": outcome, "complexity": plan.estimated_complexity,
            "risks": [{"category": r.category, "severity": r.severity} for r in plan.risks],
        }
        self.project_memory.remember(
            key=f"plan:{plan.task_id}:v{plan.version}", value=summary,
            verified=(outcome == "COMPLETED"), source="planner",
        )
