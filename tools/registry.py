"""
TOOL SYSTEM — REGISTRY
-------------------------
The one interface Omega Core / workers use to call any tool. Every call
goes through the same pipeline, in this order, no exceptions:

    REQUEST -> VALIDATE -> PERMISSION CHECK -> EXECUTE -> RESULT -> LOG

This is what makes the system "modular": a worker never touches
ToolImplementations directly. It builds a ToolCall and hands it to
ToolRegistry.execute(). Adding a new tool means registering a spec here -
nothing about the calling convention changes.
"""

import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

from .schemas import ToolCall, ToolResult, ToolStatus, Permission
from .permissions import PermissionManager, PermissionConfig
from .implementations import ToolImplementations, classify_command, SandboxViolation


@dataclass
class ToolSpec:
    name: str
    fn: Callable
    required_args: set
    base_permissions: set                       # permissions always required
    dynamic_permissions: Optional[Callable] = None  # fn(args) -> extra permissions needed
    description: str = ""


class ExecutionLog:
    """Append-only log of every tool call attempted, regardless of outcome."""

    def __init__(self):
        self.entries: list[dict] = []

    def record(self, call: ToolCall, result: ToolResult):
        self.entries.append({
            "call": call.to_dict(),
            "result": result.to_dict(),
        })

    def failures(self):
        return [e for e in self.entries if e["result"]["status"] != "SUCCESS"]

    def by_tool(self, tool_name: str):
        return [e for e in self.entries if e["call"]["tool_name"] == tool_name]


class ToolRegistry:
    def __init__(self, base_dir: str, permission_config: PermissionConfig = None):
        self.impl = ToolImplementations(base_dir)
        self.permissions = PermissionManager(permission_config or PermissionConfig())
        self.log = ExecutionLog()
        self._specs: dict[str, ToolSpec] = {}
        self._register_default_tools()

    # ---- registration ----

    def register(self, spec: ToolSpec):
        self._specs[spec.name] = spec

    def _register_default_tools(self):
        impl = self.impl

        self.register(ToolSpec(
            name="read_file", fn=impl.read_file,
            required_args={"path"}, base_permissions={Permission.READ},
            description="Read a file's contents (sandboxed to the project root).",
        ))
        self.register(ToolSpec(
            name="write_file", fn=impl.write_file,
            required_args={"path", "content"}, base_permissions={Permission.WRITE},
            description="Create or overwrite a file.",
        ))
        self.register(ToolSpec(
            name="edit_file", fn=impl.edit_file,
            required_args={"path", "old_str", "new_str"}, base_permissions={Permission.WRITE},
            description="Replace a unique substring in an existing file.",
        ))
        self.register(ToolSpec(
            name="list_files", fn=impl.list_files,
            required_args=set(), base_permissions={Permission.READ},
            description="List files/directories under a path.",
        ))
        self.register(ToolSpec(
            name="search_files", fn=impl.search_files,
            required_args={"pattern"}, base_permissions={Permission.READ},
            description="Regex search across files under a path.",
        ))
        self.register(ToolSpec(
            name="create_directory", fn=impl.create_directory,
            required_args={"path"}, base_permissions={Permission.WRITE},
            description="Create a directory (and parents) under the project root.",
        ))
        self.register(ToolSpec(
            name="create_project", fn=impl.create_project,
            required_args={"path", "files"}, base_permissions={Permission.WRITE},
            description="Create a directory with multiple files in one call.",
        ))
        self.register(ToolSpec(
            name="run_command", fn=impl.run_command,
            required_args={"command"}, base_permissions={Permission.EXECUTE},
            dynamic_permissions=lambda args: classify_command(args.get("command", "")),
            description="Run a shell command inside the sandboxed project root, with a timeout.",
        ))
        self.register(ToolSpec(
            name="run_tests", fn=impl.run_tests,
            required_args=set(), base_permissions={Permission.EXECUTE},
            # Same command-classification gate as run_command, applied to the
            # caller-supplied test_command. Without this, run_tests was an
            # unclassified shell backdoor model-reachable via run_tests's
            # test_command argument: "rm -rf /" as a test_command needed only
            # EXECUTE (granted by default) while the identical string through
            # run_command was correctly denied. The default test command
            # classifies to no extra permissions, so legitimate test
            # execution is unaffected.
            dynamic_permissions=lambda args: classify_command(args.get("test_command") or ""),
            description="Run the project's test suite (default: pytest) with a timeout. Custom commands are classified like run_command.",
        ))
        self.register(ToolSpec(
            name="inspect_project", fn=impl.inspect_project,
            required_args=set(), base_permissions={Permission.READ},
            description="Summarize project structure: file counts, extensions, detected entry points.",
        ))

    def available_tools(self) -> list[str]:
        return sorted(self._specs.keys())

    # ---- the pipeline ----

    def execute(self, call: ToolCall) -> ToolResult:
        start = time.time()

        # 1. REQUEST — call already constructed by caller (has call_id, etc.)

        # 2. VALIDATE
        spec = self._specs.get(call.tool_name)
        if spec is None:
            result = ToolResult(
                call_id=call.call_id, tool_name=call.tool_name, status=ToolStatus.INVALID,
                error=f"Unknown tool: '{call.tool_name}'. Available: {self.available_tools()}",
                duration_seconds=time.time() - start,
            )
            self.log.record(call, result)
            return result

        missing_args = spec.required_args - set(call.arguments.keys())
        if missing_args:
            result = ToolResult(
                call_id=call.call_id, tool_name=call.tool_name, status=ToolStatus.INVALID,
                error=f"Missing required arguments: {sorted(missing_args)}",
                duration_seconds=time.time() - start,
            )
            self.log.record(call, result)
            return result

        # 3. PERMISSION CHECK
        required = set(spec.base_permissions)
        if spec.dynamic_permissions:
            required |= spec.dynamic_permissions(call.arguments)

        allowed, missing = self.permissions.check(required)
        if not allowed:
            result = ToolResult(
                call_id=call.call_id, tool_name=call.tool_name, status=ToolStatus.DENIED,
                error=f"Permission denied. Missing: {sorted(p.value for p in missing)}",
                duration_seconds=time.time() - start,
                permissions_used=sorted(required, key=lambda p: p.value),
            )
            self.log.record(call, result)
            return result

        # 4. EXECUTE
        try:
            output = spec.fn(**call.arguments)
            status = ToolStatus.SUCCESS
            error = None
        except TimeoutError as e:
            output, status, error = None, ToolStatus.TIMEOUT, str(e)
        except SandboxViolation as e:
            output, status, error = None, ToolStatus.DENIED, str(e)
        except (FileNotFoundError, FileExistsError, IsADirectoryError, NotADirectoryError, ValueError, TypeError) as e:
            output, status, error = None, ToolStatus.INVALID, str(e)
        except Exception as e:
            output, status, error = None, ToolStatus.ERROR, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"

        # 5. RESULT
        result = ToolResult(
            call_id=call.call_id, tool_name=call.tool_name, status=status,
            output=output, error=error, duration_seconds=time.time() - start,
            permissions_used=sorted(required, key=lambda p: p.value),
        )

        # 6. LOG
        self.log.record(call, result)
        return result
