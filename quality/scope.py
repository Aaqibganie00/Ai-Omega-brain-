"""
QUALITY CONTROL LAYER — SCOPE VERIFICATION
------------------------------------------------
Classifies changed files as in-scope (matches the task's expected files)
or out-of-scope (unexpected). Does not automatically reject out-of-scope
changes - just classifies and severities them (item 7's instruction);
the Quality Gate decides what to do with that.
"""

from .schemas import ScopeResult, Severity


class ScopeChecker:
    def check(self, changed_files: list, expected_scope: set) -> ScopeResult:
        in_scope = sorted(f for f in changed_files if f in expected_scope)
        out_of_scope = sorted(f for f in changed_files if f not in expected_scope)
        severity = Severity.HIGH.value if out_of_scope else Severity.INFO.value
        return ScopeResult(in_scope_changes=in_scope, out_of_scope_changes=out_of_scope, severity=severity)
