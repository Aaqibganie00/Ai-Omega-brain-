"""
PHASE 9 — CONTRADICTION RESOLUTION
------------------------------------------
Extends staleness.ContradictionDetector (Phase 8, reused via composition)
with an explicit resolution status per conflicting pair. Both records are
ALWAYS preserved. If evidence cannot establish WHY two experiences
disagree, the resolution is UNKNOWN - never a fabricated guess.
"""

from dataclasses import dataclass

from .staleness import ContradictionDetector

RESOLUTION_STATUSES = {"SUPPORTED", "WEAKENED", "CONTRADICTED", "CONTEXT_DEPENDENT", "UNKNOWN"}


@dataclass
class ContradictionResolution:
    experience_id_a: str
    experience_id_b: str
    status: str
    reason: str
    evidence_a_strength: str
    evidence_b_strength: str


class ContradictionResolver:
    def __init__(self, base_detector=None):
        self.base_detector = base_detector or ContradictionDetector()

    def resolve(self, store, task_type):
        base_result = self.base_detector.check(store, task_type)
        if not base_result.conflicting:
            return []

        experiences = {e["experience_id"]: e for e in store.search(task_type=task_type)}
        successes = [e for e in experiences.values() if e.get("evidence_confidence") == "HIGH"]
        resolutions = []

        for failure_id in base_result.conflicting_experience_ids:
            failure_exp = experiences.get(failure_id)
            if failure_exp is None:
                continue
            success_exp = max(successes, key=lambda e: e.get("timestamp", 0), default=None)
            if success_exp is None:
                resolutions.append(ContradictionResolution(
                    experience_id_a=failure_id, experience_id_b="none",
                    status="UNKNOWN", reason="no prior success record available to compare against",
                    evidence_a_strength=failure_exp.get("evidence_confidence", "LOW"), evidence_b_strength="N/A",
                ))
                continue

            resolutions.append(self._resolve_pair(success_exp, failure_exp))

        return resolutions

    def _resolve_pair(self, success_exp, failure_exp):
        success_files = set(success_exp.get("files_changed", []))
        failure_files = set(failure_exp.get("files_changed", []))
        same_context = bool(success_files & failure_files) or success_exp.get("task_id") == failure_exp.get("task_id")

        more_recent_is_failure = failure_exp.get("timestamp", 0) > success_exp.get("timestamp", 0)

        if not same_context and success_files and failure_files:
            status = "CONTEXT_DEPENDENT"
            reason = "success and failure touched different files - may reflect different conditions rather than a genuine reversal"
        elif more_recent_is_failure and same_context:
            status = "CONTRADICTED"
            reason = "a more recent, same-context failure directly conflicts with the earlier verified success"
        elif more_recent_is_failure and not (success_files or failure_files):
            status = "UNKNOWN"
            reason = "insufficient evidence (no file-level context available) to determine why outcomes differ"
        elif not more_recent_is_failure:
            status = "SUPPORTED"
            reason = "the success record is more recent than the conflicting failure - current evidence still favors success"
        else:
            status = "WEAKENED"
            reason = "a conflicting failure exists but context/recency evidence does not clearly override the prior success"

        return ContradictionResolution(
            experience_id_a=success_exp["experience_id"], experience_id_b=failure_exp["experience_id"],
            status=status, reason=reason,
            evidence_a_strength=success_exp.get("evidence_confidence", "LOW"),
            evidence_b_strength=failure_exp.get("evidence_confidence", "LOW"),
        )
