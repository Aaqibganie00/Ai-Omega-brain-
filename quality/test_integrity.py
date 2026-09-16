"""
QUALITY CONTROL LAYER — TEST INTEGRITY CHECK
--------------------------------------------------
Snapshots test files before/after and flags obvious manipulation: deleted
tests, decreased assertion counts, newly added skip markers, and newly
introduced tautological assertions (assertTrue(True), assertEqual(x, x)).

Explicitly NOT a complete adversarial-proof system (per item 6's own
scope limit) - these are the reasonable MVP checks named in the spec, not
an exhaustive static analyzer.
"""

import re
from tools import ToolCall
from .schemas import TestIntegrityResult

_ASSERT_RE = re.compile(r'\bassert\w*\s*\(')
_SKIP_RE = re.compile(r'@(unittest\.)?(skip|skipIf|skipUnless)\b')
_EXPECTED_FAILURE_RE = re.compile(r'@(unittest\.)?expectedFailure\b')
_TRIVIAL_RE = re.compile(
    r'assertTrue\(\s*True\s*\)|assertEqual\(\s*(\w+)\s*,\s*\1\s*\)|assertEqual\(\s*(\d+)\s*,\s*\2\s*\)'
)


def _is_test_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py")


class TestIntegrityChecker:
    def snapshot(self, tool_registry) -> dict:
        """Read-only: every file operation goes through the registry's
        permission/audit pipeline (ToolCall -> execute()), never impl directly.
        If listing/reading is denied, the snapshot is simply empty/skips the
        file - a degraded snapshot can only make the integrity check stricter
        downstream, never looser."""
        listing = tool_registry.execute(ToolCall(
            tool_name="list_files", arguments={"path": ".", "recursive": True},
            requested_by="quality.test_integrity",
        ))
        files = {}
        if not listing.ok:
            return files
        for path in listing.output["entries"]:
            if _is_test_file(path):
                read = tool_registry.execute(ToolCall(
                    tool_name="read_file", arguments={"path": path},
                    requested_by="quality.test_integrity",
                ))
                if read.ok:
                    files[path] = read.output["content"]
        return files

    def check(self, before: dict, after: dict) -> TestIntegrityResult:
        before_keys, after_keys = set(before), set(after)
        created = after_keys - before_keys
        deleted = before_keys - after_keys
        common = before_keys & after_keys
        modified = {p for p in common if before[p] != after[p]}

        suspicious = []
        for p in deleted:
            suspicious.append(f"Test file deleted: {p}")

        for p in modified:
            b, a = before[p], after[p]
            b_asserts, a_asserts = len(_ASSERT_RE.findall(b)), len(_ASSERT_RE.findall(a))
            if a_asserts < b_asserts:
                suspicious.append(f"{p}: assertion count decreased ({b_asserts} -> {a_asserts}) - possible weakened test")

            if _SKIP_RE.search(a) and not _SKIP_RE.search(b):
                suspicious.append(f"{p}: a test was newly marked @skip")
            if _EXPECTED_FAILURE_RE.search(a) and not _EXPECTED_FAILURE_RE.search(b):
                suspicious.append(f"{p}: a test was newly marked @expectedFailure")
            if _TRIVIAL_RE.search(a) and not _TRIVIAL_RE.search(b):
                suspicious.append(f"{p}: a trivial/tautological assertion was introduced")

            if "def test_" in b:
                before_test_count = len(re.findall(r'def test_\w+', b))
                after_test_count = len(re.findall(r'def test_\w+', a))
                if after_test_count < before_test_count:
                    suspicious.append(f"{p}: number of test methods decreased ({before_test_count} -> {after_test_count})")

        return TestIntegrityResult(
            tests_before=before_keys, tests_created=created, tests_modified=modified,
            tests_deleted=deleted, suspicious=suspicious, integrity_ok=(len(suspicious) == 0),
        )
