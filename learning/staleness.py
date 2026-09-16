"""
LEARNING LAYER — STALENESS & CONTRADICTION DETECTION
-----------------------------------------------------------
Never deletes knowledge - only re-tags status. Old knowledge stays visible
(as history) even after being marked STALE/SUPERSEDED.
"""

import time
from .schemas import KnowledgeStatus, ContradictionResult

STALE_AGE_SECONDS = 60 * 60 * 24 * 90
CONTRADICTION_FAILURE_THRESHOLD = 2


def check_staleness(experience):
    age = time.time() - experience.get("timestamp", time.time())
    if age > STALE_AGE_SECONDS and experience.get("status") == KnowledgeStatus.ACTIVE.value:
        return KnowledgeStatus.STALE.value
    return experience.get("status", KnowledgeStatus.ACTIVE.value)


class ContradictionDetector:
    def check(self, store, task_type):
        experiences = sorted(store.search(task_type=task_type), key=lambda e: e.get("timestamp", 0))
        successes = [e for e in experiences if e.get("evidence_confidence") == "HIGH"]
        if not successes:
            return ContradictionResult(conflicting=False)

        last_success_time = successes[-1].get("timestamp", 0)
        later_failures = [
            e for e in experiences
            if e.get("timestamp", 0) > last_success_time and e.get("quality_gate_result") in ("REJECTED", "BLOCKED")
        ]

        if len(later_failures) >= CONTRADICTION_FAILURE_THRESHOLD:
            return ContradictionResult(
                conflicting=True,
                conflicting_experience_ids=[e["experience_id"] for e in later_failures],
                reason=f"{len(later_failures)} verified failure(s) occurred after a previously HIGH-confidence success for task_type='{task_type}'",
            )
        return ContradictionResult(conflicting=False)

    def mark_superseded(self, store, experience_id, reason):
        entry = store.get(experience_id)
        if entry is None:
            return
        entry["status"] = KnowledgeStatus.SUPERSEDED.value
        store.project_memory.remember(
            key=f"experience:{experience_id}", value=entry,
            verified=(entry.get("evidence_confidence") == "HIGH"), source=entry.get("source", "learning_layer"),
        )
