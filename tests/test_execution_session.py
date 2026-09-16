"""
Phase 10 tests: ExecutionSession end-to-end coordination, deterministic
control propagation (permissions / limits / timeout), session checkpoints
(create / persist / restore / reject), propagation of verification,
quality-gate and learning/context results, failure propagation, and
confirmation that the security hardening (run_tests gate + permission/
audit pipeline) remains fully intact inside sessions.

Run with: python3 -m unittest tests.test_execution_session -v
"""

import json
import os
import sys
import shutil
import tempfile
import time
import unittest
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ToolRegistry, Permission, PermissionConfig
from memory import ProjectMemory
from providers import MockAIProvider
from orchestration.schemas import ResourceLimits
from execution import (
    ExecutionSession, SessionState, SessionCheckpoint, SessionCheckpointManager,
    validate_checkpoint_data,
)

CALC_SCRIPT = [
    {"text": "implementing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {
        "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"}},
        # P0-1: provide a runnable entry point for the build/run stage.
        {"id": "c1b", "name": "write_file", "arguments": {
            "path": "main.py", "content": "print('calculator entry ok')\n"}}]},
    {"text": "implementing tests", "tool_calls": [{"id": "c2", "name": "write_file", "arguments": {
        "path": "test_calculator.py", "content": (
            "import unittest\nfrom calculator import add\n"
            "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")}}]},
    {"text": "done", "tool_calls": []},
]

CHEAT_SCRIPT = [
    {"text": "weakening", "tool_calls": [{"id": "c1", "name": "edit_file", "arguments": {
        "path": "test_calculator.py", "old_str": "self.assertEqual(subtract(5, 3), 2)",
        "new_str": "self.assertTrue(True)"}}]},
    {"text": "done", "tool_calls": []},
]

RESEARCH_OBJECTIVE = "Research and compare microservices and monolith tradeoffs"


class SlowMockProvider(MockAIProvider):
    """Always late: deterministic way to exercise the worker timeout path."""

    def __init__(self, delay=0.5, **kwargs):
        super().__init__(**kwargs)
        self.delay = delay

    def generate(self, request):
        time.sleep(self.delay)
        return super().generate(request)


class SessionTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_session_test_")
        self.pm_path = os.path.join(self.sandbox, "pm.json")
        self.memory = ProjectMemory(self.pm_path)
        self.registry = ToolRegistry(base_dir=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def make_session(self, script=None, registry=None, limits=None, provider=None):
        return ExecutionSession(
            provider=provider or MockAIProvider(script=script or CALC_SCRIPT),
            tool_registry=registry or self.registry,
            project_memory=self.memory,
            limits=limits,
        )

    def seed_broken_subtract(self, registry):
        registry.impl.write_file("calculator.py", "def subtract(a, b):\n    return a + b\n")
        registry.impl.write_file("test_calculator.py", (
            "import unittest\nfrom calculator import subtract\nclass T(unittest.TestCase):\n"
            "    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n"))


# ---------------------------------------------------------------------------
# 1. Successful end-to-end session
# ---------------------------------------------------------------------------

class TestSuccessfulSession(SessionTestBase):
    def test_successful_end_to_end_session(self):
        session = self.make_session()
        result = session.run("Build a calculator that adds numbers.")

        self.assertEqual(result.state, SessionState.COMPLETED.value)
        self.assertEqual(result.execution_status, "COMPLETED")
        self.assertEqual(result.quality_decision, "APPROVED")
        self.assertTrue(result.verification.get("passed"))
        # P0-1: build plans include the runcheck (build/run) stage.
        self.assertEqual(result.plan_summary["step_count"], 5)
        self.assertEqual(result.checkpoint_id is not None, True)
        self.assertEqual(result.errors, [])
        self.assertGreater(result.duration_seconds, 0)
        # the calc project was really created inside the sandbox
        self.assertTrue(os.path.exists(os.path.join(self.sandbox, "calculator.py")))

    def test_session_events_and_controls_binding(self):
        session = self.make_session()
        result = session.run("Build a calculator that adds numbers.")

        event_names = [e["event"] for e in result.events]
        self.assertIn("SESSION_STARTED", event_names)
        self.assertIn("SESSION_CONTROLS_BOUND", event_names)
        self.assertIn("CHECKPOINT_PERSISTED", event_names)
        self.assertIn("SESSION_COMPLETED", event_names)

        self.assertEqual(result.controls.permission_policy["granted"], ["EXECUTE", "READ", "WRITE"])
        self.assertEqual(result.controls.limits["max_total_workers"], 8)
        self.assertEqual(result.controls.limits["worker_timeout_seconds"], 30.0)
        self.assertIn("run_tests", result.controls.tool_restrictions["registered_tools"])


# ---------------------------------------------------------------------------
# 2-3. Deterministic control propagation
# ---------------------------------------------------------------------------

class TestPermissionPropagation(SessionTestBase):
    def test_read_only_policy_propagates_and_denials_are_logged(self):
        ro_registry = ToolRegistry(base_dir=self.sandbox, permission_config=PermissionConfig(granted={Permission.READ}))
        session = self.make_session(registry=ro_registry)
        result = session.run("Build a calculator that adds numbers.")

        # the session-level policy snapshot equals the registry's actual policy
        self.assertEqual(result.controls.permission_policy["granted"], ["READ"])
        # execution failed rather than silently succeeding
        self.assertEqual(result.state, SessionState.FAILED.value)
        # nothing was written. With a READ-only policy the coding worker is
        # blocked at SELECTION (Phase 6 WorkerSelector checks its required
        # permissions before it ever starts) - so the correct propagation
        # evidence is the permission-rejection error, observable in the
        # session's propagated errors.
        self.assertEqual([f for f in os.listdir(self.sandbox) if f != "pm.json"], [])
        self.assertTrue(any("permissions" in e for e in result.errors))
        cp, reasons = session.restore_checkpoint()
        self.assertEqual(reasons, [])
        self.assertEqual(cp.state, "FAILED")


class TestLimitPropagation(SessionTestBase):
    def test_max_total_workers_limit_propagates_into_orchestration(self):
        limits = ResourceLimits(max_total_workers=1)
        session = self.make_session(limits=limits)
        result = session.run("Build a calculator that adds numbers.")

        self.assertEqual(result.execution_status, "LIMIT_REACHED")
        self.assertNotEqual(result.quality_decision, "APPROVED")
        self.assertEqual(result.state, SessionState.FAILED.value)
        orch_events = result.raw_result["plan_result"]["orchestration_result"].events
        self.assertTrue(any(e["event"] == "WORKER_LIMIT_REACHED" for e in orch_events))
        self.assertEqual(result.controls.limits["max_total_workers"], 1)


# ---------------------------------------------------------------------------
# 4. Timeout behavior (worker_timeout_seconds now enforced)
# ---------------------------------------------------------------------------

class TestWorkerTimeout(SessionTestBase):
    def test_timeout_fails_explicitly_never_silently(self):
        limits = ResourceLimits(worker_timeout_seconds=0.1)
        session = self.make_session(limits=limits, provider=SlowMockProvider(delay=0.5))
        result = session.run(RESEARCH_OBJECTIVE)

        # explicit failure state, NOT success
        self.assertEqual(result.state, SessionState.FAILED.value)
        self.assertNotEqual(result.quality_decision, "APPROVED")
        # timeout is observable: events + failure state + error text
        self.assertTrue(len(result.recovery["timed_out"]) >= 1)
        self.assertTrue(any("worker_timeout_seconds" in e for e in result.errors))
        self.assertTrue(any(w.endswith("-research-a") or "research" in w for w in result.recovery["failed_workers"]))
        # checkpoint captures the failed state
        self.assertEqual(result.state, SessionState.FAILED.value)
        restored, reasons = session.restore_checkpoint()
        self.assertEqual(reasons, [])
        self.assertEqual(restored.state, "FAILED")
        self.assertTrue(len(restored.failed_steps) >= 1)

    def test_timeout_disabled_preserves_existing_behavior(self):
        # worker_timeout_seconds=None disables the deadline entirely: even a
        # deliberately slow provider completes normally, with zero timeout
        # events - identical to pre-Phase-10 behavior.
        limits = ResourceLimits(worker_timeout_seconds=None)
        session = self.make_session(limits=limits, provider=SlowMockProvider(delay=0.2, script=CALC_SCRIPT))
        result = session.run("Build a calculator that adds numbers.")

        self.assertEqual(result.state, SessionState.COMPLETED.value)
        self.assertEqual(result.quality_decision, "APPROVED")
        self.assertEqual(result.recovery["timed_out"], [])
        orch_events = result.raw_result["plan_result"]["orchestration_result"].events
        self.assertFalse(any(e["event"] == "WORKER_TIMEOUT" for e in orch_events))

    def test_default_timeout_does_not_change_happy_path(self):
        session = self.make_session()  # default 30s - workers are instant
        result = session.run("Build a calculator that adds numbers.")
        self.assertEqual(result.state, SessionState.COMPLETED.value)
        self.assertEqual(result.recovery["timed_out"], [])


# ---------------------------------------------------------------------------
# 5. Checkpoint creation / persistence / restore / rejection
# ---------------------------------------------------------------------------

class TestCheckpoints(SessionTestBase):
    def test_checkpoint_creation_contents(self):
        session = self.make_session()
        result = session.run("Build a calculator that adds numbers.")

        restored, reasons = session.restore_checkpoint()
        self.assertEqual(reasons, [])
        cp = restored
        self.assertEqual(cp.checkpoint_id, result.checkpoint_id)
        self.assertEqual(cp.session_id, session.session_id)
        self.assertEqual(cp.task_id, result.task_id)
        self.assertEqual(cp.state, "COMPLETED")
        self.assertEqual(cp.schema_version, 1)
        self.assertEqual(len(cp.completed_steps), 5)
        self.assertEqual(cp.failed_steps, [])
        self.assertEqual(cp.remaining_steps, [])
        self.assertEqual(cp.plan_checkpoint["plan_version"], 1)
        self.assertEqual(cp.permission_policy["granted"], ["EXECUTE", "READ", "WRITE"])
        self.assertEqual(cp.limits["max_total_workers"], 8)
        self.assertEqual(cp.quality_decision, "APPROVED")
        self.assertTrue(cp.evidence["verification_passed"])
        self.assertTrue(cp.evidence["integrity_ok"])
        # the result itself is fully JSON-serializable
        json.dumps(result.to_dict())

    def test_checkpoint_persisted_to_disk(self):
        session = self.make_session()
        result = session.run("Build a calculator that adds numbers.")

        with open(self.pm_path) as f:
            stored = json.load(f)
        key = f"session_checkpoint:{session.session_id}"
        self.assertIn(key, stored)
        self.assertEqual(stored[key]["value"]["checkpoint_id"], result.checkpoint_id)
        self.assertTrue(stored[key]["verified"])  # COMPLETED checkpoints are verified-trust entries
        # session summary recorded too (same ProjectMemory pattern as prior phases)
        self.assertIn(f"execution_session:{result.task_id}", stored)

    def test_checkpoint_restore_reconstructs_state_across_instances(self):
        session = self.make_session()
        result = session.run("Build a calculator that adds numbers.")

        # a brand-new manager + brand-new ProjectMemory handle on the same file
        fresh_manager = SessionCheckpointManager(ProjectMemory(self.pm_path))
        restored, reasons = fresh_manager.restore(session.session_id)
        self.assertEqual(reasons, [])
        self.assertEqual(asdict(restored), asdict(session.restore_checkpoint()[0]))
        self.assertEqual(restored.completed_steps, result.raw_result["plan_result"]["checkpoint"].completed_steps)

    def test_invalid_checkpoint_rejected_on_persist_without_corruption(self):
        session = self.make_session()
        result = session.run("Build a calculator that adds numbers.")
        manager = session.checkpoints
        original, _ = manager.restore(session.session_id)

        for tamper in (
            lambda d: d.pop("task_id"),
            lambda d: d.update(schema_version=999),
            lambda d: d.update(completed_steps="not-a-list"),
            lambda d: d.update(permission_policy={"granted": ["READ", "INVOKE"]}),
        ):
            bad = asdict(original)
            tamper(bad)
            self.assertTrue(len(validate_checkpoint_data(bad)) >= 1)
            ok, reasons = manager.persist(bad)
            self.assertFalse(ok)
            self.assertTrue(len(reasons) >= 1)
            # the previously persisted VALID checkpoint is untouched
            still_there, _ = manager.restore(session.session_id)
            self.assertEqual(asdict(still_there), asdict(original))

    def test_corrupted_checkpoint_rejected_on_restore_readonly(self):
        session = self.make_session()
        session.run("Build a calculator that adds numbers.")
        manager = session.checkpoints
        before = dict(self.memory.all())

        # corrupt the stored payload in place
        entry = self.memory.recall(manager.stored_key(session.session_id))
        entry["value"].pop("session_id")
        entry["value"]["state"] = "BOGUS"
        restored, reasons = manager.restore(session.session_id)
        self.assertIsNone(restored)
        self.assertTrue(len(reasons) >= 1)
        # restore never mutates any stored state, even on rejection
        self.assertEqual(self.memory.all(), before)

    def test_restore_missing_checkpoint(self):
        manager = SessionCheckpointManager(self.memory)
        restored, reasons = manager.restore("ses-does-not-exist")
        self.assertIsNone(restored)
        self.assertTrue(any("ses-does-not-exist" in r for r in reasons))


# ---------------------------------------------------------------------------
# 6. Verification / Quality Gate / Learning-context propagation
# ---------------------------------------------------------------------------

class TestResultPropagation(SessionTestBase):
    def test_verification_result_propagates_on_success(self):
        session = self.make_session()
        result = session.run("Build a calculator that adds numbers.")
        self.assertTrue(result.verification["passed"])
        self.assertTrue(result.verification["checks"]["tests_passed"])
        self.assertTrue(result.integrity["integrity_ok"])

    def test_quality_gate_blocked_propagates_when_test_weakened(self):
        self.seed_broken_subtract(self.registry)
        session = self.make_session(script=CHEAT_SCRIPT)
        result = session.run("Fix the calculator project subtract implementation.")

        self.assertEqual(result.quality_decision, "BLOCKED")
        self.assertFalse(result.integrity["integrity_ok"])
        self.assertTrue(len(result.integrity["suspicious"]) >= 1)
        self.assertEqual(result.state, SessionState.FAILED.value)
        # and the learning layer never treats this as verified knowledge
        self.assertNotEqual(result.learning["evidence_confidence"], "HIGH")

    def test_learning_and_context_propagation_across_sessions(self):
        # first run: empty experience store -> empty advisory context
        first = self.make_session().run("Build a calculator that adds numbers.")
        self.assertEqual(first.context["task_type"], "calculator")
        self.assertEqual(first.context["relevant_experience_ids"], [])
        self.assertEqual(first.learning["evidence_confidence"], "HIGH")

        # second session on the SAME memory: prior verified experience is
        # retrieved as advisory context (never authority)
        second_session = self.make_session()
        second = second_session.run("Build a calculator that adds numbers.")
        self.assertGreaterEqual(len(second.context["relevant_experience_ids"]), 1)
        self.assertGreaterEqual(second.learning["retrieved_experience_count"], 1)
        plan = second.raw_result["plan_result"]["plan"]
        advisory = [a for a in plan.assumptions if "Prior experience" in a]
        self.assertTrue(len(advisory) >= 1)

    def test_failure_propagation(self):
        # script writes a WRONG implementation but correct tests
        bad_script = [
            {"text": "implementing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {
                "path": "calculator.py", "content": "def add(a, b):\n    return a - b\n"}}]},
            {"text": "tests", "tool_calls": [{"id": "c2", "name": "write_file", "arguments": {
                "path": "test_calculator.py", "content": (
                    "import unittest\nfrom calculator import add\n"
                    "class T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")}}]},
            {"text": "done", "tool_calls": []},
        ]
        session = self.make_session(script=bad_script)
        result = session.run("Build a calculator that adds numbers.")

        self.assertEqual(result.state, SessionState.FAILED.value)
        self.assertNotEqual(result.quality_decision, "APPROVED")
        self.assertTrue(len(result.errors) >= 1)
        self.assertTrue(len(result.recovery["failed_workers"]) >= 1)
        self.assertFalse(result.verification["passed"])
        cp, reasons = session.restore_checkpoint()
        self.assertEqual(reasons, [])
        self.assertEqual(cp.state, "FAILED")
        self.assertTrue(len(cp.failed_steps) >= 1)
        self.assertNotEqual(result.learning["evidence_confidence"], "HIGH")


