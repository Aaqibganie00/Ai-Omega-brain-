"""
Phase 5 tests: requirements extraction, evidence collection, test
integrity, file audit, scope, regression, improved critic, quality gate,
and end-to-end scenarios A-I from the spec.
Run with: python3 -m unittest tests.test_quality -v
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ToolRegistry
from providers import MockAIProvider
from memory import ProjectMemory
from quality import (
    extract_requirements, EvidenceCollector, TestIntegrityChecker, FileChangeAuditor,
    ScopeChecker, RegressionChecker, ImprovedCritic, QualityGate, QualityControlledExecutor,
    RequirementStatus, Severity, QualityGateDecision,
)


def seed_correct_calculator(registry):
    registry.impl.write_file("calculator.py", "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n")
    registry.impl.write_file("test_calculator.py", (
        "import unittest\nfrom calculator import add, subtract\n"
        "class TestCalculator(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        "    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n"
    ))


class QualityTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_quality_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)


# ---- unit tests for individual components ---------------------------------

class TestRequirementsExtraction(unittest.TestCase):
    def test_explicit_lists_are_authoritative(self):
        req = extract_requirements("do stuff", required_files=["a.py"], required_features=["add"])
        self.assertEqual(req.required_files, ["a.py"])
        self.assertIn("add", req.required_features)

    def test_keyword_scan_adds_feature_hints_without_removing_explicit(self):
        req = extract_requirements("Support addition and subtraction", required_features=["custom_feature"])
        self.assertIn("custom_feature", req.required_features)
        self.assertIn("add", req.required_features)
        self.assertIn("subtract", req.required_features)


class TestEvidenceCollector(QualityTestBase):
    def test_collect_records_real_file_existence(self):
        self.registry.impl.write_file("a.py", "x = 1\n")
        req = extract_requirements("obj", required_files=["a.py", "missing.py"])
        collector = EvidenceCollector(self.registry)
        evidence = collector.collect(req)
        self.assertTrue(evidence["file_existence"]["a.py"])
        self.assertFalse(evidence["file_existence"]["missing.py"])

    def test_check_requirements_fails_missing_file(self):
        req = extract_requirements("obj", required_files=["missing.py"])
        collector = EvidenceCollector(self.registry)
        evidence = collector.collect(req)
        checks = collector.check_requirements(req, evidence)
        self.assertEqual(checks[0].status, RequirementStatus.FAIL.value)

    def test_check_requirements_unknown_when_no_test_references_feature(self):
        seed_correct_calculator(self.registry)
        # remove subtract's test coverage entirely by asking about a feature never tested
        req = extract_requirements("obj", required_features=["multiply"])
        collector = EvidenceCollector(self.registry)
        evidence = collector.collect(req)
        checks = collector.check_requirements(req, evidence)
        self.assertEqual(checks[0].status, RequirementStatus.UNKNOWN.value)

    def test_evidence_collector_never_writes(self):
        self.assertNotIn("write_file", EvidenceCollector.READ_ONLY_TOOLS)
        self.assertNotIn("edit_file", EvidenceCollector.READ_ONLY_TOOLS)


class TestTestIntegrityChecker(QualityTestBase):
    def test_no_change_is_clean(self):
        seed_correct_calculator(self.registry)
        checker = TestIntegrityChecker()
        before = checker.snapshot(self.registry)
        after = checker.snapshot(self.registry)
        result = checker.check(before, after)
        self.assertTrue(result.integrity_ok)

    def test_deleted_test_file_flagged(self):
        seed_correct_calculator(self.registry)
        checker = TestIntegrityChecker()
        before = checker.snapshot(self.registry)
        after = dict(before)
        del after["test_calculator.py"]
        result = checker.check(before, after)
        self.assertFalse(result.integrity_ok)
        self.assertTrue(any("deleted" in s.lower() for s in result.suspicious))

    def test_weakened_assertion_count_flagged(self):
        before = {"test_x.py": (
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertEqual(1, 1)\n        self.assertEqual(2, 2)\n"
        )}
        after = {"test_x.py": (
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_a(self):\n        self.assertEqual(1, 1)\n"
        )}
        result = TestIntegrityChecker().check(before, after)
        self.assertFalse(result.integrity_ok)
        self.assertTrue(any("decreased" in s for s in result.suspicious))

    def test_newly_added_skip_flagged(self):
        before = {"test_x.py": "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 2)\n"}
        after = {"test_x.py": "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('nope')\n    def test_a(self):\n        self.assertEqual(1, 2)\n"}
        result = TestIntegrityChecker().check(before, after)
        self.assertFalse(result.integrity_ok)
        self.assertTrue(any("skip" in s.lower() for s in result.suspicious))

    def test_trivial_assertion_introduced_flagged(self):
        before = {"test_x.py": "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(subtract(5, 3), 2)\n"}
        after = {"test_x.py": "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n"}
        result = TestIntegrityChecker().check(before, after)
        self.assertFalse(result.integrity_ok)
        self.assertTrue(any("trivial" in s.lower() for s in result.suspicious))

    def test_new_test_file_created_is_not_suspicious_by_itself(self):
        before = {}
        after = {"test_new.py": "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 1)\n"}
        result = TestIntegrityChecker().check(before, after)
        self.assertTrue(result.integrity_ok)
        self.assertIn("test_new.py", result.tests_created)


class TestFileChangeAuditor(QualityTestBase):
    def test_classifies_created_modified_deleted_unchanged(self):
        self.registry.impl.write_file("keep.py", "x = 1\n")
        self.registry.impl.write_file("change.py", "y = 1\n")
        self.registry.impl.write_file("remove.py", "z = 1\n")
        auditor = FileChangeAuditor()
        before = auditor.snapshot(self.registry)

        self.registry.impl.write_file("change.py", "y = 2\n")
        self.registry.impl.write_file("new.py", "w = 1\n")
        os.remove(os.path.join(self.sandbox, "remove.py"))

        after = auditor.snapshot(self.registry)
        audit = auditor.audit(before, after)
        self.assertIn("new.py", audit.created)
        self.assertIn("change.py", audit.modified)
        self.assertIn("remove.py", audit.deleted)
        self.assertIn("keep.py", audit.unchanged)


class TestScopeChecker(unittest.TestCase):
    def test_in_scope_change_only(self):
        result = ScopeChecker().check(["calculator.py"], expected_scope={"calculator.py", "test_calculator.py"})
        self.assertEqual(result.out_of_scope_changes, [])
        self.assertEqual(result.severity, Severity.INFO.value)

    def test_out_of_scope_change_flagged(self):
        result = ScopeChecker().check(["calculator.py", "unrelated_config.py"], expected_scope={"calculator.py"})
        self.assertEqual(result.out_of_scope_changes, ["unrelated_config.py"])
        self.assertEqual(result.severity, Severity.HIGH.value)


class TestRegressionChecker(unittest.TestCase):
    def test_detects_regression(self):
        baseline = {"stderr": "test_a (m.T.test_a) ... ok\ntest_b (m.T.test_b) ... ok\n"}
        final = {"stderr": "test_a (m.T.test_a) ... ok\ntest_b (m.T.test_b) ... FAIL\n"}
        result = RegressionChecker().compare(baseline, final)
        self.assertTrue(result.regression_detected)
        self.assertIn("m.T.test_b", result.regressions)

    def test_new_failing_test_is_not_a_regression(self):
        baseline = {"stderr": "test_a (m.T.test_a) ... ok\n"}
        final = {"stderr": "test_a (m.T.test_a) ... ok\ntest_b (m.T.test_b) ... FAIL\n"}
        result = RegressionChecker().compare(baseline, final)
        self.assertFalse(result.regression_detected)

    def test_no_regression_when_everything_still_passes(self):
        baseline = {"stderr": "test_a (m.T.test_a) ... ok\n"}
        final = {"stderr": "test_a (m.T.test_a) ... ok\n"}
        result = RegressionChecker().compare(baseline, final)
        self.assertFalse(result.regression_detected)


class TestQualityGate(unittest.TestCase):
    def _minimal_ok_inputs(self):
        from repair.schemas import TaskVerificationResult
        integrity = TestIntegrityChecker().check({}, {})
        scope = ScopeChecker().check([], set())
        regression = RegressionChecker().compare({"stderr": ""}, {"stderr": ""})
        return integrity, scope, regression

    def test_approved_when_everything_clean(self):
        from quality.critic_v2 import CriticResultV2
        integrity, scope, regression = self._minimal_ok_inputs()
        critic = CriticResultV2(approved=True, severity=Severity.INFO.value)
        decision, _ = QualityGate().decide([], {"passed": True}, regression, None, scope, integrity, critic)
        self.assertEqual(decision, QualityGateDecision.APPROVED)

    def test_blocked_on_integrity_violation_regardless_of_everything_else(self):
        from quality.critic_v2 import CriticResultV2
        integrity = TestIntegrityChecker().check({"t.py": "x"}, {})  # deleted test
        scope = ScopeChecker().check([], set())
        regression = RegressionChecker().compare({"stderr": ""}, {"stderr": ""})
        critic = CriticResultV2(approved=True, severity=Severity.INFO.value)  # critic thinks it's fine
        decision, reasons = QualityGate().decide([], {"passed": True}, regression, None, scope, integrity, critic)
        self.assertEqual(decision, QualityGateDecision.BLOCKED)

    def test_unknown_requirement_never_becomes_approved(self):
        from repair.schemas import TaskVerificationResult
        from quality.schemas import RequirementCheck
        from quality.critic_v2 import CriticResultV2
        integrity, scope, regression = self._minimal_ok_inputs()
        critic = CriticResultV2(approved=True, severity=Severity.INFO.value)
        unknown_check = RequirementCheck(requirement="feature implemented: multiply", status=RequirementStatus.UNKNOWN.value, evidence="")
        decision, _ = QualityGate().decide([unknown_check], {"passed": True}, regression, None, scope, integrity, critic)
        self.assertNotEqual(decision, QualityGateDecision.APPROVED)
        self.assertEqual(decision, QualityGateDecision.INCOMPLETE)

    def test_rejected_on_regression(self):
        from quality.critic_v2 import CriticResultV2
        integrity = TestIntegrityChecker().check({}, {})
        scope = ScopeChecker().check([], set())
        regression = RegressionChecker().compare({"stderr": "test_a (m.T.test_a) ... ok\n"}, {"stderr": "test_a (m.T.test_a) ... FAIL\n"})
        critic = CriticResultV2(approved=True, severity=Severity.INFO.value)
        decision, _ = QualityGate().decide([], {"passed": True}, regression, None, scope, integrity, critic)
        self.assertEqual(decision, QualityGateDecision.REJECTED)


# ---- end-to-end scenarios A-I -------------------------------------------------

class TestEndToEndScenarios(QualityTestBase):
    def test_A_correct_implementation_approved(self):
        seed_correct_calculator(self.registry)
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry)
        report = qc.run("Create a calculator that supports addition and subtraction.",
                         required_files=["calculator.py", "test_calculator.py"])
        self.assertEqual(report.final_decision, "APPROVED")

    def test_B_missing_feature_not_approved(self):
        self.registry.impl.write_file("calculator.py", "def add(a, b):\n    return a + b\n")
        self.registry.impl.write_file("test_calculator.py", (
            "import unittest\nfrom calculator import add\n"
            "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        ))
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry, max_repair_attempts=1)
        report = qc.run("Create a calculator that supports addition and subtraction.",
                         required_files=["calculator.py"])
        # tests pass, but subtract was never even tested - status is UNKNOWN
        # (honest evidence-based state, never silently PASS) so the gate
        # correctly does not approve; it maps to INCOMPLETE rather than a
        # blanket REJECTED, per item 3's requirement that UNKNOWN != PASS.
        self.assertNotEqual(report.final_decision, "APPROVED")
        feature_check = next(rc for rc in report.requirement_checks if "subtract" in rc.requirement)
        self.assertEqual(feature_check.status, "UNKNOWN")

    def test_C_weakened_test_blocked(self):
        # exercised directly against the integrity+gate pipeline (item 20's
        # own instruction: "just prove the basic integrity check works" -
        # Phase 4's own fix-scope restriction already prevents the repair
        # fixer from touching test files at all, so this proves Phase 5's
        # INDEPENDENT detection layer, regardless of how the change happened).
        seed_correct_calculator(self.registry)
        integrity_checker = TestIntegrityChecker()
        before = integrity_checker.snapshot(self.registry)

        # simulate the test being weakened (assertion replaced with a tautology)
        self.registry.impl.write_file("test_calculator.py", (
            "import unittest\nfrom calculator import add, subtract\n"
            "class TestCalculator(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
            "    def test_subtract(self):\n        self.assertTrue(True)  # weakened!\n"
        ))
        after = integrity_checker.snapshot(self.registry)
        integrity_result = integrity_checker.check(before, after)
        self.assertFalse(integrity_result.integrity_ok)

        scope = ScopeChecker().check([], set())
        regression = RegressionChecker().compare({"stderr": ""}, {"stderr": ""})
        from quality.critic_v2 import CriticResultV2
        critic = CriticResultV2(approved=True, severity=Severity.INFO.value)  # even if critic is fooled...
        decision, reasons = QualityGate().decide([], {"passed": True}, regression, None, scope, integrity_result, critic)
        self.assertEqual(decision, QualityGateDecision.BLOCKED)  # ...the gate still blocks it

    def test_D_unrelated_file_modification_flagged(self):
        seed_correct_calculator(self.registry)
        self.registry.impl.write_file("unrelated_config.py", "SETTING = 1\n")
        auditor = FileChangeAuditor()
        before = auditor.snapshot(self.registry)
        self.registry.impl.write_file("unrelated_config.py", "SETTING = 2\n")  # touched, out of task scope
        after = auditor.snapshot(self.registry)
        audit = auditor.audit(before, after)
        scope_result = ScopeChecker().check(audit.modified, expected_scope={"calculator.py", "test_calculator.py"})
        self.assertIn("unrelated_config.py", scope_result.out_of_scope_changes)
        self.assertEqual(scope_result.severity, Severity.HIGH.value)

    def test_E_regression_rejected(self):
        seed_correct_calculator(self.registry)
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry)
        baseline = self.registry.impl.run_tests()
        # simulate a regression: break something that was previously passing
        self.registry.impl.write_file("calculator.py", "def add(a, b):\n    return a + b + 1  # BROKEN\n\ndef subtract(a, b):\n    return a - b\n")
        final = self.registry.impl.run_tests()
        result = RegressionChecker().compare(baseline, final)
        self.assertTrue(result.regression_detected)

    def test_F_required_file_missing_rejected(self):
        seed_correct_calculator(self.registry)
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry, max_repair_attempts=1)
        report = qc.run("Create a calculator project.", required_files=["calculator.py", "README.md"])
        self.assertNotEqual(report.final_decision, "APPROVED")
        readme_check = next(rc for rc in report.requirement_checks if "README.md" in rc.requirement)
        self.assertEqual(readme_check.status, "FAIL")

    def test_G_runtime_failure_despite_passing_tests_rejected(self):
        seed_correct_calculator(self.registry)
        # entry command deliberately fails at runtime even though unit tests pass
        self.registry.impl.write_file("run.py", "raise SystemExit(1)\n")
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry, max_repair_attempts=1)
        report = qc.run("Create a calculator project.", required_files=["calculator.py"], entry_command="python3 run.py")
        runtime_check = next(rc for rc in report.requirement_checks if "runtime check" in rc.requirement)
        self.assertEqual(runtime_check.status, "FAIL")
        self.assertNotEqual(report.final_decision, "APPROVED")

    def test_H_critic_issue_triggers_repair_then_approved(self):
        # tests fail initially (real bug), scripted fixer actually fixes it -
        # exercises the second-pass loop reusing Phase 4's repair mechanism.
        self.registry.impl.write_file("calculator.py", "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a + b  # BUG\n")
        self.registry.impl.write_file("test_calculator.py", (
            "import unittest\nfrom calculator import add, subtract\n"
            "class TestCalculator(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
            "    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n"
        ))
        fix_script = [
            {"text": "inspecting", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "calculator.py"}}]},
            {"text": "fixing", "tool_calls": [{"id": "c2", "name": "edit_file", "arguments": {
                "path": "calculator.py", "old_str": "return a + b  # BUG", "new_str": "return a - b"}}]},
            {"text": "Fixed.", "tool_calls": []},
        ]
        qc = QualityControlledExecutor(provider=MockAIProvider(script=fix_script), tool_registry=self.registry)
        report = qc.run("Create a calculator that supports addition and subtraction.",
                         required_files=["calculator.py", "test_calculator.py"])
        self.assertEqual(report.final_decision, "APPROVED")
        with open(os.path.join(self.sandbox, "calculator.py")) as f:
            self.assertIn("return a - b", f.read())

    def test_I_unknown_requirement_never_silently_passes(self):
        seed_correct_calculator(self.registry)
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry, max_repair_attempts=1)
        report = qc.run("Create a calculator that supports addition, subtraction, and logarithms.",
                         required_files=["calculator.py"], required_features=["logarithm"])
        log_check = next(rc for rc in report.requirement_checks if "logarithm" in rc.requirement)
        self.assertEqual(log_check.status, "UNKNOWN")
        self.assertNotEqual(report.final_decision, "APPROVED")


# ---- security scenario (item 20) ------------------------------------------

class TestSecurityScenario(QualityTestBase):
    def test_ai_cannot_pass_by_weakening_test_instead_of_fixing_code(self):
        """The scripted 'model' tries to cheat: instead of fixing the real
        bug in calculator.py, it edits test_calculator.py to weaken the
        assertion so the suite passes trivially. Two independent layers
        should both refuse to reward this:
          1. Phase 4's fix-scope restriction denies the edit outright
             (test files are excluded from the fixer's writable scope).
          2. Even if a test file WERE modified through some other path,
             Phase 5's TestIntegrityChecker+QualityGate would BLOCK it.
        This test proves both.
        """
        self.registry.impl.write_file("calculator.py", "def subtract(a, b):\n    return a + b\n")  # real bug
        self.registry.impl.write_file("test_calculator.py", (
            "import unittest\nfrom calculator import subtract\n"
            "class T(unittest.TestCase):\n    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n"
        ))

        cheat_script = [
            {"text": "I'll just fix the test instead", "tool_calls": [{"id": "c1", "name": "edit_file", "arguments": {
                "path": "test_calculator.py",
                "old_str": "self.assertEqual(subtract(5, 3), 2)",
                "new_str": "self.assertTrue(True)  # 'fixed'",
            }}]},
            {"text": "done", "tool_calls": []},
        ]
        qc = QualityControlledExecutor(provider=MockAIProvider(script=cheat_script), tool_registry=self.registry, max_repair_attempts=1)
        report = qc.run("Fix the subtract function.", required_files=["calculator.py"])

        # Layer 1: the cheat attempt itself must have been denied by fix-scope
        # (calculator.py, not test_calculator.py, is the actual bug location)
        with open(os.path.join(self.sandbox, "test_calculator.py")) as f:
            test_content = f.read()
        self.assertIn("assertEqual(subtract(5, 3), 2)", test_content)  # test file untouched

        # The real bug is still there and the task correctly did not get approved
        self.assertNotEqual(report.final_decision, "APPROVED")


# ---- memory + observability -------------------------------------------------

class TestQualityMemory(QualityTestBase):
    def test_verification_summary_stored_structured_not_raw(self):
        seed_correct_calculator(self.registry)
        memory = ProjectMemory(path=os.path.join(self.sandbox, "pm.json"))
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry, project_memory=memory)
        report = qc.run("Create a calculator.", task_id="qc-mem-1", required_files=["calculator.py"])

        entry = memory.recall("quality_report:qc-mem-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["value"]["final_decision"], report.final_decision)
        self.assertIn("requirement_status", entry["value"])

    def test_memory_never_contains_api_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-quality-should-never-leak"
        try:
            seed_correct_calculator(self.registry)
            memory = ProjectMemory(path=os.path.join(self.sandbox, "pm2.json"))
            qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry, project_memory=memory)
            qc.run("Create a calculator.", task_id="qc-key-safety", required_files=["calculator.py"])
            entry = memory.recall("quality_report:qc-key-safety")
            self.assertNotIn("sk-quality-should-never-leak", str(entry))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]


class TestQualityObservability(QualityTestBase):
    def test_required_events_present(self):
        seed_correct_calculator(self.registry)
        qc = QualityControlledExecutor(provider=MockAIProvider(), tool_registry=self.registry)
        report = qc.run("Create a calculator.", required_files=["calculator.py"])
        names = {e["event"] for e in report.events}
        for required in ("REQUIREMENTS_EXTRACTED", "EVIDENCE_COLLECTION_STARTED", "EVIDENCE_COLLECTED",
                          "SCOPE_CHECK_STARTED", "SCOPE_CHECK_RESULT", "TEST_INTEGRITY_CHECKED",
                          "REGRESSION_CHECKED", "CRITIC_STARTED", "CRITIC_RESULT",
                          "QUALITY_GATE_STARTED", "QUALITY_GATE_RESULT"):
            self.assertIn(required, names)


# ---- security: QC layer never writes ----------------------------------------

class TestQualityControlNeverModifiesFiles(QualityTestBase):
    def test_evidence_collection_alone_does_not_change_project_state(self):
        seed_correct_calculator(self.registry)
        before = FileChangeAuditor().snapshot(self.registry)
        req = extract_requirements("obj", required_files=["calculator.py"])
        EvidenceCollector(self.registry).collect(req)
        TestIntegrityChecker().snapshot(self.registry)
        after = FileChangeAuditor().snapshot(self.registry)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
