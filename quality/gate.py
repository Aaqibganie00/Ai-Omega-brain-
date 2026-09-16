"""
QUALITY CONTROL LAYER — QUALITY GATE
-------------------------------------------
Final deterministic decision. Never trusts a single signal - checks
integrity, critic severity, regressions, requirement coverage (including
UNKNOWN, which never silently passes), raw test result, and scope, in a
fixed priority order. Only APPROVED may map to the existing COMPLETED
state - the caller (orchestrator) does that mapping, not this class.
"""

from .schemas import QualityGateDecision, RequirementStatus, Severity


class QualityGate:
    def decide(self, requirement_checks, test_result, regression_result, file_audit, scope_result, integrity_result, critic_result):
        if not integrity_result.integrity_ok:
            return QualityGateDecision.BLOCKED, [f"test integrity violation: {s}" for s in integrity_result.suspicious]

        if critic_result.severity == Severity.BLOCKING.value:
            return QualityGateDecision.BLOCKED, [i["issue"] for i in critic_result.issues if i["severity"] == Severity.BLOCKING.value]

        if regression_result is not None and regression_result.regression_detected:
            return QualityGateDecision.REJECTED, [f"regression(s): {regression_result.regressions}"]

        failed_reqs = [rc for rc in requirement_checks if rc.status == RequirementStatus.FAIL.value]
        if failed_reqs:
            return QualityGateDecision.REJECTED, [f"failed requirement: {rc.requirement}" for rc in failed_reqs]

        unknown_reqs = [rc for rc in requirement_checks if rc.status == RequirementStatus.UNKNOWN.value]
        if unknown_reqs:
            return QualityGateDecision.INCOMPLETE, [f"unknown requirement status: {rc.requirement}" for rc in unknown_reqs]

        if not test_result.get("passed"):
            return QualityGateDecision.REJECTED, ["tests are not passing"]

        if scope_result is not None and scope_result.severity == Severity.HIGH.value:
            return QualityGateDecision.REJECTED, [f"out-of-scope changes: {scope_result.out_of_scope_changes}"]

        if not critic_result.approved:
            return QualityGateDecision.REJECTED, [i["issue"] for i in critic_result.issues] or ["critic did not approve"]

        return QualityGateDecision.APPROVED, ["all checks passed"]
