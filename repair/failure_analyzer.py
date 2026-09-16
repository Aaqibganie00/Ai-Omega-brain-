"""
SELF-CORRECTION LAYER — FAILURE ANALYZER
--------------------------------------------
Deterministic, heuristic analysis of REAL test output (stdout/stderr from
tools.run_tests). This does not call a model - it parses actual unittest
output, so its conclusions are grounded in evidence rather than guessed.
confidence stays low/explicit when the parse is ambiguous, per the "do not
allow unsupported guesses to be treated as facts" requirement.

IMPORTANT SCOPING NOTE (found via real testing, not assumed up front): a
Python traceback's file frames point to where the assertion executed -
almost always the TEST file itself - not necessarily where the bug lives.
Scoping a fix to "files mentioned in the traceback" would therefore lock
the fixer OUT of the actual buggy implementation file. So: traceback files
that look like test files are kept separate (as tests_involved, never
included in affected_files - the fixer must not be able to edit them,
which also prevents "fixing" a test by weakening its assertion) and,
when a tool_registry is supplied, this walks their imports to find the
real implementation module(s) to scope the fix to instead.
"""

import re
from tools import ToolCall
from .schemas import FailureAnalysis

_TRACEBACK_FILE_RE = re.compile(r'File "([^"]+)", line (\d+)')
_FAIL_TEST_RE = re.compile(r'^(FAIL|ERROR): (\S+)', re.MULTILINE)
_EXCEPTION_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Failure)):\s*(.*)$', re.MULTILINE)
_IMPORT_RE = re.compile(r'^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))', re.MULTILINE)


def _is_project_file(path: str) -> bool:
    lowered = path.lower()
    return not any(seg in lowered for seg in ("site-packages", "/usr/lib/python", "lib/python3"))


def _looks_like_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py")


class FailureAnalyzer:
    def analyze(self, test_result: dict, task_context: dict = None, tool_registry=None) -> FailureAnalysis:
        stdout = test_result.get("stdout", "") or ""
        stderr = test_result.get("stderr", "") or ""
        combined = stdout + "\n" + stderr

        if test_result.get("passed"):
            return FailureAnalysis(
                failure_type="none", root_cause="Tests passed - no failure to analyze.",
                confidence=1.0, evidence=combined[-500:],
            )

        exc_match = _EXCEPTION_RE.search(combined)
        failure_type = exc_match.group(1) if exc_match else "UnknownFailure"
        exc_detail = exc_match.group(2).strip() if exc_match else ""

        failing_tests = _FAIL_TEST_RE.findall(combined)
        test_names = [t[1] for t in failing_tests]

        file_matches = _TRACEBACK_FILE_RE.findall(combined)
        raw_files = sorted({f for f, _line in file_matches if _is_project_file(f)})

        test_files = [f for f in raw_files if _looks_like_test_file(f)]
        non_test_files = [f for f in raw_files if not _looks_like_test_file(f)]

        affected_files = list(non_test_files)
        if tool_registry is not None:
            affected_files.extend(self._resolve_implementation_files(test_files, tool_registry))
        affected_files = sorted(set(affected_files))

        if exc_match:
            root_cause = f"{failure_type}: {exc_detail}" if exc_detail else failure_type
            confidence = 0.7 if affected_files else 0.5
        elif test_names:
            root_cause = f"Test(s) failed: {', '.join(test_names)} (no exception type parsed)"
            confidence = 0.3
        else:
            root_cause = "Test run did not pass, but no recognizable failure pattern was found in the output."
            confidence = 0.1

        return FailureAnalysis(
            failure_type=failure_type,
            root_cause=root_cause,
            affected_files=affected_files,
            evidence=combined[-800:],
            recommended_fix=(
                f"Inspect the failing assertion/exception in {', '.join(test_names) or 'the failing test'} "
                "and correct the implementation to satisfy it."
            ),
            confidence=confidence,
        )

    def _resolve_implementation_files(self, test_files: list, tool_registry) -> list:
        """Reads each failing test file through the registry's own
        permission/audit pipeline (ToolCall -> execute() - never impl
        directly) and regex-parses its local imports to find candidate
        implementation files that actually exist in the project.
        Deterministic - no model call."""
        resolved = []
        for tf in test_files:
            base = tf.rsplit("/", 1)[-1]
            read = tool_registry.execute(ToolCall(
                tool_name="read_file", arguments={"path": base},
                requested_by="repair.failure_analyzer",
            ))
            if not read.ok:
                continue
            content = read.output["content"]
            for from_mod, import_mod in _IMPORT_RE.findall(content):
                module = from_mod or import_mod
                if not module:
                    continue
                candidate = f"{module}.py"
                probe = tool_registry.execute(ToolCall(
                    tool_name="read_file", arguments={"path": candidate},
                    requested_by="repair.failure_analyzer",
                ))
                if probe.ok:
                    resolved.append(candidate)
        return resolved

