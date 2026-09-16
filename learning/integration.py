"""
LEARNING LAYER — PLANNER INTEGRATION
-----------------------------------------
    objective -> KnowledgeRetriever (advisory only, informational)
        -> planning.PlannedOrchestrator.run() (Phase 7, COMPLETELY UNMODIFIED)
        -> real evidence extracted from the result
        -> ExperienceValidator (evidence-based, never trusts model claims)
        -> LessonExtractor / StrategyExtractor (only for HIGH-confidence)
        -> ExperienceStore.save()

Retrieved experience is surfaced in the return value for a caller to
consider - it is never wired into PlannedOrchestrator's internals, so it
CANNOT bypass validation, permissions, or the Quality Gate.
"""

import uuid
from agent.events import AgentEventLog
from planning import PlannedOrchestrator

from .schemas import ExperienceRecord, KnowledgeStatus
from .store import ExperienceStore
from .validator import ExperienceValidator
from .lesson_extractor import LessonExtractor
from .strategy_extractor import StrategyExtractor
from .retriever import KnowledgeRetriever
from .staleness import ContradictionDetector, check_staleness
from .context import ContextBuilder
from .contradiction import ContradictionResolver
from .prior_knowledge import build_prior_knowledge_pack, prior_knowledge_pack_to_planner_guidance


def _classify_task_type(objective):
    lowered = objective.lower()
    if "calculator" in lowered:
        return "calculator"
    if "research" in lowered:
        return "research"
    if "test" in lowered or "fix" in lowered or "bug" in lowered or "repair" in lowered:
        return "repair"
    return "general"


