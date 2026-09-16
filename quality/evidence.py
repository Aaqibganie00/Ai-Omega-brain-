"""
QUALITY CONTROL LAYER — EVIDENCE COLLECTOR
-----------------------------------------------
Read-only by design: only calls read_file/list_files/search_files/
inspect_project/run_tests/run_command (for the optional runtime check).
Never write_file/edit_file - the Quality Control layer must not modify
project files (item 18).

Every operation funnels through the normal ToolCall -> ToolRegistry.execute()
pipeline (validate -> permission -> audit-log), never through
registry.impl.* directly. Previously the impl bypass meant evidence
operations (including the caller-supplied entry_command, which executes a
subprocess) ran with NO permission check and left NO audit-log entry - so
e.g. a READ-only permission config could not actually stop the collector
from executing commands. That hole is closed: DENIED operations now
produce recorded evidence of denial instead of silent execution.
"""

import re
from tools import ToolCall
from .schemas import RequirementCheck, RequirementStatus, Severity


class EvidenceCollector:
    READ_ONLY_TOOLS = {"read_file", "list_files", "search_files", "inspect_project", "run_tests", "run_command"}

    def __init__(self, tool_registry):
        self.tool_registry = tool_registry

    def _execute(self, tool_name, arguments):
        """Single entry point for every evidence operation: builds a normal
        ToolCall and runs it through the registry's own permission/audit
        pipeline. Works with ToolRegistry and ScopedToolRegistry alike (only
        .execute() is required)."""
        return self.tool_registry.execute(ToolCall(
            tool_name=tool_name, arguments=arguments, requested_by="quality.evidence",
        ))

    def collect(self, requirements, test_command=None) -> dict:
        evidence = {"file_existence": {}, "file_contents": {}, "runtime": None}

        for path in requirements.required_files:
            result = self._execute("read_file", {"path": path})
            if result.ok:
                evidence["file_existence"][path] = True
                evidence["file_contents"][path] = result.output["content"]
            else:
                evidence["file_existence"][path] = False

        result = self._execute("run_tests", ({"test_command": test_command} if test_command else {}))
        if result.ok and isinstance(result.output, dict):
            evidence["test_result"] = result.output
        else:
            # non-ok includes DENIED (missing EXECUTE permission or rejected
            # command classification) - the denial is already audit-logged by
            # the registry; evidence reflects "not verified", never a fake pass.
            evidence["test_result"] = {"passed": False, "stdout": "", "stderr": result.error or f"run_tests tool status: {result.status.value}"}

        result = self._execute("inspect_project", {})
        evidence["project_structure"] = result.output if result.ok else None

        if requirements.entry_command:
            result = self._execute("run_command", {"command": requirements.entry_command, "timeout": 15})
            if result.ok and isinstance(result.output, dict):
                evidence["runtime"] = {"command": requirements.entry_command, "returncode": result.output["returncode"],
                                        "stdout": result.output["stdout"], "stderr": result.output["stderr"]}
            else:
                # includes DENIED - command was NOT executed; the registry log
                # records the denial. returncode -1 mirrors the previous
                # error-path shape without ever fabricating a run.
                evidence["runtime"] = {"command": requirements.entry_command, "returncode": -1,
                                        "error": result.error or f"run_command tool status: {result.status.value}"}

        return evidence

    def check_requirements(self, requirements, evidence) -> list:
        checks = []

        for path in requirements.required_files:
            exists = evidence["file_existence"].get(path, False)
            checks.append(RequirementCheck(
                requirement=f"required file exists: {path}",
                status=RequirementStatus.PASS.value if exists else RequirementStatus.FAIL.value,
                evidence=f"file_existence[{path}]={exists}",
                severity=Severity.HIGH.value,
            ))

        test_stdout = (evidence.get("test_result", {}) or {}).get("stdout", "") or ""
        test_stderr = (evidence.get("test_result", {}) or {}).get("stderr", "") or ""
        combined = test_stdout + "\n" + test_stderr

        for feature in requirements.required_features:
            checks.append(self._check_feature(feature, combined, evidence))

        for test_name in requirements.required_tests:
            checks.append(self._check_named_test(test_name, combined))

        if requirements.entry_command:
            runtime = evidence.get("runtime") or {}
            rc = runtime.get("returncode")
            if rc is None:
                status, ev = RequirementStatus.UNKNOWN.value, "runtime check did not produce a result"
            elif rc == 0:
                status, ev = RequirementStatus.PASS.value, f"'{requirements.entry_command}' exited 0"
            else:
                status, ev = RequirementStatus.FAIL.value, f"'{requirements.entry_command}' exited {rc}: {runtime.get('stderr', '')[:200]}"
            checks.append(RequirementCheck(requirement=f"runtime check: {requirements.entry_command}",
                                            status=status, evidence=ev, severity=Severity.HIGH.value))

        return checks

    def _check_feature(self, feature: str, test_output: str, evidence) -> RequirementCheck:
        test_name_pattern = re.compile(rf'test_\w*{re.escape(feature)}\w*', re.IGNORECASE)
        names_in_output = set(test_name_pattern.findall(test_output))

        if not names_in_output:
            return RequirementCheck(
                requirement=f"feature implemented: {feature}",
                status=RequirementStatus.UNKNOWN.value,
                evidence="no test referencing this feature was found in the test output",
                severity=Severity.MEDIUM.value,
            )

        failed = any(re.search(rf'(FAIL|ERROR): {re.escape(n)}', test_output) for n in names_in_output)
        status = RequirementStatus.FAIL.value if failed else RequirementStatus.PASS.value
        return RequirementCheck(
            requirement=f"feature implemented: {feature}",
            status=status,
            evidence=f"matched test(s): {sorted(names_in_output)}; failed={failed}",
            severity=Severity.HIGH.value,
        )

    def _check_named_test(self, test_name: str, test_output: str) -> RequirementCheck:
        if test_name not in test_output:
            return RequirementCheck(
                requirement=f"required test exists and ran: {test_name}",
                status=RequirementStatus.UNKNOWN.value,
                evidence="test name not found in test run output",
                severity=Severity.MEDIUM.value,
            )
        failed = bool(re.search(rf'(FAIL|ERROR): {re.escape(test_name)}', test_output))
        return RequirementCheck(
            requirement=f"required test passes: {test_name}",
            status=RequirementStatus.FAIL.value if failed else RequirementStatus.PASS.value,
            evidence=f"failed={failed}",
            severity=Severity.HIGH.value,
        )
