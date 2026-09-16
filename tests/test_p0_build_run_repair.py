"""
P0 core tests (TRUE-PRODUCT-GOAL): build/run stage, failure->repair dispatch,
controlled run_command in the coding flow, bounded PlanReviser iteration, and
the configurable tool-turn budget.

All flows run REAL tools through the REAL ToolRegistry against temp sandboxes;
subprocesses really execute; no success is mocked. The model is the existing
scripted MockAIProvider - its turns only DECIDE which tool calls to request;
every call still passes the real permission/classification/sandbox pipeline.
Run with: python3 -m unittest tests.test_p0_build_run_repair -v
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ToolRegistry, Permission, PermissionConfig
from tools.schemas import ToolCall
from providers import MockAIProvider
from orchestration import Orchestrator, ResourceLimits, build_default_registry
from orchestration.schemas import SubTask
from planning.integration import PlannedOrchestrator
from agent.session import ToolUseSession, DEFAULT_MAX_TOOL_TURNS


# ---------------------------------------------------------------------------
# deterministic scripted fixtures
# ---------------------------------------------------------------------------

BUGGY_APP = (
    "def add(a, b):\n    return a - b  # DEFECT\n\n\n"
    "if __name__ == '__main__':\n"
    "    result = add(2, 3)\n"
    "    print('APP-OUT', result)\n"
    "    import sys\n"
    "    sys.exit(0 if result == 5 else 1)\n"
)
FIXED_MARKER = "return a + b"

APP_TESTS = (
    "import unittest\nfrom main import add\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
)

# defect -> repair -> fixed (the mandatory acceptance script)
DEFECT_REPAIR_SCRIPT = [
    {"text": "writing app", "tool_calls": [
        {"id": "c1", "name": "write_file", "arguments": {"path": "main.py", "content": BUGGY_APP}}]},
    {"text": "writing tests", "tool_calls": [
        {"id": "c2", "name": "write_file", "arguments": {"path": "test_main.py", "content": APP_TESTS}}]},
    {"text": "done", "tool_calls": []},
    # repair (debugging executor) turn: fix through edit_file via the registry
    {"text": "fixing defect", "tool_calls": [
        {"id": "c3", "name": "edit_file", "arguments": {
            "path": "main.py", "old_str": "return a - b  # DEFECT", "new_str": "return a + b"}}]},
    {"text": "fixed", "tool_calls": []},
]

# defect that the scripted model NEVER fixes -> repair must exhaust honestly
UNFIXABLE_SCRIPT = [
    {"text": "writing app", "tool_calls": [
        {"id": "c1", "name": "write_file", "arguments": {"path": "main.py", "content": BUGGY_APP}}]},
    {"text": "writing tests", "tool_calls": [
        {"id": "c2", "name": "write_file", "arguments": {"path": "test_main.py", "content": APP_TESTS}}]},
    {"text": "done", "tool_calls": []},
    {"text": "cannot fix", "tool_calls": []},
]

HAPPY_CALC_CALC = "def add(a, b):\n    return a + b\n"
HAPPY_MAIN = "print('ACCEPTANCE-OUTPUT-7749')\n"
HAPPY_TESTS = (
    "import unittest\nfrom calculator import add\n"
    "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
)

# P0-4 fixture: pass 1 forgets main.py (runcheck fails -> REJECTED),
# pass 2 supplies it (second orchestration pass succeeds).
REVISION_SCRIPT = [
    {"text": "implementing", "tool_calls": [
        {"id": "c1", "name": "write_file", "arguments": {"path": "calculator.py", "content": HAPPY_CALC_CALC}},
        {"id": "c1b", "name": "write_file", "arguments": {"path": "test_calculator.py", "content": HAPPY_TESTS}}]},
    {"text": "done", "tool_calls": []},
    # second pass (revised plan): now also provide the runnable entry point
    {"text": "add entry", "tool_calls": [
        {"id": "c2", "name": "write_file", "arguments": {"path": "main.py", "content": HAPPY_MAIN}}]},
    {"text": "done", "tool_calls": []},
    {"text": "analysis", "tool_calls": []},
    {"text": "fixed", "tool_calls": []},
]


class P0TestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_p0_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# P0-1: build/run stage is a real graph node with real subprocess evidence
# ---------------------------------------------------------------------------

class TestBuildRunWorker(P0TestBase):
    def test_build_run_worker_registered_and_selectable(self):
        reg = build_default_registry()
        matches = reg.find_by_capability("build_run")
        self.assertTrue(any(m.worker_type == "build_run" for m in matches))
        d = reg.get("build_run")
        # permissions must be the minimum: read the tree, execute the entry
        self.assertNotIn(Permission.WRITE, d.required_permissions)
        self.assertIn(Permission.EXECUTE, d.required_permissions)

    def test_successful_run_records_real_subprocess_evidence(self):
        self.registry.impl.write_file("main.py", HAPPY_MAIN)
        self.registry.impl.write_file("test_main.py", (
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n"
        ))
        result = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry).run("Build a small app project.")
        self.assertEqual(result.status, "COMPLETED")
        runs = result.aggregation.get("runs")
        self.assertTrue(runs, "no run evidence recorded")
        run = runs[-1]
        self.assertEqual(run["command"], "python3 -B main.py")
        self.assertEqual(run["returncode"], 0)
        self.assertTrue(run["ok"])
        # the marker proves OUR subprocess really executed this entry point
        self.assertIn("ACCEPTANCE-OUTPUT-7749", run["stdout"])

    def test_missing_entry_point_fails_honestly(self):
        self.registry.impl.write_file("test_main.py", (
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n"
        ))
        result = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry).run("Build a small app project.")
        self.assertEqual(result.status, "FAILED")
        runs = result.aggregation.get("runs") or []
        self.assertTrue(runs)
        self.assertFalse(runs[-1]["ok"])
        self.assertIn("no runnable entry", (runs[-1]["error"] or "").lower())

    def test_nonzero_exit_fails_honestly(self):
        self.registry.impl.write_file("main.py", "import sys\nprint('boom')\nsys.exit(3)\n")
        self.registry.impl.write_file("test_main.py", (
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n"
        ))
        result = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry).run("Build a small app project.")
        self.assertEqual(result.status, "FAILED")
        runs = (result.aggregation.get("runs") or [])
        self.assertEqual(runs[-1]["returncode"], 3)
        self.assertFalse(runs[-1]["ok"])
        self.assertIn("boom", runs[-1]["stdout"])

    def test_alternative_entry_points_used(self):
        self.registry.impl.write_file("app.py", "print('ALT-ENTRY-551')\n")
        self.registry.impl.write_file("test_main.py", (
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n"
        ))
        result = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry).run("Build a small app project.")
        self.assertEqual(result.status, "COMPLETED")
        run = (result.aggregation.get("runs") or [])[-1]
        self.assertEqual(run["command"], "python3 -B app.py")
        self.assertIn("ALT-ENTRY-551", run["stdout"])


# ---------------------------------------------------------------------------
# P0-2: bounded failure -> repair -> re-verify inside normal orchestration
# ---------------------------------------------------------------------------

class TestRepairDispatch(P0TestBase):
    def test_defect_triggers_repair_and_recheck(self):
        result = Orchestrator(
            provider=MockAIProvider(script=DEFECT_REPAIR_SCRIPT),
            tool_registry=self.registry,
        ).run("Build a small runnable app project.")
        events = [e["event"] for e in result.events]
        for e in ("REPAIR_DISPATCHED", "REPAIR_RESOLVED", "REPAIR_RETRY_STARTED", "REPAIR_RETRY_RESOLVED"):
            self.assertIn(e, events)
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.quality_decision, "APPROVED")
        # history preserved AND recovery tracked (never a rewrite):
        self.assertTrue(any(s.endswith("-test") for s in result.aggregation["failed_workers"]))
        self.assertTrue(any(s.endswith("-test") for s in result.aggregation.get("recovered_failures", [])))
        # real test chronology: defect detected -> repaired -> fresh pass
        passes = [t.get("passed") for t in result.aggregation.get("tests", [])]
        self.assertEqual(passes[0], False)
        self.assertIn(True, passes[1:])

    def test_unfixable_failure_exhausts_honestly(self):
        result = Orchestrator(
            provider=MockAIProvider(script=UNFIXABLE_SCRIPT),
            tool_registry=self.registry,
        ).run("Build a small runnable app project.")
        events = [e["event"] for e in result.events]
        self.assertIn("REPAIR_DISPATCHED", events)
        self.assertIn("REPAIR_EXHAUSTED", events)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.aggregation.get("recovered_failures", []), [])

    def test_repair_disabled_never_dispatches(self):
        result = Orchestrator(
            provider=MockAIProvider(script=DEFECT_REPAIR_SCRIPT),
            tool_registry=self.registry,
            max_repair_dispatches=0,
        ).run("Build a small runnable app project.")
        events = [e["event"] for e in result.events]
        self.assertNotIn("REPAIR_DISPATCHED", events)
        self.assertEqual(result.status, "FAILED")

    def test_repair_target_selection_only_repairable_capabilities(self):
        orch = Orchestrator(provider=MockAIProvider(), tool_registry=self.registry, max_repair_dispatches=0)
        from orchestration.shared_state import SharedTaskState
        from orchestration.schemas import WorkerResult, WorkerStatus
        shared = SharedTaskState(task_id="t", objective="probe")
        shared.record_result("t-review", WorkerResult(
            worker_id="w", task_id="t-review", status=WorkerStatus.FAILED.value,
            output="x", errors=["boom"]))
        recovered = set()
        target = orch._select_repair_target([SubTask("t-review", "t", "d", {"review"})], shared, recovered)
        self.assertIsNone(target)  # review is NOT repairable
        shared.record_result("t-test", WorkerResult(
            worker_id="w", task_id="t-test", status=WorkerStatus.FAILED.value,
            output="x", errors=["tests failed"]))
        target = orch._select_repair_target(
            [SubTask("t-review", "t", "d", {"review"}), SubTask("t-test", "t", "d", {"testing"})],
            shared, recovered)
        self.assertIsNotNone(target)
        self.assertEqual(target[0], "t-test")
        self.assertIn("tests failed", target[1])


# ---------------------------------------------------------------------------
# P0-3: run_command available through the EXISTING permission architecture
# ---------------------------------------------------------------------------

class TestRunCommandPolicy(P0TestBase):
    def _call(self, command, registry=None):
        registry = registry or self.registry
        return registry.execute(ToolCall(tool_name="run_command", arguments={"command": command, "cwd": "."}))

    def test_plain_shell_command_works_under_default_policy(self):
        result = self._call("python3 -c \"print('RC-OK-917')\"")
        self.assertEqual(result.status.value, "SUCCESS")
        self.assertIn("RC-OK-917", str(result.output))

    def test_network_command_denied_without_network_grant(self):
        result = self._call("curl -s https://example.com")
        self.assertEqual(result.status.value, "DENIED")
        self.assertIn("NETWORK", result.error)

    def test_destructive_command_denied(self):
        result = self._call("rm -rf /usr/bin")
        self.assertEqual(result.status.value, "DENIED")
        self.assertIn("DESTRUCTIVE", result.error)

    def test_no_execute_permission_denies_run_command(self):
        rw_registry = ToolRegistry(base_dir=self.sandbox, permission_config=PermissionConfig(
            granted={Permission.READ, Permission.WRITE}))
        result = self._call("python3 -c \"print(1)\"", registry=rw_registry)
        self.assertEqual(result.status.value, "DENIED")
        self.assertIn("EXECUTE", result.error)

    def test_coding_flow_can_call_run_command_through_session(self):
        """A coding worker whose allowed tools include run_command executes a
        real shell command through the session + registry (audit included)."""
        from agent.coding_worker import CodingAgentWorker
        worker = CodingAgentWorker(provider=MockAIProvider(script=[
            {"text": "run it", "tool_calls": [
                {"id": "c1", "name": "run_command", "arguments": {"command": "echo SESSION-SHELL-313", "cwd": "."}}]},
            {"text": "done", "tool_calls": []},
        ]), tool_registry=self.registry)
        result = worker.execute_task("run a quick shell echo")
        self.assertEqual(result.status, "COMPLETED")
        audit = self.registry.log.by_tool("run_command")
        self.assertTrue(len(audit) >= 1)


# ---------------------------------------------------------------------------
# P0-5: tool-turn budget is configurable; defaults are conservative
# ---------------------------------------------------------------------------

class TestToolTurnBudget(P0TestBase):
    def test_session_own_default_unchanged(self):
        self.assertEqual(DEFAULT_MAX_TOOL_TURNS, 6)
        session = ToolUseSession(provider=MockAIProvider(), tool_registry=self.registry,
                                 allowed_tools={"read_file"})
        self.assertEqual(session.max_tool_turns, 6)

    def test_orchestration_path_defaults_to_12_and_is_configurable(self):
        self.assertEqual(ResourceLimits().max_tool_turns, 12)
        self.assertEqual(ResourceLimits(max_tool_turns=3).max_tool_turns, 3)

    def test_tight_budget_bounds_a_long_coding_flow(self):
        many_writes = [
            {"text": f"write {i}", "tool_calls": [
                {"id": f"c{i}", "name": "write_file", "arguments": {"path": f"f{i}.txt", "content": "x"}}]}
            for i in range(5)
        ]
        many_writes.append({"text": "done", "tool_calls": []})
        result = Orchestrator(
            provider=MockAIProvider(script=many_writes),
            tool_registry=self.registry,
            limits=ResourceLimits(max_tool_turns=2),
        ).run("Build a small app project.")
        self.assertEqual(result.status, "FAILED")
        # the coding worker MUST have been stopped by the turn budget
        code_result = next(r for sid, r in result.worker_results.items() if sid.endswith("-code"))
        self.assertNotEqual(code_result.status, "COMPLETED")
        # bounded: far fewer than all 5 writes happened
        written = [f for f in os.listdir(self.sandbox) if f.startswith("f")]
        self.assertLess(len(written), 5)

    def test_default_budget_allows_a_seven_turn_flow(self):
        """Under the orchestration default (12) a flow that EXCEEDS the bare
        ToolUseSession default (6) still completes - proving the configurable
        path, not the hardcoded one, is in effect."""
        writes = [
            {"text": f"write {i}", "tool_calls": [
                {"id": f"c{i}", "name": "write_file", "arguments": {"path": f"f{i}.txt", "content": "x"}}]}
            for i in range(7)
        ]
        writes.append({"text": "done", "tool_calls": []})
        from agent.coding_worker import CodingAgentWorker
        p = MockAIProvider(script=writes)
        worker = CodingAgentWorker(provider=p, tool_registry=self.registry)  # default budget path
        # orchestration orchestrates via SharedTaskState; here we set the same
        # seam (max_tool_turns=12) directly the way the adapter does.
        worker.session.max_tool_turns = 12
        result = worker.execute_task("write several files")
        # 7 write turns + 1 done turn = 8 > old 6 default; must have completed
        self.assertEqual(result.status, "COMPLETED")

    def test_execution_controls_snapshot_includes_budget(self):
        from orchestration.schemas import ResourceLimits as RL
        from memory import ProjectMemory
        from execution.session import ExecutionSession
        pm = ProjectMemory(path=os.path.join(self.sandbox, "pm.json"))
        session = ExecutionSession(provider=MockAIProvider(), tool_registry=self.registry,
                                   project_memory=pm, limits=RL(max_tool_turns=9))
        controls = session._bind_controls()
        self.assertEqual(controls["limits"]["max_tool_turns"], 9)


# ---------------------------------------------------------------------------
# P0-4: bounded (max 1) top-level PlanReviser iteration, REJECTED-only
# ---------------------------------------------------------------------------

class TestPlanRevisionBounded(P0TestBase):
    def _planned(self, script, max_revisions=1, max_repair_dispatches=0):
        po = PlannedOrchestrator(provider=MockAIProvider(script=script),
                                 tool_registry=self.registry, max_revisions=max_revisions)
        po.orchestrator.max_repair_dispatches = max_repair_dispatches
        return po

    def test_rejected_gate_triggers_exactly_one_revision_then_success(self):
        po = self._planned(REVISION_SCRIPT)
        result = po.run("Build a small runnable calculator project.")
        names = [e["event"] for e in result["events"]]
        self.assertEqual(names.count("PLAN_REVISION_STARTED"), 1)
        self.assertEqual(names.count("PLAN_REVISION_VALIDATED"), 1)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["plan_revision"]["attempts"], 1)
        self.assertEqual(result["plan_revision"]["plan_version"], 2)
        # the revised plan's acceptance criteria are consistent with reality
        self.assertIn("main.py", sorted(os.listdir(self.sandbox)))

    def test_no_revision_when_budget_zero(self):
        # pass 1 (and only pass) lacks main.py -> REJECTED -> no revision
        po = self._planned(REVISION_SCRIPT, max_revisions=0)
        result = po.run("Build a small runnable calculator project.")
        names = [e["event"] for e in result["events"]]
        self.assertNotIn("PLAN_REVISION_STARTED", names)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["plan_revision"]["attempts"], 0)

    def test_blocked_integrity_never_triggers_revision(self):
        """A weakened-test attempt is BLOCKED by the real integrity checker;
        BLOCKED must NEVER be retried through plan revision (security)."""
        self.registry.impl.write_file("calculator.py", "def subtract(a, b):\n    return a + b\n")
        self.registry.impl.write_file("test_calculator.py",
            "import unittest\nfrom calculator import subtract\nclass T(unittest.TestCase):\n"
            "    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n")
        cheat_script = [
            {"text": "cheating", "tool_calls": [
                {"id": "c1", "name": "edit_file", "arguments": {
                    "path": "test_calculator.py", "old_str": "self.assertEqual(subtract(5, 3), 2)",
                    "new_str": "self.assertTrue(True)"}}]},
            {"text": "done", "tool_calls": []},
        ]
        po = self._planned(cheat_script, max_revisions=1)
        result = po.run("Fix the calculator project subtract implementation.")
        names = [e["event"] for e in result["events"]]
        self.assertNotIn("PLAN_REVISION_STARTED", names)
        self.assertEqual(result["quality_decision"], "BLOCKED")
        self.assertEqual(result["status"], "FAILED")


# ---------------------------------------------------------------------------
# MANDATORY ACCEPTANCE (user directive): deterministic end-to-end
# ---------------------------------------------------------------------------

class TestMandatoryAcceptance(P0TestBase):
    def test_build_small_runnable_project_with_intentional_defect(self):
        """Scenario 'build a small runnable software project' with an
        intentional defect, through REAL file writes/edits, the REAL
        ToolRegistry + permissions + classifier, REAL subprocesses, REAL
        failure detection, REAL repair, REAL retest, REAL run evidence,
        REAL verification and the REAL quality gate. Nothing is mocked
        except the model's choices (scripted turns)."""
        before_snapshot = dict(os.listdir(self.sandbox))
        self.assertEqual(before_snapshot, {})

        result = Orchestrator(
            provider=MockAIProvider(script=DEFECT_REPAIR_SCRIPT),
            tool_registry=self.registry,
        ).run("Build a small runnable software project.")

        events = [e["event"] for e in result.events]

        # (1) the defect was REALLY detected by real execution evidence
        passes = [t.get("passed") for t in result.aggregation.get("tests", [])]
        self.assertEqual(passes[0], False, "defect must fail tests before repair")

        # (2) repair was dispatched through the normal orchestration path
        self.assertIn("REPAIR_DISPATCHED", events)
        self.assertIn("REPAIR_RESOLVED", events)
        self.assertIn("REPAIR_RETRY_RESOLVED", events)

        # (3) the repair CHANGED the project (file on disk proves it)
        with open(os.path.join(self.sandbox, "main.py")) as f:
            final_source = f.read()
        self.assertIn("DEFECT", BUGGY_APP)
        self.assertNotIn("DEFECT", final_source)
        self.assertIn(FIXED_MARKER, final_source)

        # (4) the FINAL subprocess actually succeeds (run evidence)
        runs = result.aggregation.get("runs") or []
        self.assertTrue(runs)
        final_run = runs[-1]
        self.assertEqual(final_run["command"], "python3 -B main.py")
        self.assertEqual(final_run["returncode"], 0), final_run
        self.assertTrue(final_run["ok"])
        self.assertIn("APP-OUT 5", final_run["stdout"])  # add(2,3) now returns 5

        # (5) verification + gate truthfully approve the repaired artifact
        self.assertTrue(result.aggregation.get("task_verification", {}).get("passed"))
        self.assertEqual(result.quality_decision, "APPROVED")
        self.assertEqual(result.status, "COMPLETED")

        # (6) integrity of the evidence trail: original failure preserved AND
        # recovery tracked; test chronology shows real detection->repair->pass
        self.assertTrue(any(s.endswith("-test") for s in result.aggregation["failed_workers"]))
        self.assertTrue(any(s.endswith("-test") for s in result.aggregation.get("recovered_failures", [])))
        self.assertIn(True, passes[1:])

        # (7) tool audit proves every write/edit/run went through the registry
        audit_entries = self.registry.log.by_tool("write_file") + \
            self.registry.log.by_tool("edit_file") + self.registry.log.by_tool("run_command")
        self.assertTrue(len(audit_entries) >= 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