# ---------------------------------------------------------------------------
# 7. Security hardening remains intact inside sessions
# ---------------------------------------------------------------------------

class TestSecurityRemainsIntact(SessionTestBase):
    def test_session_does_not_reopen_permission_bypasses(self):
        # READ+WRITE granted, EXECUTE withheld: writes by the coding worker
        # succeed, but no EXECUTE-requiring tool may ever run - not even via
        # the (now classification-gated) run_tests path.
        rw_registry = ToolRegistry(base_dir=self.sandbox, permission_config=PermissionConfig(granted={Permission.READ, Permission.WRITE}))
        result = self.make_session(registry=rw_registry).run("Build a calculator that adds numbers.")

        # writes went through legitimately...
        self.assertTrue(os.path.exists(os.path.join(self.sandbox, "calculator.py")))
        # ...but the task still FAILED: the testing worker (requires EXECUTE)
        # cannot be selected, and no EXECUTE-gated tool ran anywhere
        self.assertEqual(result.state, SessionState.FAILED.value)
        executed_with_execute_perm = [
            e for e in rw_registry.log.entries
            if e["result"]["status"] == "SUCCESS" and "EXECUTE" in e["result"]["permissions_used"]
        ]
        self.assertEqual(executed_with_execute_perm, [])
        # the session architecture adds no shadow execution path: every tool
        # call during the entire session went through the single registry's log
        self.assertTrue(all(e["call"]["tool_name"] in rw_registry.available_tools() for e in rw_registry.log.entries))

    def test_run_tests_classification_gate_unchanged(self):
        from tools import ToolCall, ToolStatus
        denied = self.registry.execute(ToolCall(tool_name="run_tests", arguments={"test_command": "rm -rf /"}))
        self.assertEqual(denied.status, ToolStatus.DENIED)
        self.assertIn("DESTRUCTIVE", denied.error)


if __name__ == "__main__":
    unittest.main()
