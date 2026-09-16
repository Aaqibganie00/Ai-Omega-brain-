"""
QUALITY CONTROL LAYER — ORCHESTRATOR
-------------------------------------------
    EXECUTOR (Phase 4, reused unmodified)
        -> TEST
        -> requirements + evidence + integrity + audit + scope + regression
        -> CRITIC (improved)
        -> QUALITY GATE
        -> (if REJECTED and budget remains) one more Phase-4 repair round
        -> re-verify once (second pass, item 12)
        -> final VerificationReport

Reuses repair.AutonomousTaskExecutor for all execution/repair - this file
adds judgment on top, it does not reimplement repair.
"""

import uuid

from tools import ToolCall
from agent.events import AgentEventLog
from repair import AutonomousTaskExecutor
from .schemas import QualityGateDecision, VerificationReport
from .requirements import extract_requirements
from .evidence import EvidenceCollector
from .test_integrity import TestIntegrityChecker
from .file_audit import FileChangeAuditor
from .scope import ScopeChecker
from .regression import RegressionChecker
from .critic_v2 import ImprovedCritic
from .gate import QualityGate


class QualityControlledExecutor:
    def __init__(self, provider, tool_registry, project_memory=None,
                 max_repair_attempts=3, max_tool_turns_per_repair=4, max_execution_seconds=120):
        self.provider = provider
        self.tool_registry = tool_registry
        self.project_memory = project_memory
        self.autonomous_executor = AutonomousTaskExecutor(
            provider=provider, tool_registry=tool_registry, project_memory=None,
            max_repair_attempts=max_repair_attempts, max_tool_turns_per_repair=max_tool_turns_per_repair,
            max_execution_seconds=max_execution_seconds,
        )
        self.evidence_collector = EvidenceCollector(tool_registry)
        self.integrity_checker = TestIntegrityChecker()
        self.file_auditor = FileChangeAuditor()
        self.scope_checker = ScopeChecker()
        self.regression_checker = RegressionChecker()
        self.critic = ImprovedCritic()
        self.gate = QualityGate()

    def run(self, objective, task_id=None, creation_worker=None,
            required_files=None, required_features=None, required_tests=None,
            entry_command=None, test_command=None):
        task_id = task_id or uuid.uuid4().hex[:12]
        log = AgentEventLog()

        requirements = extract_requirements(
            objective, required_files=required_files, required_features=required_features,
            required_tests=required_tests, entry_command=entry_command,
        )
        log.emit("REQUIREMENTS_EXTRACTED", task_id, detail=f"files={requirements.required_files} features={requirements.required_features}")

        files_before = self.file_auditor.snapshot(self.tool_registry)
        tests_before = self.integrity_checker.snapshot(self.tool_registry)
        # Permission/audit-pipeline (never registry.impl directly): the
        # baseline run goes through the same run_tests gate as everything
        # else - including the command classification. A DENIED baseline is
        # represented as a non-passing result, never fabricated as a pass.
        baseline_call = ToolCall(
            tool_name="run_tests",
            arguments=({"test_command": test_command} if test_command else {}),
            requested_by="quality.orchestrator",
        )
        baseline_result = self.tool_registry.execute(baseline_call)
        if baseline_result.ok and isinstance(baseline_result.output, dict):
            baseline_test_result = baseline_result.output
        else:
            baseline_test_result = {"passed": False, "returncode": -1, "stdout": "",
                                    "stderr": baseline_result.error or "run_tests tool failed"}

        exec_result = self.autonomous_executor.run(
            objective, task_id=f"{task_id}-exec", creation_worker=creation_worker,
            test_command=test_command, required_files=required_files,
        )

        (decision, files_changed_total, requirement_checks, integrity_result, audit,
         scope_result, regression_result, critic_result, final_test_result, tests_after) = self._verify_once(
            task_id, log, requirements, files_before, tests_before, baseline_test_result,
            test_command, exec_result.files_changed_total, exec_result.final_test_result, len(exec_result.attempts),
        )

        if decision == QualityGateDecision.REJECTED and len(exec_result.attempts) < self.autonomous_executor.max_repair_attempts:
            log.emit("REPAIR_STARTED", task_id, detail="quality-gate-triggered second pass")
            exec_result2 = self.autonomous_executor.run(
                objective, task_id=f"{task_id}-exec2", test_command=test_command, required_files=required_files,
            )
            (decision, files_changed_total, requirement_checks, integrity_result, audit,
             scope_result, regression_result, critic_result, final_test_result, tests_after) = self._verify_once(
                task_id, log, requirements, files_before, tests_before, baseline_test_result,
                test_command, exec_result2.files_changed_total, exec_result2.final_test_result, len(exec_result2.attempts),
                label="second-pass ",
            )

        report = VerificationReport(
            task_id=task_id, requirements=requirements, requirement_checks=requirement_checks,
            files_created=audit.created, files_modified=audit.modified, files_deleted=audit.deleted,
            tests_before=sorted(tests_before.keys()), tests_after=sorted(tests_after.keys()),
            regression=regression_result, runtime_status=None, critic=critic_result,
            integrity=integrity_result, scope=scope_result, out_of_scope_changes=scope_result.out_of_scope_changes,
            final_decision=decision.value, events=log.as_dicts(),
        )

        if self.project_memory is not None:
            self._record_memory(report)
        return report

    def _verify_once(self, task_id, log, requirements, files_before, tests_before, baseline_test_result,
                      test_command, files_changed_total, final_test_result, attempts_made, label=""):
        log.emit("EVIDENCE_COLLECTION_STARTED", task_id, detail=(label.strip() or None))
        evidence = self.evidence_collector.collect(requirements, test_command=test_command)
        if final_test_result:
            evidence["test_result"] = final_test_result
        log.emit("EVIDENCE_COLLECTED", task_id)

        requirement_checks = self.evidence_collector.check_requirements(requirements, evidence)

        log.emit("TEST_INTEGRITY_CHECKED", task_id)
        tests_after = self.integrity_checker.snapshot(self.tool_registry)
        integrity_result = self.integrity_checker.check(tests_before, tests_after)

        files_after = self.file_auditor.snapshot(self.tool_registry)
        audit = self.file_auditor.audit(files_before, files_after)

        log.emit("SCOPE_CHECK_STARTED", task_id)
        expected_scope = set(requirements.required_files) | set(files_changed_total)
        changed_files = sorted(set(audit.created) | set(audit.modified))
        scope_result = self.scope_checker.check(changed_files, expected_scope)
        log.emit("SCOPE_CHECK_RESULT", task_id, detail=f"out_of_scope={scope_result.out_of_scope_changes}")

        log.emit("REGRESSION_CHECKED", task_id)
        regression_result = self.regression_checker.compare(baseline_test_result, evidence.get("test_result", {}))

        log.emit("CRITIC_STARTED", task_id)
        critic_result = self.critic.review(
            requirements, requirement_checks, evidence.get("test_result", {}), regression_result,
            integrity_result, scope_result, files_changed_total, attempts_made=attempts_made,
        )
        log.emit("CRITIC_RESULT", task_id, success=critic_result.approved, detail=critic_result.severity)

        log.emit("QUALITY_GATE_STARTED", task_id)
        decision, reasons = self.gate.decide(
            requirement_checks, evidence.get("test_result", {}), regression_result, audit, scope_result, integrity_result, critic_result,
        )
        log.emit("QUALITY_GATE_RESULT", task_id, detail=f"{decision.value}: {reasons}")

        return (decision, files_changed_total, requirement_checks, integrity_result, audit,
                scope_result, regression_result, critic_result, evidence.get("test_result", {}), tests_after)

    def _record_memory(self, report):
        summary = {
            "task_id": report.task_id,
            "final_decision": report.final_decision,
            "requirement_status": {rc.requirement: rc.status for rc in report.requirement_checks},
            "critic_findings": report.critic.issues,
            "regression_status": {"detected": report.regression.regression_detected, "regressions": report.regression.regressions},
            "files_created": report.files_created, "files_modified": report.files_modified, "files_deleted": report.files_deleted,
        }
        self.project_memory.remember(
            key=f"quality_report:{report.task_id}", value=summary,
            verified=(report.final_decision == "APPROVED"), source="quality_control",
        )
