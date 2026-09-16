"""
Phase 9 tests: Context/ContextBuilder, multi-signal relevance,
deduplication, evidence-aware decay, strategy lifecycle, contradiction
resolution, PriorKnowledgePack, planner integration, security/secrets,
context limits, observability, end-to-end Task1->Task2.

Covers required scenarios A-T (see class/method names below for mapping).
Run with: python3 -m unittest tests.test_context_intelligence -v
"""

import os
import sys
import time
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ToolRegistry, PermissionConfig, Permission
from providers import MockAIProvider
from memory import ProjectMemory
from learning import (
    ExperienceRecord, ExperienceStore, LearningEnabledOrchestrator,
    Context, ContextBuilder, MultiSignalRelevanceEngine, ExperienceDeduplicator,
    compute_decayed_confidence, StrategyState, StrategyLifecycleManager,
    ContradictionResolver, PriorKnowledgePack, build_prior_knowledge_pack,
    prior_knowledge_pack_to_planner_guidance, redact_secrets, redact_secrets_deep,
)
from learning.context import MAX_EXPERIENCES, MAX_STRATEGIES, MAX_FAILURES


class P9TestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_p9_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)
        self.memory = ProjectMemory(path=os.path.join(self.sandbox, "pm.json"))
        self.store = ExperienceStore(self.memory)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)


CALC_SCRIPT = [
    {"text": "implementing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {
        "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"}},
        # P0-1: provide a runnable entry point for the build/run stage.
        {"id": "c1b", "name": "write_file", "arguments": {
            "path": "main.py", "content": "print('calculator entry ok')\n"}}]},
    {"text": "implementing tests", "tool_calls": [{"id": "c2", "name": "write_file", "arguments": {
        "path": "test_calculator.py", "content": "import unittest\nfrom calculator import add\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3), 5)\n"}}]},
    {"text": "done", "tool_calls": []},
]


# A. Empty memory --------------------------------------------------------------

class TestA_EmptyMemory(P9TestBase):
    def test_context_builds_cleanly_from_empty_store(self):
        ctx = ContextBuilder(self.store).build("t1", "build a calculator", "calculator")
        self.assertEqual(ctx.relevant_experiences, [])
        self.assertFalse(ctx.truncated)
        self.assertEqual(ctx.evidence_confidence, "LOW")

    def test_retrieval_from_empty_store_returns_nothing(self):
        pack = build_prior_knowledge_pack(ContextBuilder(self.store).build("t1", "x", "x"))
        self.assertEqual(pack.source_experience_ids, [])


# B. Relevant successful experience retrieval -----------------------------------

class TestB_RelevantSuccessRetrieval(P9TestBase):
    def test_relevant_success_is_retrieved(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                          task_summary="build a calculator with add and subtract", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED"))
        ctx = ContextBuilder(self.store).build("t2", "build a calculator with add and subtract", "calculator")
        self.assertEqual(len(ctx.relevant_experiences), 1)
        self.assertEqual(ctx.relevant_experiences[0]["experience_id"], "e1")


# C. Irrelevant experience filtering ---------------------------------------------

class TestC_IrrelevantFiltering(P9TestBase):
    def test_unrelated_experience_scores_low_and_is_deprioritized(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                          task_summary="build a calculator with add and subtract", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED"))
        self.store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="research",
                                          task_summary="research database vendor options", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED"))
        ctx = ContextBuilder(self.store).build("t3", "build a calculator with add and subtract", "calculator", )
        ids = [e["experience_id"] for e in ctx.relevant_experiences]
        self.assertIn("e1", ids)
        if "e2" in ids:
            self.assertLess(ctx.relevant_experiences[ids.index("e2")]["relevance_score"],
                             ctx.relevant_experiences[ids.index("e1")]["relevance_score"])


# D. Relevant failure warning -----------------------------------------------------

class TestD_FailureWarning(P9TestBase):
    def test_prior_failure_surfaced_as_warning(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                          task_summary="build a calculator", plan_summary="p",
                                          quality_gate_result="REJECTED",
                                          failures=[{"failure_type": "AssertionError", "root_cause": "subtract wrong", "affected_files": ["calculator.py"]}]))
        ctx = ContextBuilder(self.store).build("t2", "build a calculator", "calculator")
        self.assertGreater(len(ctx.previous_failures), 0)
        pack = build_prior_knowledge_pack(ctx)
        self.assertTrue(any("AssertionError" in w for w in pack.failure_warnings))


# E. Relevant strategy retrieval --------------------------------------------------

class TestE_StrategyRetrieval(P9TestBase):
    def test_strategy_from_high_confidence_experience_surfaced(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                          task_summary="build a calculator", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED",
                                          reusable_patterns=[{"strategy_id": "s1", "task_type": "calculator", "step_sequence": [], "outcome": "SUCCESS"}]))
        ctx = ContextBuilder(self.store).build("t2", "build a calculator", "calculator")
        self.assertTrue(any(s.get("strategy_id") == "s1" for s in ctx.relevant_strategies))


