"""
Phase 7 tests: plan schema, validation (circular/missing/duplicate deps,
capability/permission checks), optimization, complexity/resource
estimation, risk analysis, ambiguity detection, revision, diff,
milestones/checkpoints, planner/orchestrator/quality-gate integration.
Run with: python3 -m unittest tests.test_planning -v
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
from planning import (
    ExecutionPlan, PlanStep, PlanValidator, PlanOptimizer, estimate_complexity, estimate_resources,
    RiskAnalyzer, AmbiguityDetector, AdvancedPlanner, diff_plans, PlanReviser, CheckpointManager,
    PlannedOrchestrator, ComplexityLevel,
)


class PlanningTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_planning_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)


CALC_SCRIPT = [
    {"text": "implementing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {
        "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"}},
        # P0-1: build plans end with a build/run stage - the script must
        # produce a runnable project entry point.
        {"id": "c1b", "name": "write_file", "arguments": {
            "path": "main.py", "content": "print('calculator entry ok')\n"}}]},
    {"text": "implementing tests", "tool_calls": [{"id": "c2", "name": "write_file", "arguments": {
        "path": "test_calculator.py", "content": "import unittest\nfrom calculator import add\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3), 5)\n"}}]},
    {"text": "done", "tool_calls": []},
]


# 1. Plan schema -------------------------------------------------------------

class TestPlanSchema(unittest.TestCase):
    def test_execution_plan_and_step_construct(self):
        step = PlanStep(step_id="s1", description="do a thing", capabilities={"coding"})
        plan = ExecutionPlan(plan_id="p1", task_id="t1", objective="obj", steps=[step])
        self.assertEqual(plan.version, 1)
        self.assertEqual(plan.steps[0].step_id, "s1")


# 2-7. Plan validation -----------------------------------------------------

class TestPlanValidator(unittest.TestCase):
    def test_valid_plan_passes(self):
        steps = [PlanStep("a", "d", {"coding"}), PlanStep("b", "d", {"testing"}, dependencies=["a"])]
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps))
        self.assertTrue(result.valid)

    def test_circular_dependency_detected(self):
        steps = [PlanStep("a", "d", {"coding"}, dependencies=["b"]), PlanStep("b", "d", {"testing"}, dependencies=["a"])]
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps))
        self.assertFalse(result.valid)
        self.assertTrue(any(i.check == "circular_dependency" for i in result.issues))

    def test_missing_dependency_detected(self):
        steps = [PlanStep("a", "d", {"coding"}, dependencies=["ghost"])]
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps))
        self.assertFalse(result.valid)
        self.assertTrue(any(i.check == "missing_dependency" for i in result.issues))

    def test_duplicate_step_id_detected(self):
        steps = [PlanStep("a", "d1", {"coding"}), PlanStep("a", "d2", {"testing"})]
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps))
        self.assertFalse(result.valid)
        self.assertTrue(any(i.check == "duplicate_step_id" for i in result.issues))

    def test_empty_plan_detected(self):
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=[]))
        self.assertFalse(result.valid)
        self.assertEqual(result.issues[0].check, "empty_plan")

    def test_invalid_capability_detected(self):
        steps = [PlanStep("a", "d", {"time_travel"})]
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps))
        self.assertFalse(result.valid)
        self.assertTrue(any(i.check == "invalid_capability" for i in result.issues))

    def test_invalid_permission_detected(self):
        steps = [PlanStep("a", "d", {"coding"})]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps, required_permissions={"SUDO_EVERYTHING"})
        result = PlanValidator().validate(plan)
        self.assertFalse(result.valid)
        self.assertTrue(any(i.check == "invalid_permission" for i in result.issues))

    def test_self_dependency_detected(self):
        steps = [PlanStep("a", "d", {"coding"}, dependencies=["a"])]
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps))
        self.assertFalse(result.valid)
        self.assertTrue(any(i.check == "impossible_dependency" for i in result.issues))

    def test_malformed_acceptance_criteria_detected(self):
        steps = [PlanStep("a", "d", {"coding"}, acceptance_criteria=["", "  "])]
        result = PlanValidator().validate(ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps))
        self.assertFalse(result.valid)
        self.assertTrue(any(i.check == "malformed_acceptance_criteria" for i in result.issues))


# 8-9. Complexity and resource estimation --------------------------------------

class TestComplexityEstimation(unittest.TestCase):
    def test_single_step_is_low(self):
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=[PlanStep("a", "d", {"coding"})])
        self.assertEqual(estimate_complexity(plan), ComplexityLevel.LOW.value)

    def test_many_steps_with_debugging_is_high_or_very_high(self):
        steps = [PlanStep(f"s{i}", "d", {"coding", "testing", "debugging"}, dependencies=[f"s{i-1}"] if i else [])
                 for i in range(8)]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps)
        self.assertIn(estimate_complexity(plan), (ComplexityLevel.HIGH.value, ComplexityLevel.VERY_HIGH.value))

    def test_resource_estimate_labeled_as_estimate_only(self):
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=[PlanStep("a", "d", {"coding"})])
        estimate = estimate_resources(plan)
        self.assertIn("estimate", estimate.note.lower())
        # the note explicitly disclaims certainty ("not guarantees") -
        # it must never assert the estimate IS a guarantee.
        self.assertNotIn("guaranteed", estimate.note.lower())
        self.assertIn("not guarantee", estimate.note.lower())


# 10. Risk analysis --------------------------------------------------------------

class TestRiskAnalyzer(unittest.TestCase):
    def test_destructive_operation_flagged(self):
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="delete all old log files", steps=[PlanStep("a", "d", {"coding"})])
        risks = RiskAnalyzer().analyze(plan)
        self.assertTrue(any(r.category == "destructive_operation" for r in risks))

    def test_missing_tests_flagged_when_coding_without_testing(self):
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="build a script", steps=[PlanStep("a", "d", {"coding"})])
        risks = RiskAnalyzer().analyze(plan)
        self.assertTrue(any(r.category == "missing_tests" for r in risks))

    def test_no_missing_tests_risk_when_testing_present(self):
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="build a script", steps=[
            PlanStep("a", "d", {"coding"}), PlanStep("b", "d", {"testing"}, dependencies=["a"])])
        risks = RiskAnalyzer().analyze(plan)
        self.assertFalse(any(r.category == "missing_tests" for r in risks))

    def test_repeated_repair_history_flagged(self):
        pm = ProjectMemory(path=os.path.join(tempfile.mkdtemp(), "pm.json"))
        pm.remember("repair_session:t1-attempt1", {"x": 1})
        pm.remember("repair_session:t1-attempt2", {"x": 2})
        plan = ExecutionPlan(plan_id="p", task_id="t1", objective="fix it", steps=[PlanStep("a", "d", {"coding"})])
        risks = RiskAnalyzer().analyze(plan, project_memory=pm)
        self.assertTrue(any(r.category == "repeated_repair_history" for r in risks))


# 11. Ambiguity detection ---------------------------------------------------------

class TestAmbiguityDetector(unittest.TestCase):
    def test_vague_objective_flagged_ambiguous(self):
        result = AmbiguityDetector().detect("Make the app better.")
        self.assertTrue(result.ambiguous)
        self.assertTrue(len(result.missing_information) > 0)

    def test_no_fabricated_requirements_when_ambiguous(self):
        result = AmbiguityDetector().detect("Make the app better.")
        # assumptions must NOT invent scope - only note that clarification is needed
        self.assertTrue(all("assum" in a.lower() or "clarif" in a.lower() or "deferred" in a.lower() for a in result.assumptions))

    def test_concrete_objective_not_ambiguous(self):
        result = AmbiguityDetector().detect("Create a calculator.py with add and subtract functions and tests.")
        self.assertFalse(result.ambiguous)


# 12-13. Plan optimization --------------------------------------------------------

class TestPlanOptimizer(unittest.TestCase):
    def test_independent_steps_identified(self):
        steps = [PlanStep("a", "d", {"research"}), PlanStep("b", "d", {"research"})]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps)
        optimized = PlanOptimizer().optimize(plan)
        self.assertEqual(set(optimized.independent_steps), {"a", "b"})

    def test_parallel_group_detected_for_shared_dependency_set(self):
        steps = [PlanStep("a", "d", {"coding"}), PlanStep("b", "run tests", {"testing"}, dependencies=["a"]),
                 PlanStep("c", "run other tests", {"testing"}, dependencies=["a"])]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps)
        optimized = PlanOptimizer().optimize(plan)
        self.assertTrue(any(set(g) == {"b", "c"} for g in optimized.parallel_groups))

    def test_optimizer_never_removes_a_genuine_dependency(self):
        steps = [PlanStep("a", "d1", {"coding"}), PlanStep("b", "d2", {"testing"}, dependencies=["a"])]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps)
        optimized = PlanOptimizer().optimize(plan)
        step_b = next(s for s in optimized.steps if s.step_id == "b")
        self.assertEqual(step_b.dependencies, ["a"])

    def test_exact_duplicate_steps_merged(self):
        steps = [PlanStep("a", "same description", {"coding"}), PlanStep("b", "same description", {"coding"})]
        plan = ExecutionPlan(plan_id="p", task_id="t", objective="o", steps=steps)
        optimized = PlanOptimizer().optimize(plan)
        self.assertEqual(len(optimized.steps), 1)


# 14-15. Plan revision and diff ----------------------------------------------------

class TestPlanRevisionAndDiff(unittest.TestCase):
    def test_revision_creates_new_version(self):
        plan = AdvancedPlanner().create_plan("Build a small calculator project.", task_id="rt")
        new_plan, diff = PlanReviser().revise(plan, evidence={"failed_step": "rt-test"}, reason="tests failed")
        self.assertEqual(new_plan.version, 2)
        self.assertIn("rt-test-debug", [s.step_id for s in new_plan.steps])

    def test_revised_plan_is_still_valid(self):
        plan = AdvancedPlanner().create_plan("Build a small calculator project.", task_id="rt2")
        new_plan, diff = PlanReviser().revise(plan, evidence={"failed_step": "rt2-test"}, reason="tests failed")
        result = PlanValidator().validate(new_plan)
        self.assertTrue(result.valid)

    def test_diff_reports_added_step_and_rerouted_dependency(self):
        plan = AdvancedPlanner().create_plan("Build a small calculator project.", task_id="rt3")
        new_plan, diff = PlanReviser().revise(plan, evidence={"failed_step": "rt3-test"}, reason="x")
        self.assertIn("rt3-test-debug", diff.added_steps)
        # P0-1: the step directly downstream of a failed test step is the
        # build/run stage (runcheck) now, with review behind it.
        self.assertTrue(any(d["step_id"] == "rt3-runcheck" for d in diff.changed_dependencies))

    def test_diff_between_identical_plans_is_empty(self):
        plan = AdvancedPlanner().create_plan("Build a small calculator project.", task_id="rt4")
        diff = diff_plans(plan, plan)
        self.assertEqual(diff.added_steps, [])
        self.assertEqual(diff.removed_steps, [])


# 16-17. Milestones and checkpoints ---------------------------------------------

class TestMilestonesAndCheckpoints(unittest.TestCase):
    def test_milestones_grouped_by_capability(self):
        plan = AdvancedPlanner().create_plan("Build a small calculator project.", task_id="mt")
        names = {m.name for m in plan.milestones}
        self.assertEqual(names, {"Implementation", "Testing", "Build/Run", "Review", "Verification"})

    def test_checkpoint_tracks_completed_failed_remaining(self):
        plan = AdvancedPlanner().create_plan("Build a small calculator project.", task_id="ct")
        cp = CheckpointManager().create(plan, completed_steps=["ct-code", "ct-test"], failed_steps=["ct-review"])
        self.assertEqual(cp.completed_steps, ["ct-code", "ct-test"])
        self.assertEqual(cp.failed_steps, ["ct-review"])
        self.assertEqual(cp.remaining_steps, ["ct-runcheck", "ct-verify"])

    def test_milestone_only_completes_with_full_evidence(self):
        plan = AdvancedPlanner().create_plan("Build a small calculator project.", task_id="mt2")
        # only partially complete the "coding" milestone's steps (there's
        # only one step in it, so completing it fully-completes that milestone
        # but NOT the multi-step ones if any existed)
        done = CheckpointManager().milestones_completed(plan, completed_steps=["mt2-code"])
        self.assertEqual([m.name for m in done], ["Implementation"])
        # testing milestone step not in completed_steps -> not completed
        testing_milestone = next(m for m in plan.milestones if m.name == "Testing")
        self.assertFalse(testing_milestone.completed)


# 18-19. Planner/orchestrator and Planner/Quality Gate integration -----------------

class TestPlannerOrchestratorIntegration(PlanningTestBase):
    def test_invalid_plan_never_reaches_execution(self):
        po = PlannedOrchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        # force an invalid plan by pre-building one with a circular dependency
        import planning.planner as planner_module
        broken_plan_steps = [PlanStep("a", "d", {"coding"}, dependencies=["b"]), PlanStep("b", "d", {"testing"}, dependencies=["a"])]

        class BrokenPlanner:
            def create_plan(self, objective, task_id=None, project_memory=None, prior_knowledge=None):
                from planning.ambiguity import AmbiguityDetector
                p = ExecutionPlan(plan_id="broken", task_id=task_id, objective=objective, steps=broken_plan_steps)
                p.ambiguity = AmbiguityDetector().detect(objective)
                p.risks = []
                return p

        po.planner = BrokenPlanner()
        result = po.run("Build something with circular deps.")
        self.assertEqual(result["status"], "REJECTED")
        self.assertIsNone(result["orchestration_result"])  # orchestrator never ran

    def test_valid_plan_executes_through_real_orchestrator(self):
        po = PlannedOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry)
        result = po.run("Create a small calculator project with implementation and tests.")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIsNotNone(result["orchestration_result"])
        with open(os.path.join(self.sandbox, "calculator.py")) as f:
            self.assertIn("def add", f.read())


class TestPlannerQualityGateIntegration(PlanningTestBase):
    def test_acceptance_criteria_become_requirement_checks(self):
        po = PlannedOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry)
        result = po.run("Create a small calculator project with implementation and tests.")
        criteria_texts = {c.requirement for c in result["requirement_checks"]}
        self.assertTrue(any("test" in c.lower() for c in criteria_texts))

    def test_quality_gate_remains_authoritative_not_bypassed(self):
        po = PlannedOrchestrator(provider=MockAIProvider(), tool_registry=self.registry)  # no script - won't create files
        result = po.run("Create a small calculator project with implementation and tests.")
        self.assertNotEqual(result["status"], "COMPLETED")  # can't be approved without real evidence


# 20. Memory -------------------------------------------------------------------

class TestPlanningMemory(PlanningTestBase):
    def test_plan_history_stored_structured(self):
        memory = ProjectMemory(path=os.path.join(self.sandbox, "pm.json"))
        po = PlannedOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=memory)
        result = po.run("Create a small calculator project with implementation and tests.", task_id="plan-mem-1")

        entry = memory.recall("plan:plan-mem-1:v1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["value"]["task_id"], "plan-mem-1")
        self.assertIn("complexity", entry["value"])

    def test_memory_never_contains_api_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-plan-should-never-leak"
        try:
            memory = ProjectMemory(path=os.path.join(self.sandbox, "pm2.json"))
            po = PlannedOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry, project_memory=memory)
            po.run("Create a small calculator project with implementation and tests.", task_id="plan-key-safety")
            entry = memory.recall("plan:plan-key-safety:v1")
            self.assertNotIn("sk-plan-should-never-leak", str(entry))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]


# 21. Observability --------------------------------------------------------------

class TestPlanningObservability(PlanningTestBase):
    def test_required_events_present(self):
        po = PlannedOrchestrator(provider=MockAIProvider(script=CALC_SCRIPT), tool_registry=self.registry)
        result = po.run("Create a small calculator project with implementation and tests.")
        names = {e["event"] for e in result["events"]}
        for required in ("PLAN_CREATED", "PLAN_VALIDATION_STARTED", "PLAN_VALIDATED",
                          "PLAN_OPTIMIZATION_STARTED", "PLAN_OPTIMIZED", "CHECKPOINT_CREATED", "MILESTONE_COMPLETED"):
            self.assertIn(required, names)

    def test_rejected_plan_emits_plan_rejected_event(self):
        po = PlannedOrchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        broken_steps = [PlanStep("a", "d", {"coding"}, dependencies=["missing"])]

        class BrokenPlanner:
            def create_plan(self, objective, task_id=None, project_memory=None, prior_knowledge=None):
                from planning.ambiguity import AmbiguityDetector
                p = ExecutionPlan(plan_id="broken2", task_id=task_id, objective=objective, steps=broken_steps)
                p.ambiguity = AmbiguityDetector().detect(objective)
                p.risks = []
                return p

        po.planner = BrokenPlanner()
        result = po.run("broken task")
        names = {e["event"] for e in result["events"]}
        self.assertIn("PLAN_REJECTED", names)


if __name__ == "__main__":
    unittest.main()
