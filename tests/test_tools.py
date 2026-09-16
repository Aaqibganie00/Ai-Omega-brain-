"""
Tests for the tool system: every tool's happy path, permission denials,
and invalid-argument handling. Run with:  python3 -m pytest tests/ -v
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ToolRegistry, ToolCall, ToolStatus, Permission, PermissionConfig


class ToolSystemTestBase(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.mkdtemp(prefix="omega_tool_test_")
        self.registry = ToolRegistry(base_dir=self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def call(self, tool_name, **kwargs):
        return self.registry.execute(ToolCall(tool_name=tool_name, arguments=kwargs))


class TestWriteAndReadFile(ToolSystemTestBase):
    def test_write_then_read(self):
        w = self.call("write_file", path="hello.txt", content="hi there")
        self.assertEqual(w.status, ToolStatus.SUCCESS)
        r = self.call("read_file", path="hello.txt")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertEqual(r.output["content"], "hi there")

    def test_read_missing_file_is_invalid(self):
        r = self.call("read_file", path="does_not_exist.txt")
        self.assertEqual(r.status, ToolStatus.INVALID)
        self.assertIn("No such file", r.error)

    def test_write_no_overwrite_conflict(self):
        self.call("write_file", path="a.txt", content="v1")
        r = self.call("write_file", path="a.txt", content="v2", overwrite=False)
        self.assertEqual(r.status, ToolStatus.INVALID)

    def test_path_traversal_is_blocked(self):
        r = self.call("read_file", path="../../etc/passwd")
        self.assertIn(r.status, (ToolStatus.DENIED, ToolStatus.INVALID))


class TestEditFile(ToolSystemTestBase):
    def test_edit_replaces_unique_match(self):
        self.call("write_file", path="f.py", content="x = 1\ny = 2\n")
        r = self.call("edit_file", path="f.py", old_str="x = 1", new_str="x = 99")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        content = self.call("read_file", path="f.py").output["content"]
        self.assertIn("x = 99", content)

    def test_edit_non_unique_match_fails(self):
        self.call("write_file", path="f.py", content="x = 1\nx = 1\n")
        r = self.call("edit_file", path="f.py", old_str="x = 1", new_str="x = 2")
        self.assertEqual(r.status, ToolStatus.INVALID)

    def test_edit_no_match_fails(self):
        self.call("write_file", path="f.py", content="a = 1\n")
        r = self.call("edit_file", path="f.py", old_str="not_present", new_str="x")
        self.assertEqual(r.status, ToolStatus.INVALID)


class TestListAndSearch(ToolSystemTestBase):
    def test_list_files(self):
        self.call("write_file", path="a.txt", content="1")
        self.call("write_file", path="sub/b.txt", content="2")
        r = self.call("list_files", path=".")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertIn("a.txt", r.output["entries"])

    def test_list_files_recursive(self):
        self.call("write_file", path="a.txt", content="1")
        self.call("write_file", path="sub/b.txt", content="2")
        r = self.call("list_files", path=".", recursive=True)
        rels = r.output["entries"]
        self.assertTrue(any("b.txt" in e for e in rels))

    def test_search_files(self):
        self.call("write_file", path="a.py", content="def foo():\n    return 42\n")
        r = self.call("search_files", pattern="return")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertEqual(len(r.output["matches"]), 1)


class TestDirectoryAndProject(ToolSystemTestBase):
    def test_create_directory(self):
        r = self.call("create_directory", path="new_dir/nested")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertTrue(os.path.isdir(os.path.join(self.sandbox, "new_dir/nested")))

    def test_create_project_writes_multiple_files(self):
        r = self.call("create_project", path="myapp", files={
            "main.py": "print('hi')\n",
            "README.md": "# myapp\n",
        })
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertTrue(os.path.exists(os.path.join(self.sandbox, "myapp/main.py")))

    def test_inspect_project(self):
        self.call("write_file", path="a.py", content="x=1")
        self.call("write_file", path="b.py", content="y=2")
        r = self.call("inspect_project")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertEqual(r.output["file_count"], 2)
        self.assertIn(".py", r.output["by_extension"])


class TestRunCommandAndTests(ToolSystemTestBase):
    def test_run_command_success(self):
        r = self.call("run_command", command="echo hello")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertIn("hello", r.output["stdout"])

    def test_run_command_nonzero_exit_is_still_success_status(self):
        # the SHELL command failing (bad exit code) is not a tool-system
        # error - the tool executed correctly and reported the result
        r = self.call("run_command", command="exit 1")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertEqual(r.output["returncode"], 1)

    def test_run_command_timeout(self):
        r = self.call("run_command", command="sleep 5", timeout=1)
        self.assertEqual(r.status, ToolStatus.TIMEOUT)

    def test_run_tests_on_passing_suite(self):
        # default runner is stdlib unittest, so test files must define
        # unittest.TestCase subclasses (bare pytest-style functions are
        # NOT discovered by `unittest discover` - this matches that
        # default's real behavior rather than assuming pytest).
        self.call("write_file", path="test_sample.py", content=(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_ok(self):\n"
            "        self.assertEqual(1 + 1, 2)\n"
        ))
        r = self.call("run_tests")
        self.assertEqual(r.status, ToolStatus.SUCCESS)
        self.assertTrue(r.output["passed"])

    def test_run_tests_on_failing_suite(self):
        self.call("write_file", path="test_sample.py", content=(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_bad(self):\n"
            "        self.assertEqual(1, 2)\n"
        ))
        r = self.call("run_tests")
        self.assertEqual(r.status, ToolStatus.SUCCESS)  # tool ran fine
        self.assertFalse(r.output["passed"])              # but the suite failed


class TestPermissionEnforcement(ToolSystemTestBase):
    def test_write_denied_without_write_permission(self):
        registry = ToolRegistry(
            base_dir=self.sandbox,
            permission_config=PermissionConfig(granted={Permission.READ}),
        )
        r = registry.execute(ToolCall(tool_name="write_file", arguments={"path": "x.txt", "content": "y"}))
        self.assertEqual(r.status, ToolStatus.DENIED)
        self.assertFalse(os.path.exists(os.path.join(self.sandbox, "x.txt")))

    def test_read_denied_without_read_permission(self):
        registry = ToolRegistry(
            base_dir=self.sandbox,
            permission_config=PermissionConfig(granted={Permission.WRITE}),
        )
        registry.execute(ToolCall(tool_name="write_file", arguments={"path": "x.txt", "content": "y"}))
        r = registry.execute(ToolCall(tool_name="read_file", arguments={"path": "x.txt"}))
        self.assertEqual(r.status, ToolStatus.DENIED)

    def test_run_command_denied_without_execute_permission(self):
        registry = ToolRegistry(
            base_dir=self.sandbox,
            permission_config=PermissionConfig(granted={Permission.READ, Permission.WRITE}),
        )
        r = registry.execute(ToolCall(tool_name="run_command", arguments={"command": "echo hi"}))
        self.assertEqual(r.status, ToolStatus.DENIED)

    def test_destructive_command_denied_without_destructive_permission(self):
        registry = ToolRegistry(
            base_dir=self.sandbox,
            permission_config=PermissionConfig(granted={Permission.READ, Permission.WRITE, Permission.EXECUTE}),
        )
        r = registry.execute(ToolCall(tool_name="run_command", arguments={"command": "rm -rf somedir"}))
        self.assertEqual(r.status, ToolStatus.DENIED)
        self.assertIn("DESTRUCTIVE", r.error)

    def test_network_command_denied_without_network_permission(self):
        registry = ToolRegistry(
            base_dir=self.sandbox,
            permission_config=PermissionConfig(granted={Permission.READ, Permission.WRITE, Permission.EXECUTE}),
        )
        r = registry.execute(ToolCall(tool_name="run_command", arguments={"command": "curl http://example.com"}))
        self.assertEqual(r.status, ToolStatus.DENIED)
        self.assertIn("NETWORK", r.error)

    def test_destructive_command_allowed_when_granted(self):
        registry = ToolRegistry(
            base_dir=self.sandbox,
            permission_config=PermissionConfig(granted={
                Permission.READ, Permission.WRITE, Permission.EXECUTE, Permission.DESTRUCTIVE
            }),
        )
        os.makedirs(os.path.join(self.sandbox, "throwaway"))
        r = registry.execute(ToolCall(tool_name="run_command", arguments={"command": "rm -rf throwaway"}))
        self.assertEqual(r.status, ToolStatus.SUCCESS)


class TestInvalidArguments(ToolSystemTestBase):
    def test_unknown_tool(self):
        r = self.call("delete_universe", target="everything")
        self.assertEqual(r.status, ToolStatus.INVALID)
        self.assertIn("Unknown tool", r.error)

    def test_missing_required_argument(self):
        r = self.call("write_file", path="a.txt")  # missing 'content'
        self.assertEqual(r.status, ToolStatus.INVALID)
        self.assertIn("content", r.error)

    def test_missing_required_argument_search(self):
        r = self.call("search_files")  # missing 'pattern'
        self.assertEqual(r.status, ToolStatus.INVALID)


class TestLoggingAndCallIds(ToolSystemTestBase):
    def test_every_call_gets_a_unique_id(self):
        r1 = self.call("read_file", path="nope1.txt")
        r2 = self.call("read_file", path="nope2.txt")
        self.assertNotEqual(r1.call_id, r2.call_id)

    def test_execution_log_records_every_call(self):
        self.call("write_file", path="a.txt", content="1")
        self.call("read_file", path="a.txt")
        self.call("read_file", path="missing.txt")
        self.assertEqual(len(self.registry.log.entries), 3)

    def test_execution_log_tracks_failures(self):
        self.call("read_file", path="missing.txt")
        failures = self.registry.log.failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["call"]["tool_name"], "read_file")

    def test_result_includes_duration(self):
        r = self.call("run_command", command="echo hi")
        self.assertGreaterEqual(r.duration_seconds, 0)


if __name__ == "__main__":
    unittest.main()