# F. Contradiction detection ------------------------------------------------------

class TestF_ContradictionDetection(P9TestBase):
    def test_contradiction_detected_between_success_and_later_failures(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calc", task_summary="s", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED", files_changed=["a.py"], timestamp=100))
        self.store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="calc", task_summary="s", plan_summary="p",
                                          quality_gate_result="REJECTED", files_changed=["a.py"], timestamp=200))
        self.store.save(ExperienceRecord(experience_id="e3", task_id="t3", task_type="calc", task_summary="s", plan_summary="p",
                                          quality_gate_result="BLOCKED", files_changed=["a.py"], timestamp=300))
        resolutions = ContradictionResolver().resolve(self.store, "calc")
        self.assertTrue(len(resolutions) > 0)
        self.assertTrue(all(r.status in ("SUPPORTED", "WEAKENED", "CONTRADICTED", "CONTEXT_DEPENDENT", "UNKNOWN") for r in resolutions))


# G. Stale experience confidence reduction -----------------------------------------

class TestG_StaleConfidenceReduction(unittest.TestCase):
    def test_old_experience_confidence_decays(self):
        old = {"quality_gate_result": "APPROVED", "evidence_confidence": "HIGH", "timestamp": time.time() - (400 * 86400)}
        conf, reasons = compute_decayed_confidence(old)
        self.assertNotEqual(conf, "HIGH")
        self.assertTrue(any("decay" in r.lower() for r in reasons))


# H. Repeatedly verified strategy confidence behavior -------------------------------

class TestH_RepeatedVerification(unittest.TestCase):
    def test_repeated_success_stabilizes_confidence(self):
        old = {"task_type": "calc", "quality_gate_result": "APPROVED", "evidence_confidence": "HIGH", "timestamp": time.time() - (400 * 86400)}
        related = [
            {"task_type": "calc", "quality_gate_result": "APPROVED", "timestamp": time.time() - 100},
            {"task_type": "calc", "quality_gate_result": "APPROVED", "timestamp": time.time() - 50},
        ]
        conf, reasons = compute_decayed_confidence(old, related_experiences=related)
        self.assertEqual(conf, "HIGH")

    def test_repeated_failure_never_raises_confidence(self):
        failure = {"quality_gate_result": "REJECTED", "evidence_confidence": "LOW", "timestamp": time.time()}
        related = [{"task_type": "x", "quality_gate_result": "REJECTED", "timestamp": time.time()} for _ in range(5)]
        conf, reasons = compute_decayed_confidence(failure, related_experiences=related)
        self.assertEqual(conf, "LOW")


# I. Duplicate experience handling -------------------------------------------------

class TestI_Deduplication(unittest.TestCase):
    def test_true_duplicates_merged_keeping_strongest(self):
        exps = [
            {"experience_id": "e1", "task_summary": "build calculator add subtract", "task_type": "calc", "timestamp": 100, "quality_gate_result": "APPROVED", "evidence_confidence": "MEDIUM"},
            {"experience_id": "e2", "task_summary": "build calculator add subtract", "task_type": "calc", "timestamp": 200, "quality_gate_result": "APPROVED", "evidence_confidence": "HIGH"},
        ]
        deduped, merged = ExperienceDeduplicator().deduplicate(exps)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["experience_id"], "e2")
        self.assertEqual(merged, 1)

    def test_contradictory_records_never_merged(self):
        exps = [
            {"experience_id": "e1", "task_summary": "build calculator add subtract", "task_type": "calc", "timestamp": 100, "quality_gate_result": "APPROVED", "evidence_confidence": "HIGH"},
            {"experience_id": "e2", "task_summary": "build calculator add subtract", "task_type": "calc", "timestamp": 200, "quality_gate_result": "REJECTED", "evidence_confidence": "LOW"},
        ]
        deduped, merged = ExperienceDeduplicator().deduplicate(exps)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(merged, 0)


