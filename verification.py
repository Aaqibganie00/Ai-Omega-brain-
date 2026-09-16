"""
VERIFICATION ENGINE + AI HEART
--------------------------------
VerificationEngine: never trusts worker output just because it exists.
Returns one of CONFIRMED / PARTIALLY_VERIFIED / UNCERTAIN / FAILED.

AIHeart: NOT simulated emotion. A concrete constraints/priorities checker
that runs alongside technical verification. It cannot override a technical
failure result - it only flags risk/policy/priority concerns for the
Omega Decision Core to weigh.
"""

from dataclasses import dataclass


VERDICTS = ("CONFIRMED", "PARTIALLY_VERIFIED", "UNCERTAIN", "FAILED")


@dataclass
class VerificationResult:
    verdict: str
    detail: str


class VerificationEngine:
    def verify_code(self, code: str) -> VerificationResult:
        if not code.strip():
            return VerificationResult("FAILED", "empty output")
        stripped = code.strip().lower()
        if stripped.startswith("<!doctype") or stripped.startswith("<html"):
            # not Python - a real system would run an HTML validator here
            return VerificationResult("PARTIALLY_VERIFIED", "HTML output - syntax not Python-checked")
        try:
            compile(code, "<generated>", "exec")
        except SyntaxError as e:
            return VerificationResult("FAILED", f"syntax error: {e}")
        return VerificationResult("CONFIRMED", "compiles cleanly")

    def verify_text_output(self, output: str) -> VerificationResult:
        """For non-code workers (planning/research/etc) - basic sanity checks
        only. Real cross-source verification would plug in here."""
        if not output or len(output.strip()) < 5:
            return VerificationResult("FAILED", "empty or trivial output")
        if "UNCERTAIN" in output:
            return VerificationResult("UNCERTAIN", "worker self-flagged uncertainty")
        return VerificationResult("PARTIALLY_VERIFIED", "output present, not independently cross-checked")


@dataclass
class HeartConstraints:
    max_retries_per_task: int = 3
    disallow_capabilities: set = None  # e.g. {"unrestricted_shell_exec"}

    def __post_init__(self):
        if self.disallow_capabilities is None:
            self.disallow_capabilities = set()


class AIHeart:
    """
    Decision/value layer. Evaluates a proposed action against constraints,
    priorities, and resource usage - separate from whether it's technically
    correct. Cannot flip a FAILED verification to CONFIRMED.
    """

    def __init__(self, constraints: HeartConstraints):
        self.constraints = constraints

    def evaluate(self, task_worker_type: str, retries_so_far: int) -> dict:
        concerns = []

        if task_worker_type in self.constraints.disallow_capabilities:
            concerns.append(f"capability '{task_worker_type}' is disallowed by policy")

        if retries_so_far >= self.constraints.max_retries_per_task:
            concerns.append("retry budget exceeded - escalate instead of retrying blindly")

        return {
            "approved": len(concerns) == 0,
            "concerns": concerns,
        }
