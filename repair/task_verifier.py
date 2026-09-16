"""
SELF-CORRECTION LAYER — TASK VERIFIER
-------------------------------------------
Separate from repair.critic.Critic (which judges the *quality* of the
change) and separate from verification.VerificationEngine (Phase 1's
code-compiles check). This checks objective, file-system-level evidence
that the task's stated requirements are actually met - required files
exist, and the real test result (not a model's claim) passed.

Every file check goes through the registry's own permission/audit
pipeline (ToolCall -> execute()) - never registry.impl.* directly, so
the existence check respects permission config and is audit-logged.
"""

from tools import ToolCall
from .schemas import TaskVerificationResult


class TaskVerifier:
    def verify(self, tool_registry, required_files: list, test_result: dict) -> TaskVerificationResult:
        checks = {}

        for path in required_files or []:
            result = tool_registry.execute(ToolCall(
                tool_name="read_file", arguments={"path": path},
                requested_by="repair.task_verifier",
            ))
            checks[f"file_exists:{path}"] = result.ok

        checks["tests_passed"] = bool(test_result.get("passed"))

        passed = all(checks.values()) if checks else bool(test_result.get("passed"))
        missing = [k for k, v in checks.items() if not v]
        detail = "All checks passed." if passed else f"Failed checks: {', '.join(missing)}"

        return TaskVerificationResult(passed=passed, checks=checks, detail=detail)
