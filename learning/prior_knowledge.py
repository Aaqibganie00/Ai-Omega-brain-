"""
PHASE 9 — PRIOR KNOWLEDGE PACK
---------------------------------
The final, compact, advisory-only payload handed to the planner. Built
FROM a bounded Context (learning/context.py) - never re-queries the full
store, so its own limits are inherited automatically.
"""

from dataclasses import dataclass, field


@dataclass
class PriorKnowledgePack:
    task_id: str
    lessons: list = field(default_factory=list)
    strategies: list = field(default_factory=list)
    failure_warnings: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    contradictions: list = field(default_factory=list)
    confidence: str = "LOW"
    evidence_confidence: str = "LOW"
    source_experience_ids: list = field(default_factory=list)


def build_prior_knowledge_pack(context, contradiction_resolutions=None):
    lessons = []
    source_ids = [exp["experience_id"] for exp in context.relevant_experiences]

    failure_warnings = [
        f"Previous attempt failed: {f.get('failure_type', 'unknown')} - {f.get('root_cause', '')[:150]}"
        for f in context.previous_failures
    ]

    contradictions = []
    for res in (contradiction_resolutions or []):
        contradictions.append({
            "status": res.status, "reason": res.reason,
            "experience_id_a": res.experience_id_a, "experience_id_b": res.experience_id_b,
        })

    return PriorKnowledgePack(
        task_id=context.task_id, lessons=lessons, strategies=context.relevant_strategies,
        failure_warnings=failure_warnings, risks=list(context.known_risks),
        contradictions=contradictions, confidence=context.confidence,
        evidence_confidence=context.evidence_confidence, source_experience_ids=source_ids,
    )


def prior_knowledge_pack_to_planner_guidance(pack, experiences_lookup=None):
    experiences_lookup = experiences_lookup or {}
    guidance = []
    for exp_id in pack.source_experience_ids:
        exp = experiences_lookup.get(exp_id, {})
        guidance.append({
            "experience_id": exp_id, "task_summary": exp.get("task_summary", ""),
            "evidence_confidence": exp.get("evidence_confidence", pack.evidence_confidence),
            "relevance_score": exp.get("relevance_score", 0.0),
            "lessons": exp.get("lessons", []),
        })
    if pack.failure_warnings:
        guidance.append({
            "experience_id": "failure-warnings", "task_summary": "; ".join(pack.failure_warnings),
            "evidence_confidence": "LOW", "relevance_score": 0.0, "lessons": [],
        })
    return guidance
