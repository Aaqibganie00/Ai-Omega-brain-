"""
ERROR RECOVERY ENGINE
-----------------------
On failure, decides: retry same worker, switch worker/backend, decompose
the task further, or escalate to the user - instead of blindly repeating
the same request.
"""

from dataclasses import dataclass


@dataclass
class RecoveryDecision:
    action: str      # "retry" | "switch_backend" | "decompose" | "escalate"
    reason: str


class ErrorRecoveryEngine:
    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries

    def decide(self, task_id: str, execution_memory, router, worker_type: str) -> RecoveryDecision:
        history = execution_memory.history_for(task_id)
        failure_count = sum(1 for h in history if h["outcome"] == "failure")

        if failure_count == 0:
            return RecoveryDecision("retry", "first failure - safe to retry once")

        if failure_count < self.max_retries:
            alt_candidates = router.candidates_for(worker_type)
            if len(alt_candidates) > 1:
                return RecoveryDecision("switch_backend", "current backend underperforming, alternative available")
            return RecoveryDecision("retry", "no alternative backend, retrying")

        return RecoveryDecision("escalate", f"exceeded {self.max_retries} retries - needs human/replanning input")
