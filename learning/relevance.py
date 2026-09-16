"""
LEARNING LAYER — RELEVANCE ENGINE
------------------------------------------
Deterministic keyword-overlap scoring, explicitly labeled as a heuristic
(RelevanceScore.method = "keyword_overlap_heuristic") - this is NOT
semantic similarity.
"""

import re
import time
from .schemas import RelevanceScore

_STOPWORDS = {"a", "an", "the", "and", "or", "with", "for", "to", "of", "in", "on", "that", "this"}


def _keywords(text):
    words = re.findall(r'[a-z0-9]+', text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


class ExperienceRelevanceEngine:
    def rank(self, task_description, task_type, experiences, top_n=5):
        task_kw = _keywords(task_description)
        now = time.time()
        scored = []

        for exp in experiences:
            exp_kw = _keywords(exp.get("task_summary", ""))
            shared = task_kw & exp_kw
            keyword_score = len(shared) / max(len(task_kw | exp_kw), 1)

            same_type_bonus = 0.3 if exp.get("task_type") == task_type else 0.0

            age_seconds = max(now - exp.get("timestamp", now), 0)
            recency_bonus = max(0.0, 0.2 - (age_seconds / (60 * 60 * 24 * 30)) * 0.2)

            confidence_weight = {"HIGH": 0.3, "MEDIUM": 0.15, "LOW": 0.0}.get(exp.get("evidence_confidence", "LOW"), 0.0)

            total = keyword_score + same_type_bonus + recency_bonus + confidence_weight

            scored.append(RelevanceScore(
                experience_id=exp.get("experience_id", "unknown"), score=round(total, 4),
                explanation={
                    "shared_keywords": sorted(shared), "same_task_type": exp.get("task_type") == task_type,
                    "keyword_score": round(keyword_score, 4), "recency_bonus": round(recency_bonus, 4),
                    "confidence_weight": confidence_weight,
                },
            ))

        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_n]


class MultiSignalRelevanceEngine(ExperienceRelevanceEngine):
    """PHASE 9: adds file overlap, capability overlap (via a workers_used ->
    capability heuristic, since ExperienceRecord doesn't store capabilities
    directly), success/failure signal, and a contradiction penalty on top
    of the Phase 8 base signals. Still explicitly a deterministic HEURISTIC
    (method stays "keyword_overlap_heuristic" via the base RelevanceScore
    unless overridden below) - not semantic similarity.

    Honest limitation: dependency-overlap and acceptance-criteria-overlap
    signals from the spec are NOT implemented here - ExperienceRecord does
    not currently store either at a granularity this engine could compare
    against, and fabricating a proxy signal for them would violate the
    "never fabricate" instruction. Both are documented, not silently
    skipped."""

    _WORKER_TO_CAPABILITY = {
        "coding_worker": "coding", "testing_worker": "testing", "debugging_worker": "debugging",
        "review_worker": "review", "verification_worker": "verification",
        "planner_worker": "planning", "research_worker": "research",
    }

    def rank(self, task_description, task_type, experiences, top_n=5,
             required_capabilities=None, relevant_files=None):
        base_scores = {s.experience_id: s for s in super().rank(task_description, task_type, experiences, top_n=len(experiences) or 1)}
        by_id = {e.get("experience_id", "unknown"): e for e in experiences}
        required_capabilities = set(required_capabilities or [])
        relevant_files = set(relevant_files or [])

        rescored = []
        for exp_id, base in base_scores.items():
            exp = by_id.get(exp_id, {})

            exp_files = set(exp.get("files_changed", []))
            file_overlap = len(relevant_files & exp_files) / max(len(relevant_files | exp_files), 1) if (relevant_files or exp_files) else 0.0

            exp_capabilities = {self._WORKER_TO_CAPABILITY.get(w) for w in exp.get("workers_used", [])} - {None}
            capability_overlap = len(required_capabilities & exp_capabilities) / max(len(required_capabilities | exp_capabilities), 1) if (required_capabilities or exp_capabilities) else 0.0

            was_success = exp.get("quality_gate_result") == "APPROVED"
            success_signal = 0.15 if was_success else -0.1  # a known-successful experience is worth surfacing more; a known failure is still surfaced (as a warning) but ranked lower as a "strategy to copy"

            contradiction_penalty = -0.3 if exp.get("status") == "CONTRADICTED" else 0.0

            total = base.score + (0.2 * file_overlap) + (0.2 * capability_overlap) + success_signal + contradiction_penalty

            explanation = dict(base.explanation)
            explanation.update({
                "file_overlap": round(file_overlap, 4), "capability_overlap": round(capability_overlap, 4),
                "success_signal": success_signal, "contradiction_penalty": contradiction_penalty,
                "dependency_overlap": "not_implemented - no per-experience dependency data available",
                "acceptance_criteria_overlap": "not_implemented - no per-experience acceptance-criteria data available",
            })

            rescored.append(RelevanceScore(experience_id=exp_id, score=round(total, 4), explanation=explanation))

        rescored.sort(key=lambda s: s.score, reverse=True)
        return rescored[:top_n]
