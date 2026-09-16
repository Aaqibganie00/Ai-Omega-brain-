"""
LEARNING LAYER — KNOWLEDGE RETRIEVER
------------------------------------------
For a new task: retrieve -> rank -> filter stale/invalid -> return concise
structured guidance. Never returns the entire experience store - only the
top_n most relevant, non-stale entries with their lessons.
"""

from .relevance import ExperienceRelevanceEngine
from .staleness import check_staleness
from .schemas import KnowledgeStatus


class KnowledgeRetriever:
    def __init__(self, store, relevance_engine=None):
        self.store = store
        self.relevance_engine = relevance_engine or ExperienceRelevanceEngine()

    def retrieve(self, task_description, task_type, top_n=3, min_confidence=None):
        candidates = self.store.all_experiences()

        active = []
        for exp in candidates:
            live_status = check_staleness(exp)
            if live_status != KnowledgeStatus.ACTIVE.value:
                continue
            if exp.get("status") in (KnowledgeStatus.INVALID.value, KnowledgeStatus.SUPERSEDED.value):
                continue
            active.append(exp)

        if min_confidence:
            rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
            active = [e for e in active if rank.get(e.get("evidence_confidence", "LOW"), 0) >= rank.get(min_confidence, 0)]

        ranked = self.relevance_engine.rank(task_description, task_type, active, top_n=top_n)
        by_id = {e["experience_id"]: e for e in active}

        guidance = []
        for score in ranked:
            exp = by_id.get(score.experience_id)
            if exp is None:
                continue
            guidance.append({
                "experience_id": exp["experience_id"],
                "task_summary": exp.get("task_summary"),
                "outcome": exp.get("quality_gate_result"),
                "evidence_confidence": exp.get("evidence_confidence"),
                "lessons": exp.get("lessons", []),
                "relevance_score": score.score,
                "relevance_explanation": score.explanation,
            })
        return guidance