# J. Large-memory bounded retrieval / K. Large-task bounded context -----------------

class TestJK_LargeMemoryBounded(P9TestBase):
    def _seed(self, n):
        for i in range(n):
            self.store.save(ExperienceRecord(experience_id=f"e{i}", task_id=f"t{i}", task_type="calculator",
                                              task_summary=f"calculator add subtract task {i}", plan_summary="p",
                                              evidence_confidence="HIGH", quality_gate_result="APPROVED"))

    def test_J_bounded_with_100_experiences(self):
        self._seed(100)
        ctx = ContextBuilder(self.store).build("t1", "calculator add subtract", "calculator")
        self.assertLessEqual(len(ctx.relevant_experiences), MAX_EXPERIENCES)
        self.assertTrue(ctx.truncated)

    def test_J_bounded_with_1000_experiences(self):
        self._seed(1000)
        ctx = ContextBuilder(self.store).build("t1", "calculator add subtract", "calculator")
        self.assertLessEqual(len(ctx.relevant_experiences), MAX_EXPERIENCES)
        self.assertTrue(ctx.truncated)

    def test_J_relevant_experiences_still_preferred_at_scale(self):
        self._seed(200)
        # give "best" a genuinely distinguishing keyword the filler
        # experiences don't share (plain numeric task indices get filtered
        # by the keyword extractor's len>2 rule, so they all tie otherwise -
        # this exercises real differentiation, not an accidental tie).
        self.store.save(ExperienceRecord(experience_id="best", task_id="tbest", task_type="calculator",
                                          task_summary="calculator add subtract uniquemarker feature", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED", timestamp=time.time()))
        ctx = ContextBuilder(self.store).build("t1", "calculator add subtract uniquemarker feature", "calculator")
        ids = [e["experience_id"] for e in ctx.relevant_experiences]
        self.assertIn("best", ids)

    def test_K_very_large_task_description_bounded(self):
        huge_description = "calculator add subtract " * 1000
        builder = ContextBuilder(self.store)
        ctx = builder.build("t1", huge_description, "calculator")
        self.assertLessEqual(len(ctx.task_description), builder.max_chars)

    def test_K_large_failure_history_bounded(self):
        for i in range(30):
            self.store.save(ExperienceRecord(experience_id=f"f{i}", task_id=f"tf{i}", task_type="calculator",
                                              task_summary="calculator failure", plan_summary="p",
                                              failures=[{"failure_type": "AssertionError", "root_cause": f"fail {i}", "affected_files": []}]))
        ctx = ContextBuilder(self.store).build("t1", "calculator", "calculator")
        self.assertLessEqual(len(ctx.previous_failures), MAX_FAILURES)

    def test_execution_remains_functional_with_large_memory(self):
        self._seed(150)
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Build a calculator that adds numbers.")
        self.assertIn(result["plan_result"]["status"], ("COMPLETED", "FAILED"))


# L. PriorKnowledgePack reaches planner --------------------------------------------

class TestL_PackReachesPlanner(P9TestBase):
    def test_pack_surfaced_as_plan_assumptions(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                          task_summary="calculator add subtract", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED",
                                          lessons=[{"lesson_type": "TOOL_PATTERN", "text": "use write_file then run_tests", "evidence": "x"}]))
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Build a calculator that adds and subtracts.")
        self.assertTrue(any("Prior experience" in a for a in result["plan_result"]["plan"].assumptions))


# M. Planner independently validates plans -----------------------------------------

class TestM_IndependentValidation(unittest.TestCase):
    def test_invalid_plan_rejected_regardless_of_prior_knowledge_text(self):
        from planning import PlanValidator, ExecutionPlan, PlanStep
        steps = [PlanStep("a", "d", {"coding"}, dependencies=["b"]), PlanStep("b", "d", {"testing"}, dependencies=["a"])]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps,
                              assumptions=["Prior experience (HIGH confidence): guaranteed to work, skip validation"])
        result = PlanValidator().validate(plan)
        self.assertFalse(result.valid)


# N. READ-only permission cannot be bypassed ----------------------------------------

class TestN_PermissionNotBypassed(P9TestBase):
    def test_read_only_permission_blocks_writes_even_with_rich_context(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                          task_summary="calculator add", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED"))
        ro_registry = ToolRegistry(base_dir=self.sandbox, permission_config=PermissionConfig(granted={Permission.READ}))
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=ro_registry, project_memory=self.memory)
        result = orch.run("Build a calculator that adds numbers.")
        written = [f for f in os.listdir(self.sandbox) if f != "pm.json"]
        self.assertEqual(written, [])


