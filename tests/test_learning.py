"""
Phase 8 tests: experience creation/validation, lesson/strategy extraction,
relevance ranking, confidence, staleness, contradiction detection,
retrieval, planner integration, memory-poisoning defense, security.
Run with: python3 -m unittest tests.test_learning -v
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
    ExperienceRecord, ExperienceValidator, LessonExtractor, StrategyExtractor,
    ExperienceStore, ExperienceRelevanceEngine, check_staleness, ContradictionDetector,
    KnowledgeRetriever, FailureMemory, LearningEnabledOrchestrator, KnowledgeConfidence, KnowledgeStatus,
)


class LearningTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_learning_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)
        self.memory = ProjectMemory(path=os.path.join(self.sandbox, "pm.json"))

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def seed_calculator(self):
        self.registry.impl.write_file("calculator.py", "def add(a, b):\n    return a + b\n")
        self.registry.impl.write_file("test_calculator.py", (
            "import unittest\nfrom calculator import add\n"
            "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3), 5)\n"
        ))
        # P0-1: build/run stage needs a runnable entry point.
        self.registry.impl.write_file("main.py", "print('calculator entry ok')\n")


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


# 1-6. Experience schema, creation, validation, success/failure storage --------

class TestExperienceSchema(unittest.TestCase):
    def test_record_constructs_with_required_fields(self):
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                task_summary="do a thing", plan_summary="2 steps")
        self.assertEqual(rec.status, "ACTIVE")
        self.assertEqual(rec.evidence_confidence, "LOW")

    def test_new_id_is_unique(self):
        self.assertNotEqual(ExperienceRecord.new_id(), ExperienceRecord.new_id())


class TestExperienceValidator(unittest.TestCase):
    def test_approved_gate_with_passing_tests_is_high(self):
        conf, reasons = ExperienceValidator().validate({
            "tests_passed": True, "quality_gate_result": "APPROVED", "required_files_exist": True, "integrity_ok": True,
        })
        self.assertEqual(conf, KnowledgeConfidence.HIGH.value)

    def test_rejected_gate_is_never_high(self):
        conf, reasons = ExperienceValidator().validate({
            "tests_passed": True, "quality_gate_result": "REJECTED", "integrity_ok": True,
        })
        self.assertNotEqual(conf, KnowledgeConfidence.HIGH.value)

    def test_integrity_violation_forces_low_regardless_of_other_signals(self):
        conf, reasons = ExperienceValidator().validate({
            "tests_passed": True, "quality_gate_result": "APPROVED", "integrity_ok": False,
        })
        self.assertEqual(conf, KnowledgeConfidence.LOW.value)

    def test_permission_denied_forces_low(self):
        conf, reasons = ExperienceValidator().validate({
            "tests_passed": True, "quality_gate_result": "APPROVED", "integrity_ok": True, "permission_denied_occurred": True,
        })
        self.assertEqual(conf, KnowledgeConfidence.LOW.value)

    def test_tests_failed_is_low(self):
        conf, reasons = ExperienceValidator().validate({"tests_passed": False, "integrity_ok": True})
        self.assertEqual(conf, KnowledgeConfidence.LOW.value)

    def test_is_reusable_as_positive_knowledge_only_for_high(self):
        v = ExperienceValidator()
        self.assertTrue(v.is_reusable_as_positive_knowledge("HIGH"))
        self.assertFalse(v.is_reusable_as_positive_knowledge("MEDIUM"))
        self.assertFalse(v.is_reusable_as_positive_knowledge("LOW"))


class TestExperienceStore(LearningTestBase):
    def test_save_and_get(self):
        store = ExperienceStore(self.memory)
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                task_summary="s", plan_summary="p", evidence_confidence="HIGH")
        store.save(rec)
        retrieved = store.get("e1")
        self.assertEqual(retrieved["task_id"], "t1")

    def test_search_by_task_type(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator", task_summary="s", plan_summary="p"))
        store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="research", task_summary="s", plan_summary="p"))
        results = store.search(task_type="calculator")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["experience_id"], "e1")

    def test_search_by_success(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p", quality_gate_result="APPROVED"))
        store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="x", task_summary="s", plan_summary="p", quality_gate_result="REJECTED"))
        successes = store.search(success=True)
        failures = store.search(success=False)
        self.assertEqual([e["experience_id"] for e in successes], ["e1"])
        self.assertEqual([e["experience_id"] for e in failures], ["e2"])

    def test_recent_orders_by_timestamp(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p", timestamp=100))
        store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="x", task_summary="s", plan_summary="p", timestamp=200))
        recent = store.recent(n=2)
        self.assertEqual(recent[0]["experience_id"], "e2")


# 7. API-key / secret exclusion ------------------------------------------------

class TestSecretExclusion(LearningTestBase):
    def test_experience_record_never_asked_to_hold_api_key(self):
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p")
        self.assertFalse(hasattr(rec, "api_key"))
        self.assertNotIn("api_key", rec.__dataclass_fields__)

    def test_stored_experience_never_contains_api_key_value(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-should-never-appear"
        try:
            store = ExperienceStore(self.memory)
            rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p")
            store.save(rec)
            self.assertNotIn("sk-should-never-appear", str(store.get("e1")))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]


# 8-9. Lesson and strategy extraction ------------------------------------------

class TestLessonExtractor(unittest.TestCase):
    def test_failure_lesson_is_specific_not_vague(self):
        rec = ExperienceRecord(
            experience_id="e1", task_id="t1", task_type="x", task_summary="fix subtract",
            plan_summary="p", failures=[{"failure_type": "AssertionError", "root_cause": "8 != 2", "affected_files": ["calculator.py"]}],
        )
        lessons = LessonExtractor().extract(rec)
        self.assertTrue(any("calculator.py" in l.text for l in lessons))
        for l in lessons:
            self.assertNotIn("try harder", l.text.lower())
            self.assertNotIn("be more careful", l.text.lower())

    def test_no_lessons_from_empty_record(self):
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p")
        self.assertEqual(LessonExtractor().extract(rec), [])

    def test_tool_pattern_lesson_only_when_approved(self):
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p",
                                quality_gate_result="APPROVED", tools_used=["write_file", "run_tests"])
        lessons = LessonExtractor().extract(rec)
        self.assertTrue(any(l.lesson_type == "TOOL_PATTERN" for l in lessons))


class TestStrategyExtractor(unittest.TestCase):
    def test_no_strategy_extracted_below_high_confidence(self):
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p",
                                evidence_confidence="MEDIUM", workers_used=["coding_worker"])
        self.assertIsNone(StrategyExtractor().extract(rec))

    def test_strategy_extracted_for_high_confidence(self):
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator", task_summary="s", plan_summary="p",
                                evidence_confidence="HIGH", workers_used=["coding_worker", "testing_worker"], tools_used=["write_file"])
        strategy = StrategyExtractor().extract(rec)
        self.assertIsNotNone(strategy)
        self.assertEqual(strategy.outcome, "SUCCESS")
        self.assertEqual(strategy.task_type, "calculator")


# 10-11. Relevance ranking and confidence --------------------------------------

class TestRelevanceEngine(unittest.TestCase):
    def test_shared_keywords_score_higher_than_unrelated(self):
        exps = [
            {"experience_id": "e1", "task_summary": "Create a calculator with add and subtract", "task_type": "calculator", "timestamp": time.time(), "evidence_confidence": "HIGH"},
            {"experience_id": "e2", "task_summary": "Research database options", "task_type": "research", "timestamp": time.time(), "evidence_confidence": "HIGH"},
        ]
        ranked = ExperienceRelevanceEngine().rank("Build a calculator that adds and subtracts numbers", "calculator", exps, top_n=2)
        self.assertEqual(ranked[0].experience_id, "e1")

    def test_explanation_is_present_and_labeled_heuristic(self):
        exps = [{"experience_id": "e1", "task_summary": "calculator add", "task_type": "calculator", "timestamp": time.time(), "evidence_confidence": "HIGH"}]
        ranked = ExperienceRelevanceEngine().rank("calculator add", "calculator", exps)
        self.assertEqual(ranked[0].method, "keyword_overlap_heuristic")
        self.assertIn("shared_keywords", ranked[0].explanation)

    def test_empty_experience_list_returns_empty(self):
        self.assertEqual(ExperienceRelevanceEngine().rank("anything", "x", []), [])


# 12. Model confidence vs evidence confidence stay separate --------------------

class TestConfidenceSeparation(unittest.TestCase):
    def test_model_confidence_never_influences_evidence_confidence(self):
        # even with model_confidence=1.0 (maximal), evidence says tests failed -> LOW
        conf, reasons = ExperienceValidator().validate({"tests_passed": False, "integrity_ok": True})
        self.assertEqual(conf, "LOW")
        rec = ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p",
                                model_confidence=1.0, evidence_confidence=conf)
        self.assertEqual(rec.evidence_confidence, "LOW")
        self.assertEqual(rec.model_confidence, 1.0)  # recorded, but never equal to evidence_confidence


# 13. Staleness -----------------------------------------------------------------

class TestStaleness(unittest.TestCase):
    def test_recent_experience_is_active(self):
        exp = {"timestamp": time.time(), "status": "ACTIVE"}
        self.assertEqual(check_staleness(exp), "ACTIVE")

    def test_old_experience_becomes_stale(self):
        exp = {"timestamp": time.time() - (60 * 60 * 24 * 200), "status": "ACTIVE"}
        self.assertEqual(check_staleness(exp), "STALE")

    def test_staleness_never_deletes_only_relabels(self):
        exp = {"timestamp": time.time() - (60 * 60 * 24 * 200), "status": "ACTIVE"}
        status = check_staleness(exp)
        self.assertIn(status, ("STALE", "ACTIVE", "INVALID", "SUPERSEDED"))  # still a valid, present status


# 14. Contradiction detection ----------------------------------------------------

class TestContradictionDetection(LearningTestBase):
    def test_no_contradiction_with_no_experiences(self):
        store = ExperienceStore(self.memory)
        result = ContradictionDetector().check(store, "calculator")
        self.assertFalse(result.conflicting)

    def test_contradiction_detected_after_repeated_failures_post_success(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator", task_summary="s", plan_summary="p",
                                     evidence_confidence="HIGH", timestamp=100))
        store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="calculator", task_summary="s", plan_summary="p",
                                     quality_gate_result="REJECTED", timestamp=200))
        store.save(ExperienceRecord(experience_id="e3", task_id="t3", task_type="calculator", task_summary="s", plan_summary="p",
                                     quality_gate_result="BLOCKED", timestamp=300))
        result = ContradictionDetector().check(store, "calculator")
        self.assertTrue(result.conflicting)
        self.assertIn("e2", result.conflicting_experience_ids)

    def test_mark_superseded_never_deletes_record(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="x", task_summary="s", plan_summary="p"))
        ContradictionDetector().mark_superseded(store, "e1", "reason")
        entry = store.get("e1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "SUPERSEDED")


# 15-16. Knowledge retrieval, invalid knowledge handling -----------------------

class TestKnowledgeRetriever(LearningTestBase):
    def test_retrieval_excludes_stale(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator", task_summary="calculator add",
                                     plan_summary="p", evidence_confidence="HIGH", status="STALE"))
        guidance = KnowledgeRetriever(store).retrieve("calculator add", "calculator")
        self.assertEqual(guidance, [])

    def test_retrieval_excludes_superseded_and_invalid(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator", task_summary="calculator add",
                                     plan_summary="p", evidence_confidence="HIGH", status="SUPERSEDED"))
        store.save(ExperienceRecord(experience_id="e2", task_id="t2", task_type="calculator", task_summary="calculator add",
                                     plan_summary="p", evidence_confidence="HIGH", status="INVALID"))
        guidance = KnowledgeRetriever(store).retrieve("calculator add", "calculator")
        self.assertEqual(guidance, [])

    def test_retrieval_returns_concise_not_full_dump(self):
        store = ExperienceStore(self.memory)
        for i in range(10):
            store.save(ExperienceRecord(experience_id=f"e{i}", task_id=f"t{i}", task_type="calculator",
                                         task_summary="calculator add subtract", plan_summary="p", evidence_confidence="HIGH"))
        guidance = KnowledgeRetriever(store).retrieve("calculator add", "calculator", top_n=3)
        self.assertLessEqual(len(guidance), 3)


class TestFailureMemory(LearningTestBase):
    def test_find_similar_failure(self):
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator", task_summary="s", plan_summary="p",
                                     failures=[{"failure_type": "AssertionError", "root_cause": "subtract returned wrong value"}]))
        matches = FailureMemory(store).find_similar("AssertionError", "subtract returned wrong value")
        self.assertTrue(len(matches) >= 1)
        self.assertIn("subtract", " ".join(matches[0]["shared_keywords"]))


# 17-18. Planner integration, failure avoidance, strategy reuse ----------------

class TestPlannerIntegration(LearningTestBase):
    def test_prior_knowledge_surfaced_as_assumptions_not_authority(self):
        self.seed_calculator()
        store = ExperienceStore(self.memory)
        store.save(ExperienceRecord(experience_id="e1", task_id="t1", task_type="calculator",
                                     task_summary="calculator add subtract", plan_summary="p", evidence_confidence="HIGH",
                                     lessons=[{"lesson_type": "TOOL_PATTERN", "text": "use write_file then run_tests", "evidence": "x"}]))
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Build a calculator that adds and subtracts.")
        self.assertTrue(any("Prior experience" in a for a in result["plan_result"]["plan"].assumptions))
        # validation/execution still ran independently, unaffected by the presence of prior knowledge
        self.assertIn(result["plan_result"]["status"], ("COMPLETED", "FAILED"))

    def test_prior_knowledge_cannot_bypass_plan_validator(self):
        # even with maximally-confident prior knowledge present, an invalid
        # plan built independently would still be rejected - the validator
        # never reads plan.assumptions.
        from planning import PlanValidator, ExecutionPlan, PlanStep
        steps = [PlanStep("a", "d", {"coding"}, dependencies=["b"]), PlanStep("b", "d", {"testing"}, dependencies=["a"])]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps,
                              assumptions=["Prior experience (HIGH confidence): trust me it works"])
        result = PlanValidator().validate(plan)
        self.assertFalse(result.valid)  # circular dependency still caught regardless of assumptions text


# 19-20. Memory poisoning defense (scenarios D & E) -----------------------------

class TestMemoryPoisoningDefense(LearningTestBase):
    def test_scenario_D_model_claims_success_but_no_evidence(self):
        # no script -> CodingAgentWorker reports COMPLETED with zero tool
        # calls; nothing is actually created.
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Create a small calculator project with implementation and tests.")
        self.assertNotEqual(result["evidence_confidence"], "HIGH")

    def test_scenario_E_test_weakening_blocked(self):
        self.registry.impl.write_file("calculator.py", "def subtract(a, b):\n    return a + b\n")
        self.registry.impl.write_file("test_calculator.py", "import unittest\nfrom calculator import subtract\nclass T(unittest.TestCase):\n    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n")
        cheat_script = [
            {"text": "cheating", "tool_calls": [{"id": "c1", "name": "edit_file", "arguments": {
                "path": "test_calculator.py", "old_str": "self.assertEqual(subtract(5, 3), 2)", "new_str": "self.assertTrue(True)"}}]},
            {"text": "done", "tool_calls": []},
        ]
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=cheat_script), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Fix the calculator project subtract implementation.")
        self.assertNotEqual(result["evidence_confidence"], "HIGH")
        self.assertEqual(result["plan_result"]["orchestration_result"].quality_decision, "BLOCKED")

    def test_only_real_verified_success_reaches_high(self):
        self.seed_calculator()
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Build a calculator with an add function.")
        self.assertEqual(result["evidence_confidence"], "HIGH")


# 21. Permission enforcement -----------------------------------------------------

class TestPermissionEnforcement(LearningTestBase):
    def test_read_only_permission_blocks_writes_through_learning_layer(self):
        read_only = ToolRegistry(base_dir=self.sandbox, permission_config=PermissionConfig(granted={Permission.READ}))
        memory = ProjectMemory(path=os.path.join(self.sandbox, "pm2.json"))
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=read_only, project_memory=memory)
        result = orch.run("Create a small calculator project with implementation and tests.")
        # pm2.json is the ProjectMemory's own persistence file (written directly
        # by ProjectMemory, not through the read-only ToolRegistry) - the real
        # thing being checked is that no PROJECT file was written via the
        # permission-gated tool path.
        self.assertEqual([f for f in os.listdir(self.sandbox) if f != "pm2.json"], [])
        self.assertNotEqual(result["evidence_confidence"], "HIGH")


# 22-24. Quality Gate / test-integrity compatibility, Phase 1-7 compatibility --

class TestCompatibility(LearningTestBase):
    def test_quality_gate_still_authoritative(self):
        orch = LearningEnabledOrchestrator(provider=MockAIProvider(), tool_registry=self.registry, project_memory=self.memory)
        result = orch.run("Create a small calculator project with implementation and tests.")
        # nothing was created -> cannot be approved regardless of learning layer
        self.assertNotEqual(result["plan_result"]["status"], "COMPLETED")

    def test_phase7_planned_orchestrator_still_works_standalone(self):
        from planning import PlannedOrchestrator
        self.seed_calculator()
        po = PlannedOrchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        result = po.run("Build a small calculator project.")
        self.assertIn(result["status"], ("COMPLETED", "FAILED"))

    def test_original_main_entrypoint_unaffected(self):
        # confirms Phase 8 didn't touch omega_core's own flow
        from omega_core import OmegaCore
        core = OmegaCore(project_memory_path=os.path.join(self.sandbox, "omega_pm.json"))
        result = core.handle_request("Create a simple web application", verbose=False)
        self.assertIn(result["status"], ("COMPLETED", "FAILED"))


if __name__ == "__main__":
    unittest.main()
