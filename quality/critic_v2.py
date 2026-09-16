"""
QUALITY CONTROL LAYER — IMPROVED CRITIC
----------------------------------------------
Composes the existing repair.critic.Critic (Phase 4, unmodified) rather
than replacing it - its base issues are folded in, then extended with
requirement coverage, test-integrity, regression, and scope findings, on
the richer 5-level severity scale. No tool access - read-only judgment
only, per item 18.
"""

from repair.critic import Critic as BaseCritic
from .schemas import CriticResultV2, Severity, RequirementStatus

_SEVERITY_ORDER = [Severity.INFO.value, Severity.LOW.value, Severity.MEDIUM.value, Severity.HIGH.value, Severity.BLOCKING.value]


def _max_severity(severities: list) -> str:
    if not severities:
        return Severity.INFO.value
    return max(severities, key=lambda s: _SEVERITY_ORDER.index(s))


class ImprovedCritic:
    def __init__(self, base_critic: BaseCritic = None):
        self.base = base_critic or BaseCritic()

    def review(self, requirements, requirement_checks, test_result, regression_result,
               integrity_result, scope_result, files_changed, attempts_made=0, model_confidence=None) -> CriticResultV2:
        base_result = self.base.review(requirements.objective, files_changed, test_result, attempts_made=attempts_made)

        issues = []
        for msg in base_result.issues:
            issues.append({"issue": msg, "severity": Severity.HIGH.value if base_result.severity == "blocking" else Severity.LOW.value})

        for rc in requirement_checks:
            if rc.status == RequirementStatus.FAIL.value:
                issues.append({"issue": f"Requirement failed: {rc.requirement} ({rc.evidence})", "severity": Severity.HIGH.value})
            elif rc.status == RequirementStatus.UNKNOWN.value:
                issues.append({"issue": f"Requirement status unknown: {rc.requirement}", "severity": Severity.MEDIUM.value})

        if not integrity_result.integrity_ok:
            for s in integrity_result.suspicious:
                issues.append({"issue": s, "severity": Severity.BLOCKING.value})

        if regression_result is not None and regression_result.regression_detected:
            issues.append({"issue": f"Regression(s) introduced: {regression_result.regressions}", "severity": Severity.BLOCKING.value})

        if scope_result is not None and scope_result.out_of_scope_changes:
            issues.append({"issue": f"Out-of-scope file changes: {scope_result.out_of_scope_changes}", "severity": Severity.MEDIUM.value})

        overall_severity = _max_severity([i["severity"] for i in issues])
        approved = base_result.approved and overall_severity not in (Severity.HIGH.value, Severity.BLOCKING.value)

        return CriticResultV2(
            approved=approved, issues=issues, severity=overall_severity,
            evidence=base_result.evidence,
            confidence={"model_confidence": model_confidence, "evidence_confidence": 1.0},
            recommendations=base_result.recommendations,
        )
