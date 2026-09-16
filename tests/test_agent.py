"""
Phase 3 tests: agent/tool-use session, security enforcement, end-to-end
mock model<->tool loop, Omega Core integration, memory integration.
Run with: python3 -m unittest tests.test_agent -v
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ToolRegistry, PermissionConfig, Permission
from providers import MockAIProvider, ClaudeProvider, ClaudeConfig, ToolDefinition
from agent import ToolUseSession, CodingAgentWorker, build_tool_definitions
from omega_core import OmegaCore


def make_registry(sandbox, permission_config=None):
    return ToolRegistry(base_dir=sandbox, permission_config=permission_config)


class AgentTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_agent_test_")

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)


# ---------------------------------------------------------------------
# 1. Tool schema conversion
# ---------------------------------------------------------------------

class TestToolAdapter(AgentTestBase):
    def test_only_allowed_tools_are_exposed(self):
        registry = make_registry(self.sandbox)
        defs = build_tool_definitions(registry, allowed_tools={"read_file", "write_file"})
        names = {d.name for d in defs}
        self.assertEqual(names, {"read_file", "write_file"})

    def test_disallowed_registered_tool_is_not_exposed(self):
        registry = make_registry(self.sandbox)
        defs = build_tool_definitions(registry, allowed_tools={"read_file"})
        names = {d.name for d in defs}
        self.assertNotIn("run_command", names)

    def test_definitions_have_valid_schema_shape(self):
        registry = make_registry(self.sandbox)
        defs = build_tool_definitions(registry, allowed_tools={"write_file"})
        self.assertEqual(len(defs), 1)
        d = defs[0]
        self.assertIsInstance(d, ToolDefinition)
        self.assertEqual(d.input_schema["type"], "object")
        self.assertIn("path", d.input_schema["properties"])
        self.assertIn("path", d.input_schema["required"])

    def test_unknown_allowed_tool_name_is_silently_ignored(self):
        registry = make_registry(self.sandbox)
        defs = build_tool_definitions(registry, allowed_tools={"read_file", "not_a_real_tool"})
        names = {d.name for d in defs}
        self.assertEqual(names, {"read_file"})


# ---------------------------------------------------------------------
# 2. End-to-end mock loop: write_file -> read_file -> final answer
# ---------------------------------------------------------------------

class TestEndToEndMockLoop(AgentTestBase):
    def test_full_write_then_read_loop_executes_real_tools(self):
        registry = make_registry(self.sandbox)
        script = [
            {"text": "Creating the file.", "tool_calls": [
                {"id": "call_1", "name": "write_file", "arguments": {"path": "note.txt", "content": "hello omega"}}
            ]},
            {"text": "Reading it back.", "tool_calls": [
                {"id": "call_2", "name": "read_file", "arguments": {"path": "note.txt"}}
            ]},
            {"text": "Done — the file contains: hello omega", "tool_calls": []},
        ]
        provider = MockAIProvider(script=script)
        session = ToolUseSession(provider=provider, tool_registry=registry, allowed_tools={"write_file", "read_file"})

        result = session.run("Create a small text file and then read it.")

        # the model/session loop reports success
        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.turns_used, 2)
        self.assertEqual([c["tool_name"] for c in result.tool_calls_made], ["write_file", "read_file"])
        self.assertTrue(all(c["success"] for c in result.tool_calls_made))

        # the REAL tool registry actually did the work - not faked
        with open(os.path.join(self.sandbox, "note.txt")) as f:
            on_disk = f.read()
        self.assertEqual(on_disk, "hello omega")

        # required event vocabulary present, in a sane order
        event_names = [e["event"] for e in result.events]
        for required in ("TASK_STARTED", "MODEL_REQUEST", "MODEL_RESPONSE",
                          "TOOL_REQUESTED", "PERMISSION_CHECK", "TOOL_EXECUTED",
                          "TOOL_RESULT", "FINAL_RESPONSE"):
            self.assertIn(required, event_names)
        self.assertEqual(event_names[0], "TASK_STARTED")
        self.assertEqual(event_names[-1], "FINAL_RESPONSE")

    def test_events_carry_enough_detail_to_trace_the_task(self):
        registry = make_registry(self.sandbox)
        script = [
            {"text": "writing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "x"}}]},
            {"text": "done", "tool_calls": []},
        ]
        provider = MockAIProvider(script=script)
        session = ToolUseSession(provider=provider, tool_registry=registry, allowed_tools={"write_file"})
        result = session.run("write a file", task_id="trace-task-1")

        tool_events = [e for e in result.events if e["tool_name"] == "write_file"]
        self.assertTrue(all(e["task_id"] == "trace-task-1" for e in tool_events))
        self.assertTrue(all(e["tool_call_id"] == "c1" for e in tool_events))


# ---------------------------------------------------------------------
# 3. Security tests
# ---------------------------------------------------------------------

class TestSecurity(AgentTestBase):
    def test_A_allowed_read_executes(self):
        registry = make_registry(self.sandbox)
        registry.impl.write_file("existing.txt", "content here")
        script = [
            {"text": "reading", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "existing.txt"}}]},
            {"text": "it says: content here", "tool_calls": []},
        ]
        session = ToolUseSession(provider=MockAIProvider(script=script), tool_registry=registry, allowed_tools={"read_file"})
        result = session.run("read the file")
        self.assertTrue(result.tool_calls_made[0]["success"])

    def test_B_denied_write_does_not_execute(self):
        # WRITE permission not granted at the registry level - even though
        # the model is offered write_file, the registry itself must refuse.
        registry = make_registry(self.sandbox, permission_config=PermissionConfig(granted={Permission.READ}))
        script = [
            {"text": "writing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "hack.txt", "content": "nope"}}]},
            {"text": "done", "tool_calls": []},
        ]
        session = ToolUseSession(provider=MockAIProvider(script=script), tool_registry=registry, allowed_tools={"write_file"})
        result = session.run("write a file")

        self.assertFalse(result.tool_calls_made[0]["success"])
        self.assertFalse(os.path.exists(os.path.join(self.sandbox, "hack.txt")))
        tool_result_events = [e for e in result.events if e["event"] == "TOOL_EXECUTED"]
        self.assertFalse(tool_result_events[0]["success"])

    def test_C_unknown_tool_rejected(self):
        registry = make_registry(self.sandbox)
        script = [
            {"text": "trying", "tool_calls": [{"id": "c1", "name": "delete_everything", "arguments": {}}]},
            {"text": "done", "tool_calls": []},
        ]
        # model was never even offered this tool - allowed_tools blocks it
        # before it ever reaches the registry
        session = ToolUseSession(provider=MockAIProvider(script=script), tool_registry=registry, allowed_tools={"read_file"})
        result = session.run("try something bad")
        self.assertFalse(result.tool_calls_made[0]["success"])

    def test_C2_unknown_tool_rejected_by_registry_directly(self):
        # Also confirm the registry itself (not just the allowed_tools
        # pre-filter) rejects tools it doesn't recognize.
        registry = make_registry(self.sandbox)
        script = [
            {"text": "trying", "tool_calls": [{"id": "c1", "name": "delete_everything", "arguments": {}}]},
            {"text": "done", "tool_calls": []},
        ]
        session = ToolUseSession(provider=MockAIProvider(script=script), tool_registry=registry, allowed_tools={"delete_everything"})
        result = session.run("try something bad")
        self.assertFalse(result.tool_calls_made[0]["success"])

    def test_D_invalid_arguments_rejected(self):
        registry = make_registry(self.sandbox)
        script = [
            # write_file called WITHOUT required 'content' argument
            {"text": "writing", "tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "x.txt"}}]},
            {"text": "done", "tool_calls": []},
        ]
        session = ToolUseSession(provider=MockAIProvider(script=script), tool_registry=registry, allowed_tools={"write_file"})
        result = session.run("write something")
        self.assertFalse(result.tool_calls_made[0]["success"])
        self.assertFalse(os.path.exists(os.path.join(self.sandbox, "x.txt")))

    def test_E_tool_call_limit_stops_execution(self):
        registry = make_registry(self.sandbox)
        # a script that would loop forever requesting tools if not capped
        script = [{"text": "again", "tool_calls": [{"id": f"c{i}", "name": "read_file", "arguments": {"path": "nope.txt"}}]} for i in range(50)]
        session = ToolUseSession(provider=MockAIProvider(script=script), tool_registry=registry, allowed_tools={"read_file"}, max_tool_turns=3)
        result = session.run("loop forever")

        self.assertEqual(result.status, "INCOMPLETE")
        self.assertLessEqual(result.turns_used, 3)
        event_names = [e["event"] for e in result.events]
        self.assertIn("TURN_LIMIT_REACHED", event_names)
        # confirms it actually STOPPED - did not run all 50 scripted turns
        self.assertLess(len([e for e in result.events if e["event"] == "TOOL_REQUESTED"]), 50)

    def test_F_path_traversal_rejected(self):
        registry = make_registry(self.sandbox)
        script = [
            {"text": "reading", "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "../../etc/passwd"}}]},
            {"text": "done", "tool_calls": []},
        ]
        session = ToolUseSession(provider=MockAIProvider(script=script), tool_registry=registry, allowed_tools={"read_file"})
        result = session.run("read outside the sandbox")
        self.assertFalse(result.tool_calls_made[0]["success"])

    def test_G_api_key_never_appears_in_session_events_or_errors(self):
        # exercise the real ClaudeProvider's missing-key path through the
        # session (no network call happens - it fails before that)
        registry = make_registry(self.sandbox)
        provider = ClaudeProvider(ClaudeConfig(api_key_env_var="ANTHROPIC_API_KEY_TEST_UNSET"))
        session = ToolUseSession(provider=provider, tool_registry=registry, allowed_tools={"read_file"})
        result = session.run("do something")

        self.assertEqual(result.status, "ERROR")
        dump = str(result.events) + str(result.error)
        self.assertNotIn("sk-", dump)  # no key-shaped secret leaked anywhere


# ---------------------------------------------------------------------
# 4. Coding worker integration
# ---------------------------------------------------------------------

class TestCodingWorkerIntegration(AgentTestBase):
    def test_coding_worker_runs_real_tools(self):
        registry = make_registry(self.sandbox)
        script = [
            {"text": "creating module", "tool_calls": [
                {"id": "c1", "name": "write_file", "arguments": {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}}
            ]},
            {"text": "done", "tool_calls": []},
        ]
        worker = CodingAgentWorker(provider=MockAIProvider(script=script), tool_registry=registry)
        result = worker.execute_task("Create a calculator module.")

        self.assertEqual(result.status, "COMPLETED")
        with open(os.path.join(self.sandbox, "calc.py")) as f:
            content = f.read()
        self.assertIn("def add", content)

    def test_coding_worker_default_tools_include_run_command(self):
        # P0-3 (intentional change): run_command IS part of the default coding
        # toolset now. What must NOT change is the enforcement pipeline - the
        # allowlist alone never grants anything; each call still passes through
        # ToolRegistry permissions + classification.
        registry = make_registry(self.sandbox)
        worker = CodingAgentWorker(provider=MockAIProvider(), tool_registry=registry)
        self.assertIn("run_command", worker.session.allowed_tools)
        # proven here: a NETWORK-classified command is denied by the default
        # policy even though run_command is available to the worker
        from tools.schemas import ToolCall
        result = registry.execute(ToolCall(tool_name="run_command", arguments={"command": "curl -s https://example.com", "cwd": "."}))
        self.assertEqual(result.status.value, "DENIED")
        self.assertIn("NETWORK", result.error)


# ---------------------------------------------------------------------
# 5. Omega Core integration
# ---------------------------------------------------------------------

class TestOmegaCoreIntegration(AgentTestBase):
    def test_execute_agentic_task_runs_through_omega_core(self):
        registry = make_registry(self.sandbox)
        script = [
            {"text": "writing", "tool_calls": [
                {"id": "c1", "name": "write_file", "arguments": {"path": "hello.py", "content": "print('hi')\n"}}
            ]},
            {"text": "done", "tool_calls": []},
        ]
        core = OmegaCore(project_memory_path=os.path.join(self.sandbox, "pm.json"))
        result = core.execute_agentic_task(
            objective="Create a hello world script.",
            provider=MockAIProvider(script=script),
            tool_registry=registry,
            task_id="omega-agentic-1",
        )

        self.assertEqual(result["status"], "COMPLETED")
        self.assertTrue(os.path.exists(os.path.join(self.sandbox, "hello.py")))

    def test_memory_records_structured_summary_not_raw_messages(self):
        registry = make_registry(self.sandbox)
        script = [
            {"text": "writing", "tool_calls": [
                {"id": "c1", "name": "write_file", "arguments": {"path": "out.txt", "content": "data"}}
            ]},
            {"text": "all done", "tool_calls": []},
        ]
        core = OmegaCore(project_memory_path=os.path.join(self.sandbox, "pm.json"))
        result = core.execute_agentic_task(
            objective="write a file", provider=MockAIProvider(script=script),
            tool_registry=registry, task_id="mem-task-1",
        )

        entry = core.project_memory.recall("agent_session:mem-task-1")
        self.assertIsNotNone(entry)
        value = entry["value"]
        self.assertEqual(value["task_id"], "mem-task-1")
        self.assertIn("write_file", value["tools_used"])
        self.assertIn("out.txt", value["files_changed"])
        self.assertEqual(value["execution_status"], "COMPLETED")
        self.assertTrue(entry["verified"])

    def test_memory_never_contains_api_key(self):
        registry = make_registry(self.sandbox)
        os.environ["ANTHROPIC_API_KEY"] = "sk-should-never-be-stored"
        try:
            script = [{"text": "done", "tool_calls": []}]
            core = OmegaCore(project_memory_path=os.path.join(self.sandbox, "pm.json"))
            core.execute_agentic_task(
                objective="say hi", provider=MockAIProvider(script=script),
                tool_registry=registry, task_id="key-safety-task",
            )
            entry = core.project_memory.recall("agent_session:key-safety-task")
            self.assertNotIn("sk-should-never-be-stored", str(entry))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_original_omega_core_flow_still_works(self):
        # confirms Phase 3 additions did not break the pre-existing
        # planner/router/handle_request path.
        core = OmegaCore(project_memory_path=os.path.join(self.sandbox, "pm2.json"))
        result = core.handle_request("Create a simple web application", verbose=False)
        self.assertIn(result["status"], ("COMPLETED", "FAILED"))


if __name__ == "__main__":
    unittest.main()