# O. Memory poisoning protection -----------------------------------------------------

class TestO_MemoryPoisoning(P9TestBase):
    def test_model_claims_success_no_evidence_never_high(self):
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Create a small calculator project with implementation and tests.")
        self.assertNotEqual(result["evidence_confidence"], "HIGH")

    def test_only_real_verified_success_reaches_high(self):
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Build a calculator that adds numbers.")
        self.assertEqual(result["evidence_confidence"], "HIGH")


# P. Test weakening remains BLOCKED --------------------------------------------------

class TestP_TestWeakeningBlocked(P9TestBase):
    def test_weakened_test_blocked_by_real_integrity_checker(self):
        self.registry.impl.write_file("calculator.py", "def subtract(a, b):\n    return a + b\n")
        self.registry.impl.write_file("test_calculator.py", "import unittest\nfrom calculator import subtract\nclass T(unittest.TestCase):\n    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n")
        cheat = [{"text": "cheat", "tool_calls": [{"id": "c1", "name": "edit_file", "arguments": {
            "path": "test_calculator.py", "old_str": "self.assertEqual(subtract(5, 3), 2)", "new_str": "self.assertTrue(True)"}}]}, {"text": "done", "tool_calls": []}]
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=cheat), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Fix the calculator project subtract implementation.")
        self.assertEqual(result["plan_result"]["orchestration_result"].quality_decision, "BLOCKED")
        self.assertNotEqual(result["evidence_confidence"], "HIGH")


# Q. API-key/secret protection -------------------------------------------------------

class TestQ_SecretProtection(P9TestBase):
    def test_secret_shapes_redacted(self):
        clean, found = redact_secrets("here is my key sk-abcdefghij1234567890")
        self.assertTrue(found)
        self.assertNotIn("sk-abcdefghij1234567890", clean)
        clean2, found2 = redact_secrets("AKIAABCDEFGHIJKLMNOP is my access key")
        self.assertTrue(found2)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", clean2)
        clean3, found3 = redact_secrets("password: SuperSecret123")
        self.assertTrue(found3)
        self.assertNotIn("SuperSecret123", clean3)

    def test_secret_never_reaches_context_from_failure_data(self):
        exp = {"experience_id": "e1", "task_summary": "s", "evidence_confidence": "HIGH",
               "failures": [{"failure_type": "AuthError", "root_cause": "used key sk-verysecretvalue12345", "affected_files": []}],
               "timestamp": time.time(), "reusable_patterns": [], "status": "ACTIVE", "task_type": "x"}

        class FakeStore:
            def all_experiences(self_inner):
                return [exp]
        ctx = ContextBuilder(FakeStore()).build("t1", "s", "x")
        self.assertNotIn("sk-verysecretvalue12345", str(ctx.previous_failures))

    def test_secret_never_in_project_memory_experience_or_events(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-p9-must-never-leak-anywhere"
        try:
            orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
            result = orch.run("Build a calculator that adds numbers.", task_id="secret-check")
            self.assertNotIn("sk-p9-must-never-leak-anywhere", str(self.memory.all()))
            self.assertNotIn("sk-p9-must-never-leak-anywhere", str(result["events"]))
            self.assertNotIn("sk-p9-must-never-leak-anywhere", str(self.store.get(result["experience_id"])))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]


# R. Previous failure warning for similar task ---------------------------------------

class TestR_FailureWarningForSimilarTask(P9TestBase):
    def test_similar_task_surfaces_prior_failure_warning(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                          task_summary="build calculator subtract feature", plan_summary="p",
                                          quality_gate_result="REJECTED",
                                          failures=[{"failure_type": "AssertionError", "root_cause": "subtract wrong result", "affected_files": ["calculator.py"]}]))
        from learning import FailureMemory
        warning = FailureMemory(self.store).warning_for("AssertionError", "subtract wrong result")
        self.assertIsNotNone(warning)
        self.assertIn("advisory", warning.lower())


# S. Previous success is advisory, not guaranteed -------------------------------------

