"""
SELF-CORRECTION LAYER — CRITIC
-----------------------------------
Independent review stage. Deliberately has NO tool access - it cannot
write, edit, or execute anything, only read the objective evidence
(test result, files changed) it's handed and render a verdict. This is
what makes it "independent": the executor/fixer's own claim of success
is never sufficient by itself (item 11's rule) - the actual test_result
dict, produced by a real tool execution, is what it grades against.
"""

from .schemas import CriticResult


class Critic:
    def review(self, objective: str, files_changed: list, test_result: dict, attempts_made: int = 0) -> CriticResult:
        issues = []
        evidence = []

        tests_passed = bool(test_result.get("passed"))
        evidence.append(f"test_result.passed={tests_passed}")
        if not tests_passed:
            issues.append("Tests are not passing.")

        # "no files changed" is only suspicious when a REPAIR was actually
        # attempted and produced nothing - a task whose tests already
        # passed from the start (no repair needed) legitimately has an
        # empty files_changed list and should not be penalized for it.
        if attempts_made > 0 and not files_changed:
            issues.append("No files were changed despite repair attempts - implementation may be missing.")
        evidence.append(f"files_changed={files_changed}")

        # Cheap "suspicious change" heuristic: a fix that touches an
        # unusually large number of files for what should be a small,
        # targeted repair is worth flagging for a human, not silently
        # approving.
        if len(files_changed) > 5:
            issues.append(f"Unusually broad change for a single repair: {len(files_changed)} files touched.")

        severity = "blocking" if (not tests_passed or (attempts_made > 0 and not files_changed)) else ("minor" if issues else "none")
        approved = tests_passed and (attempts_made == 0 or bool(files_changed)) and severity != "blocking"

        recommendations = []
        if not approved:
            recommendations.append("Re-run analysis and repair with a narrower, test-driven fix.")

        return CriticResult(
            approved=approved, issues=issues, severity=severity,
            evidence=evidence, recommendations=recommendations,
        )
