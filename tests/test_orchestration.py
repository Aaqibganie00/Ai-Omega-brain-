"""
Phase 6 tests: worker registry, decomposition, dependency graph, bounded
parallel execution, resource limits, permission enforcement, failure
handling, aggregation, memory, observability.
Run with: python3 -m unittest tests.test_orchestration -v
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
from orchestration import (
    Orchestrator, ResourceLimits, WorkerRegistry, WorkerDefinition,
    TaskDecomposer, WorkerSelector, ResultAggregator, SharedTaskState, build_default_registry,
)
from orchestration.schemas import SubTask, WorkerResult, WorkerStatus


class OrchestrationTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_orch_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def seed_calculator(self):
        self.registry.impl.write_file("calculator.py", "def add(a, b):\n    return a + b\n")
        self.registry.impl.write_file("test_calculator.py", (
            "import unittest\nfrom calculator import add\n"
            "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3), 5)\n"
        ))
        # P0-1: the build/run stage needs a runnable project entry point.
        self.registry.impl.write_file("main.py", "print('calculator entry ok')\n")


# 1-4: worker registry, discovery, capability matching, selection ----------

class TestWorkerRegistry(unittest.TestCase):
    def test_register_and_get(self):
        reg = WorkerRegistry()
        d = WorkerDefinition("coding", {"coding"}, {Permission.WRITE}, "desc", lambda *a: None)
        reg.register(d)
        self.assertIs(reg.get("coding"), d)

    def test_find_by_capability(self):
        reg = build_default_registry()
        matches = reg.find_by_capability("coding")
        self.assertTrue(any(m.worker_type == "coding" for m in matches))

    def test_capability_with_no_match_returns_empty(self):
        reg = build_default_registry()
        self.assertEqual(reg.find_by_capability("nonexistent_capability"), [])

    def test_all_default_worker_types_registered(self):
        # P0-1: the build/run stage is a REAL registered worker type.
        reg = build_default_registry()
        types = set(reg.all_worker_types())
        self.assertEqual(types, {"planning", "research", "coding", "testing", "debugging", "review", "verification", "build_run"})


class TestWorkerSelector(OrchestrationTestBase):
    def test_selects_matching_worker(self):
        reg = build_default_registry()
        selector = WorkerSelector(reg)
        subtask = SubTask("s1", "t1", "code something", {"coding"})
        worker_type, reason = selector.select(subtask, self.registry.permissions)
        self.assertEqual(worker_type, "coding")

    def test_no_worker_for_unknown_capability(self):
        reg = build_default_registry()
        selector = WorkerSelector(reg)
        subtask = SubTask("s1", "t1", "do something exotic", {"quantum_computing"})
        worker_type, reason = selector.select(subtask, self.registry.permissions)
        self.assertIsNone(worker_type)

    def test_permission_enforcement_blocks_selection(self):
        reg = build_default_registry()
        selector = WorkerSelector(reg)
        read_only_registry = ToolRegistry(base_dir=self.sandbox, permission_config=PermissionConfig(granted={Permission.READ}))
        subtask = SubTask("s1", "t1", "code something", {"coding"})  # needs WRITE
        worker_type, reason = selector.select(subtask, read_only_registry.permissions)
        self.assertIsNone(worker_type)


# 5. task decomposition ------------------------------------------------------

class TestTaskDecomposer(unittest.TestCase):
    def test_simple_task_decomposes_minimally(self):
        subtasks = TaskDecomposer().decompose("t1", "write a hello world script")
        self.assertEqual(len(subtasks), 1)

    def test_project_task_decomposes_into_pipeline(self):
        # P0-1: build plans now include the runcheck (build/run) stage.
        subtasks = TaskDecomposer().decompose("t1", "Build a small calculator project.")
        types = {s.subtask_id.split("-")[-1] for s in subtasks}
        self.assertEqual(types, {"code", "test", "runcheck", "review", "verify"})

    def test_research_task_decomposes_into_independent_branches(self):
        subtasks = TaskDecomposer().decompose("t1", "Research and compare three database options.")
        self.assertEqual(len(subtasks), 4)
        independent = [s for s in subtasks if not s.dependencies]
        self.assertEqual(len(independent), 3)

    def test_dependencies_are_explicit_not_hidden(self):
        subtasks = TaskDecomposer().decompose("t1", "Build a small calculator project.")
        by_id = {s.subtask_id: s for s in subtasks}
        test_task = by_id["t1-test"]
        self.assertIn("t1-code", test_task.dependencies)


# 6-8. dependency graph, sequential deps, independent parallel -----------------

class TestDependencyExecution(OrchestrationTestBase):
    def test_sequential_dependency_order_respected(self):
        self.seed_calculator()
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        result = orch.run("Build a small calculator project.")
        events = [e for e in result.events if e["event"] == "WORKER_COMPLETED"]
        order = [e["detail"] for e in events]
        # code must complete before test, test before review, review before verify
        self.assertLess(order.index([o for o in order if o.endswith("-code")][0]),
                         order.index([o for o in order if o.endswith("-test")][0]))

    def test_independent_tasks_do_not_wait_on_each_other(self):
        task_id = "indep-1"
        A = SubTask(f"{task_id}-A", task_id, "research a", {"research"})
        B = SubTask(f"{task_id}-B", task_id, "research b", {"research"})
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        result = orch.run("two independent research tasks", task_id=task_id, subtasks_override=[A, B])
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(set(result.aggregation["successful_workers"]), {A.subtask_id, B.subtask_id})


# 9. maximum concurrent workers (real overlap, not fake) -----------------------

class SlowMockProvider(MockAIProvider):
    def generate(self, request):
        time.sleep(0.15)
        return super().generate(request)


class TestConcurrency(OrchestrationTestBase):
    def test_concurrency_never_exceeds_max_concurrent_workers(self):
        orch = Orchestrator(provider=SlowMockProvider(), tool_registry=self.registry, limits=ResourceLimits(max_concurrent_workers=2))
        result = orch.run("Research and compare three approaches.")
        self.assertLessEqual(result.performance["concurrency_observed"], 2)

    def test_real_overlap_occurs_not_just_labeled_parallel(self):
        # 3 independent research subtasks, each takes ~0.15s if truly
        # sequential that's ~0.45s; concurrent (limit=3) should be ~0.15-0.3s
        orch = Orchestrator(provider=SlowMockProvider(), tool_registry=self.registry, limits=ResourceLimits(max_concurrent_workers=3))
        result = orch.run("Research and compare three approaches.")
        self.assertEqual(result.performance["concurrency_observed"], 3)
        self.assertLess(result.performance["total_time_seconds"], 0.45)  # proves real overlap


# 10-11. max total workers / max task depth ------------------------------------

class TestResourceLimits(OrchestrationTestBase):
    def test_max_total_workers_stops_further_scheduling(self):
        self.seed_calculator()
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry, limits=ResourceLimits(max_total_workers=2))
        result = orch.run("Build a small calculator project.")
        self.assertEqual(result.performance["total_workers_started"], 2)
        self.assertEqual(result.status, "LIMIT_REACHED")
        # review and verify subtasks never got to run
        self.assertEqual(len(result.aggregation["successful_workers"]), 2)

    def test_max_task_depth_prevents_recursive_explosion(self):
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry, limits=ResourceLimits(max_task_depth=1))
        result = orch.run("Build a small calculator project.", depth=5)
        self.assertEqual(result.status, "LIMIT_REACHED")
        self.assertEqual(len(result.subtasks), 0)  # never even decomposed - stopped before spawning anything

    def test_no_recursive_worker_explosion_via_repeated_orchestration(self):
        # simulate what a "worker creates a worker" chain would look like:
        # repeatedly calling run() at increasing depth must halt at the limit.
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry, limits=ResourceLimits(max_task_depth=2))
        statuses = [orch.run("recurse", depth=d).status for d in range(5)]
        self.assertIn("LIMIT_REACHED", statuses)
        limit_index = statuses.index("LIMIT_REACHED")
        self.assertTrue(all(s == "LIMIT_REACHED" for s in statuses[limit_index:]))


# 12. permission enforcement (already covered above, add one more direct case) -

class TestPermissionEnforcement(OrchestrationTestBase):
    def test_worker_without_permission_fails_cleanly_not_bypassed(self):
        read_only = ToolRegistry(base_dir=self.sandbox, permission_config=PermissionConfig(granted={Permission.READ}))
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=read_only)
        result = orch.run("Build a small calculator project.")
        self.assertIn(result.status, ("FAILED", "LIMIT_REACHED"))
        self.assertFalse(os.path.exists(os.path.join(self.sandbox, "calculator.py")))  # no bypass happened


# 13-15. worker failure, retry-via-debugging-worker, dependency blocking ------

class TestFailureHandling(OrchestrationTestBase):
    def test_worker_failure_blocks_only_dependents_not_unrelated_work(self):
        self.registry.impl.write_file("test_calculator.py", "import unittest\nfrom calculator import add\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2,3), 5)\n")
        task_id = "fail-1"
        A = SubTask(f"{task_id}-A", task_id, "no-op", {"coding"})
        B = SubTask(f"{task_id}-B", task_id, "run tests", {"testing"}, dependencies=[A.subtask_id])
        C = SubTask(f"{task_id}-C", task_id, "review", {"review"}, dependencies=[B.subtask_id])
        D = SubTask(f"{task_id}-D", task_id, "independent", {"research"})
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        result = orch.run("dep test", task_id=task_id, subtasks_override=[A, B, C, D])

        self.assertIn(A.subtask_id, result.aggregation["successful_workers"])
        self.assertIn(B.subtask_id, result.aggregation["failed_workers"])   # calculator.py missing
        self.assertIn(C.subtask_id, result.aggregation["blocked_workers"])  # never ran
        self.assertIn(D.subtask_id, result.aggregation["successful_workers"])  # unaffected

    def test_debugging_worker_reuses_phase4_repair_not_a_new_system(self):
        import orchestration.workers as workers_module
        import inspect
        source = inspect.getsource(workers_module)
        # look at the actual debugging_worker function body specifically
        func_start = source.index("def debugging_worker")
        func_body = source[func_start:source.index("\n\n\n", func_start)]
        self.assertIn("AutonomousTaskExecutor", func_body)


# 16-17. result aggregation, shared state ---------------------------------------

class TestResultAggregation(unittest.TestCase):
    def test_aggregator_classifies_correctly(self):
        state = SharedTaskState(task_id="t1", objective="obj")
        s1 = SubTask("s1", "t1", "d", {"coding"})
        s2 = SubTask("s2", "t1", "d", {"testing"})
        s3 = SubTask("s3", "t1", "d", {"review"})
        state.subtasks = [s1, s2, s3]
        state.record_result("s1", WorkerResult(worker_id="w", task_id="s1", status=WorkerStatus.COMPLETED.value, files_changed=["a.py"]))
        state.record_result("s2", WorkerResult(worker_id="w", task_id="s2", status=WorkerStatus.FAILED.value, errors=["boom"]))
        # s3 has no result at all - blocked

        agg = ResultAggregator().aggregate(state)
        self.assertEqual(agg["successful_workers"], ["s1"])
        self.assertEqual(agg["failed_workers"], ["s2"])
        self.assertEqual(agg["blocked_workers"], ["s3"])
        self.assertEqual(agg["files_changed"], ["a.py"])
        self.assertIn("boom", agg["errors"])


class TestSharedState(unittest.TestCase):
    def test_concurrent_record_result_is_thread_safe(self):
        import threading
        state = SharedTaskState(task_id="t1", objective="obj")

        def record(i):
            state.record_result(f"s{i}", WorkerResult(worker_id="w", task_id=f"s{i}", status="COMPLETED", files_changed=[f"f{i}.py"]))

        threads = [threading.Thread(target=record, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(state.worker_results), 20)
        self.assertEqual(len(state.files_changed), 20)


# 18. worker communication ------------------------------------------------------

class TestWorkerCommunication(OrchestrationTestBase):
    def test_worker_messages_are_structured_not_free_text(self):
        from orchestration.schemas import WorkerMessage
        msg = WorkerMessage(sender="coding", receiver="orchestrator", task_id="t1", message_type="RESULT", payload={"status": "COMPLETED"})
        self.assertEqual(msg.message_type, "RESULT")
        self.assertIsInstance(msg.payload, dict)


# 19. cancellation (via depth limit halting mid-orchestration) ------------------

class TestCancellationViaLimits(OrchestrationTestBase):
    def test_limit_reached_preserves_completed_results_not_inconsistent(self):
        self.seed_calculator()
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry, limits=ResourceLimits(max_total_workers=1))
        result = orch.run("Build a small calculator project.")
        self.assertEqual(result.status, "LIMIT_REACHED")
        # the one worker that DID run is still recorded, not discarded
        self.assertEqual(len(result.worker_results), 1)


# 20. memory integration ---------------------------------------------------------

class TestOrchestrationMemory(OrchestrationTestBase):
    def test_structured_summary_stored(self):
        self.seed_calculator()
        memory = ProjectMemory(path=os.path.join(self.sandbox, "pm.json"))
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry, project_memory=memory)
        result = orch.run("Build a small calculator project.", task_id="orch-mem-1")

        entry = memory.recall("orchestration:orch-mem-1")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["value"]["status"], result.status)
        self.assertIn("workers_used", entry["value"])

    def test_memory_never_contains_api_key(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-orch-should-never-leak"
        try:
            self.seed_calculator()
            memory = ProjectMemory(path=os.path.join(self.sandbox, "pm2.json"))
            orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry, project_memory=memory)
            orch.run("Build a small calculator project.", task_id="orch-key-safety")
            entry = memory.recall("orchestration:orch-key-safety")
            self.assertNotIn("sk-orch-should-never-leak", str(entry))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]


# 21. observability -----------------------------------------------------------

class TestObservability(OrchestrationTestBase):
    def test_required_events_present(self):
        self.seed_calculator()
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        result = orch.run("Build a small calculator project.")
        names = {e["event"] for e in result.events}
        for required in ("ORCHESTRATION_STARTED", "TASK_DECOMPOSED", "WORKER_SELECTED", "WORKER_STARTED",
                          "WORKER_COMPLETED", "RESULT_AGGREGATION_STARTED", "RESULT_AGGREGATED", "ORCHESTRATION_COMPLETED"):
            self.assertIn(required, names)


# 22. quality gate integration --------------------------------------------------

class TestQualityGateIntegration(OrchestrationTestBase):
    def test_quality_gate_is_final_authority_not_bypassed(self):
        self.seed_calculator()
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry)
        result = orch.run("Build a small calculator project.")
        self.assertIsNotNone(result.quality_decision)
        self.assertEqual(result.quality_decision, "APPROVED")
        self.assertEqual(result.status, "COMPLETED")


# 23. existing coding worker compatibility ---------------------------------------

class TestCodingWorkerCompatibility(OrchestrationTestBase):
    def test_existing_coding_agent_worker_still_works_standalone(self):
        # Phase 3's CodingAgentWorker used directly, completely unaffected
        # by Phase 6's existence.
        from agent.coding_worker import CodingAgentWorker
        script = [{"text": "writing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "x.py", "content": "y = 1\n"}}]}, {"text": "done", "tool_calls": []}]
        worker = CodingAgentWorker(provider=MockAIProvider(script=script), tool_registry=self.registry)
        result = worker.execute_task("write x.py")
        self.assertEqual(result.status, "COMPLETED")

    def test_orchestration_coding_worker_reuses_same_class(self):
        import orchestration.workers as workers_module
        import inspect
        source = inspect.getsource(workers_module)
        func_start = source.index("def coding_worker_adapter")
        func_body = source[func_start:source.index("\n\n\n", func_start)]
        self.assertIn("CodingAgentWorker", func_body)


if __name__ == "__main__":
    unittest.main()
