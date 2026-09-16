"""
LEARNING LAYER — FAILURE MEMORY
-----------------------------------
Answers "have we seen this failure before?" via deterministic keyword
matching (heuristic, same caveat as relevance.py). Existing Phase 4
repair/loop-detection limits remain authoritative - this never creates
its own retry behavior, only surfaces history.
"""

from .relevance import _keywords


class FailureMemory:
    def __init__(self, store):
        self.store = store

    def find_similar(self, failure_type, root_cause, top_n=3):
        target_kw = _keywords(f"{failure_type} {root_cause}")
        matches = []
        for exp in self.store.all_experiences():
            for failure in exp.get("failures", []):
                fail_kw = _keywords(f"{failure.get('failure_type', '')} {failure.get('root_cause', '')}")
                overlap = target_kw & fail_kw
                if overlap:
                    matches.append({
                        "experience_id": exp["experience_id"], "failure": failure,
                        "eventually_succeeded": exp.get("quality_gate_result") == "APPROVED",
                        "shared_keywords": sorted(overlap),
                    })
        matches.sort(key=lambda m: len(m["shared_keywords"]), reverse=True)
        return matches[:top_n]

    def warning_for(self, failure_type, root_cause, top_n=3):
        """PHASE 9: a concise, ADVISORY warning string built only from
        matched history - never a directive. Never blocks execution on its
        own; only an existing safety component (PlanValidator, permissions,
        Quality Gate, TestIntegrityChecker) may actually block anything."""
        matches = self.find_similar(failure_type, root_cause, top_n=top_n)
        if not matches:
            return None
        never_succeeded = all(not m["eventually_succeeded"] for m in matches)
        qualifier = "never succeeded in prior attempts" if never_succeeded else "had mixed prior outcomes"
        return (f"Previous attempts using a similar approach ({qualifier}) matched keywords "
                f"{sorted({kw for m in matches for kw in m['shared_keywords']})} - advisory only, still independently verified.")
