"""
Phase 4 tests: task state machine, autonomous executor, failure analyzer,
fixer scope, critic, verification, repair limits, loop detection, memory,
events, bounded context. Run with: python3 -m unittest tests.test_repair -v
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ToolRegistry, ToolCall
from providers import MockAIProvider
from memory import ProjectMemory
from repair import (
    TaskState, FailureAnalyzer, DebuggerFixerWorker, Critic, TaskVerifier,
    ScopedToolRegistry, build_repair_prompt, AutonomousTaskExecutor,
)


def seed_buggy_calculator(registry, bug="return a + b  # BUG: should be a - b"):
    registry.impl.write_file("calculator.py", (
        "def add(a, b):\n    return a + b\n\n"
        f"def subtract(a, b):\n    {bug}\n"
    ))
    registry.impl.write_file("test_calculator.py", (
        "import unittest\nfrom calculator import add, subtract\n\n"
        "class TestCalculator(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        "    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n"
    ))


FIX_SCRIPT = [
    {"text": "inspecting", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "calculator.py"}}]},
    {"text": "fixing", "tool_calls": [{"id": "c2", "name": "edit_file", "arguments": {
        "path": "calculator.py", "old_str": "return a + b  # BUG: should be a - b", "new_str": "return a - b",
    }}]},
    {"text": "Fixed the subtract bug.", "tool_calls": []},
]


class RepairTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_repair_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)


# 1. Task state machine -------------------------------------------------

class TestTaskStateMachine(unittest.TestCase):
    def test_all_required_states_exist(self):
        required = {"CREATED", "PLANNING", "EXECUTING", "TESTING", "FAILED", "ANALYZING",
                    "FIXING", "RETESTING", "VERIFYING", "COMPLETED", "FAILED_FINAL", "LIMIT_REACHED"}
        actual = {s.value for s in TaskState}
        self.assertTrue(required.issubset(actual))


# 2. Successful execution / end-to-end ----------------------------------

class TestSuccessfulRepair(RepairTestBase):
    def test_real_bug_is_actually_fixed_and_verified(self):
        seed_buggy_calculator(self.registry)
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=FIX_SCRIPT), tool_registry=self.registry)
        result = executor.run("Create a calculator project and make sure its tests pass.",
                               required_files=["calculator.py", "test_calculator.py"])

        self.assertEqual(result.final_state, "COMPLETED")
        self.assertEqual(result.files_changed_total, ["calculator.py"])
        self.assertTrue(result.final_test_result["passed"])
        self.assertTrue(result.critic.approved)
        self.assertTrue(result.verification.passed)

        # real file, actually changed on disk - not simulated
        with open(os.path.join(self.sandbox, "calculator.py")) as f:
            content = f.read()
        self.assertIn("return a - b", content)

    def test_no_repair_needed_when_tests_already_pass(self):
        self.registry.impl.write_file("m.py", "def f():\n    return 1\n")
        self.registry.impl.write_file("test_m.py", "import unittest\nfrom m import f\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(f(), 1)\n")
        executor = AutonomousTaskExecutor(provider=MockAIProvider(), tool_registry=self.registry)
        result = executor.run("no-op task", required_files=["m.py"])
        self.assertEqual(result.final_state, "COMPLETED")
        self.assertEqual(len(result.attempts), 0)

    def test_creation_phase_files_count_toward_files_changed_total(self):
        # a creation_worker (Phase 3's CodingAgentWorker) writes the files;
        # the executor must credit those, not just repair-phase edits.
        from agent.coding_worker import CodingAgentWorker
        create_script = [
            {"text": "creating", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {
                "path": "greet.py", "content": "def greet():\n    return 'hi'\n"}}]},
            {"text": "done", "tool_calls": []},
        ]
        test_script = [
            {"text": "creating test", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {
                "path": "test_greet.py", "content": "import unittest\nfrom greet import greet\nclass T(unittest.TestCase):\n    def test_greet(self):\n        self.assertEqual(greet(), 'hi')\n"}}]},
            {"text": "done", "tool_calls": []},
        ]
        # creation_worker writes greet.py; pre-seed the test file directly
        # (single creation_worker call covers one file in this MVP flow)
        self.registry.impl.write_file("test_greet.py", "import unittest\nfrom greet import greet\nclass T(unittest.TestCase):\n    def test_greet(self):\n        self.assertEqual(greet(), 'hi')\n")
        creation_worker = CodingAgentWorker(provider=MockAIProvider(script=create_script), tool_registry=self.registry)
        executor = AutonomousTaskExecutor(provider=MockAIProvider(), tool_registry=self.registry)
        result = executor.run("create greet.py", creation_worker=creation_worker, required_files=["greet.py"])

        self.assertEqual(result.final_state, "COMPLETED")
        self.assertIn("greet.py", result.files_changed_total)


# 3. Failure detection / analysis ----------------------------------------

class TestFailureDetection(RepairTestBase):
    def test_real_test_failure_is_detected_not_assumed(self):
        seed_buggy_calculator(self.registry)
        test_result = self.registry.execute(ToolCall(tool_name="run_tests", arguments={})).output
        self.assertFalse(test_result["passed"])
        self.assertIn("AssertionError", test_result["stderr"])


class TestFailureAnalyzer(RepairTestBase):
    def test_analyze_passed_result_short_circuits(self):
        analysis = FailureAnalyzer().analyze({"passed": True, "stdout": "", "stderr": ""})
        self.assertEqual(analysis.failure_type, "none")
        self.assertEqual(analysis.confidence, 1.0)

    def test_analyze_real_assertion_failure(self):
        seed_buggy_calculator(self.registry)
        test_result = self.registry.execute(ToolCall(tool_name="run_tests", arguments={})).output
        analysis = FailureAnalyzer().analyze(test_result, tool_registry=self.registry)
        self.assertEqual(analysis.failure_type, "AssertionError")
        self.assertLess(analysis.confidence, 1.0)  # heuristic, never claims certainty

    def test_analyzer_resolves_implementation_file_not_just_test_file(self):
        # this is the exact bug found during manual testing: traceback
        # points at the TEST file, but the fix belongs in the implementation.
        seed_buggy_calculator(self.registry)
        test_result = self.registry.execute(ToolCall(tool_name="run_tests", arguments={})).output
        analysis = FailureAnalyzer().analyze(test_result, tool_registry=self.registry)
        self.assertIn("calculator.py", analysis.affected_files)
        self.assertNotIn("test_calculator.py", analysis.affected_files)

    def test_no_false_confidence_without_recognizable_pattern(self):
        analysis = FailureAnalyzer().analyze({"passed": False, "stdout": "", "stderr": "gibberish with no known shape"})
        self.assertLessEqual(analysis.confidence, 0.3)


# 4. Fixer + fix scope ----------------------------------------------------

class TestFixerScope(RepairTestBase):
    def test_fixer_uses_real_tool_registry_and_applies_real_edit(self):
        seed_buggy_calculator(self.registry)
        test_result = self.registry.execute(ToolCall(tool_name="run_tests", arguments={})).output
        analysis = FailureAnalyzer().analyze(test_result, tool_registry=self.registry)

        fixer = DebuggerFixerWorker(provider=MockAIProvider(script=FIX_SCRIPT), tool_registry=self.registry)
        result = fixer.propose_fix("fix subtract", analysis, task_id="t1")

        self.assertEqual(result.status, "COMPLETED")
        with open(os.path.join(self.sandbox, "calculator.py")) as f:
            self.assertIn("return a - b", f.read())

    def test_scoped_registry_blocks_edit_outside_allowed_paths(self):
        self.registry.impl.write_file("a.py", "x = 1\n")
        self.registry.impl.write_file("b.py", "y = 2\n")
        scoped = ScopedToolRegistry(self.registry, allowed_paths={"a.py"})

        denied = scoped.execute(ToolCall(tool_name="write_file", arguments={"path": "b.py", "content": "hacked"}))
        self.assertEqual(denied.status.value, "DENIED")
        with open(os.path.join(self.sandbox, "b.py")) as f:
            self.assertEqual(f.read(), "y = 2\n")  # untouched

        allowed = scoped.execute(ToolCall(tool_name="write_file", arguments={"path": "a.py", "content": "x = 2\n"}))
        self.assertEqual(allowed.status.value, "SUCCESS")

    def test_scoped_registry_none_allows_unrestricted(self):
        self.registry.impl.write_file("a.py", "x = 1\n")
        scoped = ScopedToolRegistry(self.registry, allowed_paths=None)
        result = scoped.execute(ToolCall(tool_name="write_file", arguments={"path": "a.py", "content": "x = 2\n"}))
        self.assertEqual(result.status.value, "SUCCESS")


# 5. Repeated failure / repair limit / loop detection ---------------------

class TestRepairLimits(RepairTestBase):
    def test_repair_limit_reached_without_progress_stops_at_failed_final(self):
        # a fixer that makes progressively DIFFERENT but still-wrong edits -
        # exercises the raw attempt-count limit rather than loop detection.
        self.registry.impl.write_file("calc.py", "def f(x):\n    return x + 100\n")
        self.registry.impl.write_file("test_calc.py", "import unittest\nfrom calc import f\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(f(1), 1)\n")

        script, prev = [], "return x + 100"
        for i, offset in enumerate([99, 98, 97, 96]):
            new = f"return x + {offset}"
            script.append({"text": f"attempt {i}", "tool_calls": [{"id": f"c{i}", "name": "edit_file",
                "arguments": {"path": "calc.py", "old_str": prev, "new_str": new}}]})
            prev = new

        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=script), tool_registry=self.registry, max_repair_attempts=3)
        result = executor.run("fix f", required_files=["calc.py"])

        self.assertEqual(result.final_state, "FAILED_FINAL")
        self.assertIn("repair budget exhausted", result.status_reason)
        self.assertLessEqual(len(result.attempts), 3)

    def test_never_exceeds_max_repair_attempts(self):
        script = [{"text": "no-op", "tool_calls": []} for _ in range(20)]  # never even fixes anything
        self.registry.impl.write_file("calc.py", "def f():\n    return 0\n")
        self.registry.impl.write_file("test_calc.py", "import unittest\nfrom calc import f\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(f(), 1)\n")
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=script), tool_registry=self.registry, max_repair_attempts=2)
        result = executor.run("fix f", required_files=["calc.py"])
        self.assertEqual(result.final_state, "FAILED_FINAL")
        self.assertLessEqual(len(result.attempts), 2)

    def test_loop_detection_stops_identical_repeated_failure(self):
        seed_buggy_calculator(self.registry)
        # a "fixer" that never actually changes the bug
        noop_script = [{"text": "trying", "tool_calls": [{"id": f"c{i}", "name": "edit_file", "arguments": {
            "path": "calculator.py", "old_str": "return a + b  # BUG: should be a - b",
            "new_str": "return a + b  # still buggy",
        }}]} for i in range(1)] + [{"text": "trying again", "tool_calls": [{"id": f"c{i}", "name": "edit_file", "arguments": {
            "path": "calculator.py", "old_str": "return a + b  # still buggy",
            "new_str": "return a + b  # still buggy 2",
        }}]} for i in range(1)] * 10
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=noop_script), tool_registry=self.registry, max_repair_attempts=10)
        result = executor.run("fix subtract", required_files=["calculator.py"])

        self.assertEqual(result.final_state, "FAILED_FINAL")
        self.assertIn("loop detected", result.status_reason)
        # proves it stopped BEFORE exhausting the (much higher) max_repair_attempts=10
        self.assertLess(len(result.attempts), 10)

    def test_execution_timeout_produces_limit_reached(self):
        seed_buggy_calculator(self.registry)
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=FIX_SCRIPT), tool_registry=self.registry, max_execution_seconds=0)
        result = executor.run("fix it", required_files=["calculator.py"])
        self.assertEqual(result.final_state, "LIMIT_REACHED")


# 6. Critic ----------------------------------------------------------------

class TestCritic(unittest.TestCase):
    def test_approves_when_tests_pass_and_files_changed(self):
        result = Critic().review("obj", ["calculator.py"], {"passed": True})
        self.assertTrue(result.approved)
        self.assertEqual(result.severity, "none")

    def test_rejects_when_tests_fail(self):
        result = Critic().review("obj", ["calculator.py"], {"passed": False})
        self.assertFalse(result.approved)
        self.assertEqual(result.severity, "blocking")

    def test_rejects_when_no_files_changed_despite_repair_attempts(self):
        result = Critic().review("obj", [], {"passed": True}, attempts_made=1)
        self.assertFalse(result.approved)
        self.assertIn("No files were changed", "".join(result.issues))

    def test_does_not_penalize_empty_files_changed_when_no_repair_was_needed(self):
        # tests already passed from the start - nothing to repair, so an
        # empty files_changed list is correct, not suspicious.
        result = Critic().review("obj", [], {"passed": True}, attempts_made=0)
        self.assertTrue(result.approved)

    def test_flags_unusually_broad_change(self):
        result = Critic().review("obj", [f"f{i}.py" for i in range(10)], {"passed": True})
        self.assertTrue(any("Unusually broad" in i for i in result.issues))


# 7. Verification / verification rejection ----------------------------------

class TestTaskVerifier(RepairTestBase):
    def test_passes_when_required_files_exist_and_tests_pass(self):
        self.registry.impl.write_file("a.py", "x = 1\n")
        result = TaskVerifier().verify(self.registry, ["a.py"], {"passed": True})
        self.assertTrue(result.passed)

    def test_rejects_when_required_file_missing(self):
        result = TaskVerifier().verify(self.registry, ["does_not_exist.py"], {"passed": True})
        self.assertFalse(result.passed)
        self.assertIn("does_not_exist.py", result.detail)

    def test_rejects_when_tests_did_not_pass_even_if_files_exist(self):
        self.registry.impl.write_file("a.py", "x = 1\n")
        result = TaskVerifier().verify(self.registry, ["a.py"], {"passed": False})
        self.assertFalse(result.passed)

    def test_executor_loops_back_to_repair_when_verifier_rejects_despite_passing_tests(self):
        # tests pass, but a REQUIRED file the task asked for was never
        # created - critic/verifier must catch what the test suite missed.
        self.registry.impl.write_file("calc.py", "def f():\n    return 1\n")
        self.registry.impl.write_file("test_calc.py", "import unittest\nfrom calc import f\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(f(), 1)\n")
        script = [{"text": "no changes needed I think", "tool_calls": []} for _ in range(5)]
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=script), tool_registry=self.registry, max_repair_attempts=2)
        result = executor.run("create calc.py and a required README.md", required_files=["calc.py", "README.md"])
        self.assertEqual(result.final_state, "FAILED_FINAL")
        self.assertFalse(result.verification.passed)


# 8. Memory ------------------------------------------------------------------

class TestRepairMemory(RepairTestBase):
    def test_repair_history_stored_in_project_memory(self):
        seed_buggy_calculator(self.registry)
        memory = ProjectMemory(path=os.path.join(self.sandbox, "pm.json"))
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=FIX_SCRIPT), tool_registry=self.registry, project_memory=memory)
        result = executor.run("fix it", task_id="mem-repair-1", required_files=["calculator.py"])

        entry = memory.recall("repair_session:mem-repair-1")
        self.assertIsNotNone(entry)
        value = entry["value"]
        self.assertEqual(value["final_state"], "COMPLETED")
        self.assertEqual(len(value["attempts"]), 1)
        self.assertIn("calculator.py", value["files_changed_total"])
        self.assertTrue(value["critic_approved"])
        self.assertTrue(value["verification_passed"])

    def test_memory_never_contains_api_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-repair-should-never-leak"
        try:
            seed_buggy_calculator(self.registry)
            memory = ProjectMemory(path=os.path.join(self.sandbox, "pm2.json"))
            executor = AutonomousTaskExecutor(provider=MockAIProvider(script=FIX_SCRIPT), tool_registry=self.registry, project_memory=memory)
            executor.run("fix it", task_id="key-safety", required_files=["calculator.py"])
            entry = memory.recall("repair_session:key-safety")
            self.assertNotIn("sk-repair-should-never-leak", str(entry))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]


# 9. Observability / events ---------------------------------------------------

class TestRepairEvents(RepairTestBase):
    def test_required_event_vocabulary_present(self):
        seed_buggy_calculator(self.registry)
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=FIX_SCRIPT), tool_registry=self.registry)
        result = executor.run("fix it", required_files=["calculator.py"])
        names = {e["event"] for e in result.events}
        for required in ("REPAIR_STARTED", "FAILURE_ANALYZED", "FIX_PROPOSED", "FIX_APPLIED",
                          "RETEST_STARTED", "RETEST_RESULT", "CRITIC_STARTED", "CRITIC_RESULT",
                          "VERIFICATION_STARTED", "VERIFICATION_RESULT"):
            self.assertIn(required, names)

    def test_phase3_events_still_present_via_underlying_tool_calls(self):
        # Phase 3's tool-call events are preserved on the underlying
        # registry's own execution log - not removed by Phase 4.
        seed_buggy_calculator(self.registry)
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=FIX_SCRIPT), tool_registry=self.registry)
        executor.run("fix it", required_files=["calculator.py"])
        tool_log_events = [e["result"]["status"] for e in self.registry.log.entries]
        self.assertIn("SUCCESS", tool_log_events)

    def test_repair_limit_reached_event_emitted_on_budget_exhaustion(self):
        script = [{"text": "no-op", "tool_calls": []} for _ in range(20)]
        self.registry.impl.write_file("calc.py", "def f():\n    return 0\n")
        self.registry.impl.write_file("test_calc.py", "import unittest\nfrom calc import f\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(f(), 1)\n")
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=script), tool_registry=self.registry, max_repair_attempts=1)
        result = executor.run("fix f", required_files=["calc.py"])
        names = [e["event"] for e in result.events]
        self.assertIn("REPAIR_LIMIT_REACHED", names)


# 10. Bounded context -----------------------------------------------------------

class TestBoundedContext(unittest.TestCase):
    def test_repair_prompt_excludes_full_history_stays_bounded(self):
        from repair.schemas import FailureAnalysis
        analysis = FailureAnalysis(
            failure_type="AssertionError", root_cause="8 != 2",
            affected_files=["calculator.py"], evidence="x" * 5000,  # deliberately huge
            recommended_fix="fix it", confidence=0.7,
        )
        prompt = build_repair_prompt("original objective", analysis, previous_attempt_summary="prior fix didn't work")

        # bounded: evidence excerpt capped, not the full 5000 chars dumped
        self.assertLess(len(prompt), 2000)
        self.assertIn("calculator.py", prompt)
        self.assertIn("prior fix didn't work", prompt)

    def test_prompt_omits_previous_summary_when_none_given(self):
        from repair.schemas import FailureAnalysis
        analysis = FailureAnalysis(failure_type="X", root_cause="Y", evidence="z")
        prompt = build_repair_prompt("obj", analysis)
        self.assertNotIn("Previous attempt", prompt)


# 11. Final failure state -----------------------------------------------------

class TestFinalFailureState(RepairTestBase):
    def test_unfixable_task_terminates_not_loops_forever(self):
        seed_buggy_calculator(self.registry)
        # scripted to run indefinitely if not bounded (50 identical no-op attempts)
        script = [{"text": "trying", "tool_calls": [{"id": f"c{i}", "name": "read_file", "arguments": {"path": "calculator.py"}}]} for i in range(50)]
        executor = AutonomousTaskExecutor(provider=MockAIProvider(script=script), tool_registry=self.registry, max_repair_attempts=3)
        result = executor.run("fix it", required_files=["calculator.py"])

        self.assertEqual(result.final_state, "FAILED_FINAL")
        self.assertLessEqual(len(result.attempts), 3)
        # confirms actual execution halted well short of the 50-call script
        model_requests = [e for e in result.events if e["event"] == "MODEL_REQUEST"]
        self.assertLess(len(model_requests), 50)


# 12. Error handling does not crash the process -------------------------------

class TestErrorHandling(RepairTestBase):
    def test_provider_failure_during_fix_is_handled_not_crashed(self):
        seed_buggy_calculator(self.registry)
        provider = MockAIProvider(simulate_timeout=True)
        executor = AutonomousTaskExecutor(provider=provider, tool_registry=self.registry, max_repair_attempts=1)
        result = executor.run("fix it", required_files=["calculator.py"])  # must not raise
        self.assertEqual(result.final_state, "FAILED_FINAL")


if __name__ == "__main__":
    unittest.main()
