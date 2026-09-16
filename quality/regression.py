"""
QUALITY CONTROL LAYER — REGRESSION VERIFICATION
-------------------------------------------------------
Parses real unittest -v output (the default run_tests format already
includes -v — see tools/implementations.py) into a per-test pass/fail map,
then compares baseline vs final to find tests that regressed: passed
before, fail now. A brand-new failing test is not a regression (it never
passed); a test that now passes fine is fine either way.
"""

import re
from .schemas import RegressionResult

_VERBOSE_LINE_RE = re.compile(r'^(\S+) \(([\w.]+)\) \.\.\. (ok|FAIL|ERROR)', re.MULTILINE)


def _parse_test_statuses(test_output: str) -> dict:
    statuses = {}
    for _test_name, qualified_id, result in _VERBOSE_LINE_RE.findall(test_output):
        # qualified_id from unittest -v is already the full dotted id
        # (e.g. "test_calculator.TestCalculator.test_add") - use it as-is
        # rather than re-concatenating, which would duplicate the method name.
        statuses[qualified_id] = result
    return statuses


class RegressionChecker:
    def compare(self, baseline_result: dict, final_result: dict) -> RegressionResult:
        baseline_stderr = (baseline_result or {}).get("stderr", "") or ""
        final_stderr = (final_result or {}).get("stderr", "") or ""

        baseline_tests = _parse_test_statuses(baseline_stderr)
        final_tests = _parse_test_statuses(final_stderr)

        regressions = sorted(
            t for t, status in baseline_tests.items()
            if status == "ok" and final_tests.get(t) in ("FAIL", "ERROR")
        )

        return RegressionResult(
            baseline_tests=baseline_tests, final_tests=final_tests,
            regressions=regressions, regression_detected=len(regressions) > 0,
        )