class LearningEnabledOrchestrator:
    def __init__(self, provider, tool_registry, project_memory, limits=None):
        if project_memory is None:
            raise ValueError("LearningEnabledOrchestrator requires a ProjectMemory instance.")
        self.provider = provider
        self.tool_registry = tool_registry
        self.project_memory = project_memory
        self.planned_orchestrator = PlannedOrchestrator(provider=provider, tool_registry=tool_registry,
                                                          project_memory=project_memory, limits=limits)
        self.store = ExperienceStore(project_memory)
        self.validator = ExperienceValidator()
        self.lesson_extractor = LessonExtractor()
        self.strategy_extractor = StrategyExtractor(self.validator)
        self.retriever = KnowledgeRetriever(self.store)
        self.contradiction_detector = ContradictionDetector()
        self.context_builder = ContextBuilder(self.store)
        self.contradiction_resolver = ContradictionResolver(self.contradiction_detector)

    def run(self, objective, task_id=None, relevant_files=None, required_capabilities=None):
        task_id = task_id or uuid.uuid4().hex[:12]
        log = AgentEventLog()
        task_type = _classify_task_type(objective)

        log.emit("CONTEXT_BUILD_STARTED", task_id)
        context = self.context_builder.build(
            task_id=task_id, task_description=objective, task_type=task_type,
            required_capabilities=required_capabilities, relevant_files=relevant_files,
        )
        log.emit("CONTEXT_RETRIEVED", task_id, detail=f"{len(context.relevant_experiences)} experience(s), truncated={context.truncated}")
        if context.truncated:
            log.emit("CONTEXT_LIMIT_APPLIED", task_id, detail="one or more bounds (experiences/strategies/failures/chars) were hit")

        for exp in context.relevant_experiences:
            log.emit("EXPERIENCE_SELECTED", task_id, detail=f"{exp['experience_id']} score={exp['relevance_score']}")
        for strat in context.relevant_strategies:
            log.emit("STRATEGY_SELECTED", task_id, detail=strat.get("strategy_id"))
        for warning in context.assumptions:
            log.emit("FAILURE_WARNING", task_id, detail=warning)

        stale_count = len([e for e in self.store.all_experiences() if check_staleness(e) == "STALE"])
        if stale_count:
            log.emit("STALE_KNOWLEDGE_DETECTED", task_id, detail=f"{stale_count} stale experience(s) excluded from context")

        contradiction_resolutions = self.contradiction_resolver.resolve(self.store, task_type)
        for res in contradiction_resolutions:
            log.emit("CONTRADICTION_DETECTED", task_id, detail=f"{res.status}: {res.reason}")

        pack = build_prior_knowledge_pack(context, contradiction_resolutions)
        experiences_lookup = {e["experience_id"]: e for e in self.store.all_experiences()}
        retrieved = prior_knowledge_pack_to_planner_guidance(pack, experiences_lookup)
        log.emit("PRIOR_KNOWLEDGE_BUILT", task_id, detail=f"{len(pack.source_experience_ids)} source experience(s), evidence_confidence={pack.evidence_confidence}")

        # advisory only - see PlannedOrchestrator.run()'s docstring for the
        # exact guarantee that this cannot bypass validation/permissions/gate
        plan_result = self.planned_orchestrator.run(objective, task_id=task_id, prior_knowledge=retrieved)

        record, reasons = self._build_and_validate_experience(task_id, task_type, objective, plan_result, log)
        self.store.save(record)
        log.emit("EXPERIENCE_CREATED", task_id, detail=record.experience_id, success=(record.evidence_confidence == "HIGH"))
        log.emit("LEARNING_UPDATED", task_id, detail=f"experience_id={record.experience_id} status={record.status}")

        contradiction = self.contradiction_detector.check(self.store, task_type)
        if contradiction.conflicting:
            log.emit("KNOWLEDGE_CONFLICT", task_id, detail=contradiction.reason)

        return {
            "task_id": task_id, "experience_id": record.experience_id,
            "evidence_confidence": record.evidence_confidence, "validation_reasons": reasons,
            "context": context, "prior_knowledge_pack": pack, "contradiction_resolutions": contradiction_resolutions,
            "retrieved_experience": retrieved, "plan_result": plan_result,
            "contradiction": contradiction, "events": log.as_dicts(),
        }

    def _build_and_validate_experience(self, task_id, task_type, objective, plan_result, log):
        orchestration_result = plan_result.get("orchestration_result")
        aggregation = orchestration_result.aggregation if orchestration_result else {}
        tests = aggregation.get("tests", [])
        final_test = tests[-1] if tests else {}

        model_claimed_success = plan_result.get("status") == "COMPLETED"

        # Phase 10 (item 6C): integrity_ok now carries the REAL
        # orchestration-level test-integrity evidence exposed by
        # Orchestrator.aggregation["test_integrity"] instead of a blind
        # True. When the evidence is absent (older callers / no orchestration
        # result) it stays None - ExperienceValidator only acts on `is False`,
        # so behavior for unavailable evidence is unchanged, while a real
        # integrity violation now correctly blocks HIGH confidence.
        evidence = {
            "tests_passed": final_test.get("passed") if tests else None,
            "quality_gate_result": plan_result.get("quality_decision"),
            "required_files_exist": bool(aggregation.get("files_changed")) if aggregation else None,
            "integrity_ok": ((aggregation.get("test_integrity") or {}).get("integrity_ok") if aggregation else None),
            "permission_denied_occurred": any(
                "permission" in str(e).lower() for e in aggregation.get("errors", [])
            ) if aggregation else False,
        }

        evidence_confidence, reasons = self.validator.validate(evidence)
        log.emit("EXPERIENCE_VALIDATED" if evidence_confidence == "HIGH" else "LEARNING_SKIPPED",
                  task_id, detail="; ".join(reasons))

        failures = []
        if orchestration_result:
            for sid in aggregation.get("failed_workers", []):
                failures.append({"failure_type": "worker_failure", "root_cause": f"subtask {sid} failed",
                                  "affected_files": aggregation.get("files_changed", [])})

        record = ExperienceRecord(
            experience_id=ExperienceRecord.new_id(), task_id=task_id, task_type=task_type,
            task_summary=objective[:200], plan_summary=f"{len(plan_result['plan'].steps) if plan_result.get('plan') else 0} steps",
            workers_used=sorted({r.worker_id for r in (orchestration_result.worker_results.values() if orchestration_result else [])}),
            tools_used=sorted({t for r in (orchestration_result.worker_results.values() if orchestration_result else []) for t in r.tools_used}),
            files_changed=aggregation.get("files_changed", []),
            execution_result=orchestration_result.status if orchestration_result else "UNKNOWN",
            verification_result=(plan_result.get("quality_decision") == "APPROVED"),
            quality_gate_result=plan_result.get("quality_decision"),
            failures=failures, successful_actions=aggregation.get("successful_workers", []),
            failed_actions=aggregation.get("failed_workers", []),
            model_claimed_success=model_claimed_success, evidence_confidence=evidence_confidence,
            evidence=evidence, status=KnowledgeStatus.ACTIVE.value,
        )

        record.lessons = [l.__dict__ for l in self.lesson_extractor.extract(record)]
        if record.lessons:
            log.emit("LESSON_EXTRACTED", task_id, detail=f"{len(record.lessons)} lesson(s)")

        strategy = self.strategy_extractor.extract(record)
        if strategy is not None:
            record.reusable_patterns = [strategy.__dict__]
            log.emit("STRATEGY_EXTRACTED", task_id, detail=strategy.strategy_id)

        return record, reasons
