"""
PHASE 9 — CONTEXT MODEL & CONTEXT BUILDER
------------------------------------------------
Context combines the current task with a BOUNDED slice of memory - never
the whole experience database. Every limit below is enforced in code, not
just documented, and tested with 100/1000-experience stores.

Note: assumptions here means store-derived cautionary notes (e.g. "N prior
attempts against this task_type all failed") - not the plan-level
ExecutionPlan.assumptions from Phase 7, which is a separate concept this
context feeds into via prior_knowledge, not vice versa.
"""

from dataclasses import dataclass, field

from .secrets import redact_secrets_deep
from .relevance import MultiSignalRelevanceEngine
from .staleness import check_staleness
from .schemas import KnowledgeStatus

MAX_EXPERIENCES = 5
MAX_STRATEGIES = 3
MAX_FAILURES = 5
MAX_ASSUMPTIONS = 5
MAX_CHARS = 4000


@dataclass
class Context:
    task_id: str
    task_description: str
    task_type: str
    project_context: dict = field(default_factory=dict)
    relevant_files: list = field(default_factory=list)
    current_constraints: list = field(default_factory=list)
    required_capabilities: set = field(default_factory=set)
    required_permissions: set = field(default_factory=set)
    acceptance_criteria: list = field(default_factory=list)
    previous_failures: list = field(default_factory=list)
    relevant_experiences: list = field(default_factory=list)
    relevant_strategies: list = field(default_factory=list)
    known_risks: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    confidence: str = "LOW"
    evidence_confidence: str = "LOW"
    truncated: bool = False


class ContextBuilder:
    def __init__(self, store, relevance_engine=None,
                 max_experiences=MAX_EXPERIENCES, max_strategies=MAX_STRATEGIES,
                 max_failures=MAX_FAILURES, max_assumptions=MAX_ASSUMPTIONS, max_chars=MAX_CHARS):
        self.store = store
        self.relevance_engine = relevance_engine or MultiSignalRelevanceEngine()
        self.max_experiences = max_experiences
        self.max_strategies = max_strategies
        self.max_failures = max_failures
        self.max_assumptions = max_assumptions
        self.max_chars = max_chars

    def build(self, task_id, task_description, task_type,
              required_capabilities=None, relevant_files=None,
              current_constraints=None, acceptance_criteria=None):
        truncated = False

        all_experiences = self.store.all_experiences()
        active = [e for e in all_experiences if check_staleness(e) == KnowledgeStatus.ACTIVE.value
                  and e.get("status") not in (KnowledgeStatus.SUPERSEDED.value, KnowledgeStatus.INVALID.value)]

        ranked = self.relevance_engine.rank(
            task_description, task_type, active, top_n=self.max_experiences,
            required_capabilities=required_capabilities, relevant_files=relevant_files,
        )
        if len(active) > self.max_experiences:
            truncated = True

        by_id = {e["experience_id"]: e for e in active}
        relevant_experiences = []
        for score in ranked:
            exp = by_id.get(score.experience_id)
            if exp is None:
                continue
            relevant_experiences.append(redact_secrets_deep({
                "experience_id": exp["experience_id"], "task_summary": exp.get("task_summary"),
                "evidence_confidence": exp.get("evidence_confidence"), "relevance_score": score.score,
                "relevance_explanation": score.explanation,
            }))

        strategies = []
        for exp in active:
            strategies.extend(exp.get("reusable_patterns", []))
        if len(strategies) > self.max_strategies:
            truncated = True
        strategies = strategies[:self.max_strategies]

        failures = []
        for exp in active:
            for f in exp.get("failures", []):
                failures.append(redact_secrets_deep(f))
        if len(failures) > self.max_failures:
            truncated = True
        failures = failures[:self.max_failures]

        # store-derived cautionary assumptions (e.g. repeated-failure warning)
        assumptions = []
        failure_task_types = {}
        for exp in active:
            if exp.get("failures") and exp.get("task_type"):
                failure_task_types[exp["task_type"]] = failure_task_types.get(exp["task_type"], 0) + 1
        for ttype, count in failure_task_types.items():
            if count >= 2 and ttype == task_type:
                assumptions.append(f"{count} prior attempt(s) on task_type='{ttype}' recorded failures - review before proceeding")
        if len(assumptions) > self.max_assumptions:
            truncated = True
        assumptions = assumptions[:self.max_assumptions]

        evidence_confidences = [e.get("evidence_confidence", "LOW") for e in relevant_experiences]
        rank_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        agg_evidence_confidence = max(evidence_confidences, key=lambda c: rank_order.get(c, 0), default="LOW")

        ctx = Context(
            task_id=task_id, task_description=task_description[:self.max_chars], task_type=task_type,
            relevant_files=list(relevant_files or []), current_constraints=list(current_constraints or []),
            required_capabilities=set(required_capabilities or set()), acceptance_criteria=list(acceptance_criteria or []),
            previous_failures=failures, relevant_experiences=relevant_experiences, relevant_strategies=strategies,
            assumptions=assumptions, evidence_confidence=agg_evidence_confidence, truncated=truncated,
        )

        total_chars = len(str(ctx.relevant_experiences)) + len(str(ctx.previous_failures)) + len(str(ctx.relevant_strategies))
        if total_chars > self.max_chars:
            ctx.relevant_experiences = ctx.relevant_experiences[: max(1, len(ctx.relevant_experiences) // 2)]
            ctx.truncated = True

        return ctx