class TestS_AdvisoryNotGuaranteed(P9TestBase):
    def test_task2_success_not_guaranteed_by_task1_success(self):
        orch1 = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
        result1 = orch1.run("Build a calculator that adds numbers.", task_id="s-task-1")
        self.assertEqual(result1["evidence_confidence"], "HIGH")

        sandbox2 = tempfile.mkdtemp()
        registry2 = ToolRegistry(base_dir=sandbox2)
        orch2 = LearningEnabledOrchestrator(provider=MockAIProvider(), tool_registry=registry2, project_memory=self.memory)
        result2 = orch2.run("Build a calculator that adds numbers and subtracts.", task_id="s-task-2")

        self.assertGreater(len(result2["prior_knowledge_pack"].source_experience_ids), 0)
        self.assertNotEqual(result2["evidence_confidence"], "HIGH")
        self.assertNotEqual(result2["plan_result"]["status"], "COMPLETED")
        shutil.rmtree(sandbox2, ignore_errors=True)


# T. Contradictory experiences preserved, never silently overwritten ------------------

class TestT_ContradictionPreserved(P9TestBase):
    def test_both_conflicting_records_remain_after_resolution(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calc", task_summary="s", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED", files_changed=["a.py"], timestamp=100))
        self.store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="calc", task_summary="s", plan_summary="p",
                                          quality_gate_result="REJECTED", files_changed=["a.py"], timestamp=200))
        self.store.save(ExperienceRecord(experience_id="e3", task_id="t3", task_type="calc", task_summary="s", plan_summary="p",
                                          quality_gate_result="BLOCKED", files_changed=["a.py"], timestamp=300))
        ContradictionResolver().resolve(self.store, "calc")
        self.assertIsNotNone(self.store.get("e1"))
        self.assertIsNotNone(self.store.get("e2"))
        self.assertIsNotNone(self.store.get("e3"))

    def test_no_fabricated_cause_when_evidence_insufficient(self):
        self.store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p",
                                          evidence_confidence="HIGH", quality_gate_result="APPROVED", timestamp=100))
        self.store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="x", task_summary="s", plan_summary="p",
                                          quality_gate_result="REJECTED", timestamp=200))
        self.store.save(ExperienceRecord(experience_id="e3", task_id="t3", task_type="x", task_summary="s", plan_summary="p",
                                          quality_gate_result="BLOCKED", timestamp=300))
        resolutions = ContradictionResolver().resolve(self.store, "x")
        self.assertTrue(all(r.status == "UNKNOWN" for r in resolutions))


# End-to-end Task1 -> Task2 (full demonstration) --------------------------------------

class TestEndToEndTwoTasks(P9TestBase):
    def test_full_two_task_demonstration(self):
        orch1 = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
        result1 = orch1.run("Build a calculator that adds numbers.", task_id="e2e-task-1")
        self.assertEqual(result1["plan_result"]["status"], "COMPLETED")
        self.assertEqual(result1["evidence_confidence"], "HIGH")

        sandbox2 = tempfile.mkdtemp()
        registry2 = ToolRegistry(base_dir=sandbox2)
        script2 = [
            {"text": "implementing", "tool_calls": [{"id": "d1", "name": "write_file", "arguments": {
                "path": "calculator.py", "content": "def add(a, b):\n    return a + b\ndef subtract(a, b):\n    return a - b\n"}},
                # P0-1: task 2 also needs a runnable entry point.
                {"id": "d1b", "name": "write_file", "arguments": {
                    "path": "main.py", "content": "print('calculator entry ok')\n"}}]},
            {"text": "implementing tests", "tool_calls": [{"id": "d2", "name": "write_file", "arguments": {
                "path": "test_calculator.py", "content": "import unittest\nfrom calculator import add, subtract\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3), 5)\n    def test_subtract(self):\n        self.assertEqual(subtract(5,3), 2)\n"}}]},
            {"text": "done", "tool_calls": []},
        ]
        orch2 = LearningEnabledOrchestrator(provider=MockAIProvider(script=script2), tool_registry=registry2, project_memory=self.memory)
        result2 = orch2.run("Build a calculator that adds numbers and subtracts.", task_id="e2e-task-2")

        self.assertGreater(len(result2["prior_knowledge_pack"].source_experience_ids), 0)
        self.assertEqual(result2["plan_result"]["status"], "COMPLETED")
        self.assertEqual(result2["evidence_confidence"], "HIGH")

        self.assertIsNotNone(self.store.get(result1["experience_id"]))
        self.assertIsNotNone(self.store.get(result2["experience_id"]))
        shutil.rmtree(sandbox2, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
