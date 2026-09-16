"""
SELF-CORRECTION LAYER — SCOPED TOOL REGISTRY
-------------------------------------------------
Wraps the EXISTING ToolRegistry (unmodified) to additionally restrict
write_file/edit_file to an explicit set of allowed paths. This is how
"fix scope" (item 6) is enforced: the fixer worker uses this wrapper
instead of the raw registry, so an attempt to touch a file outside the
failure's affected_files is denied before the underlying registry ever
executes it - not just discouraged in a prompt.

Duck-types the same interface ToolUseSession/tool_adapter expect
(execute(), available_tools(), _specs, log, impl, permissions) so nothing
in the agent layer needs to know it's wrapped.
"""

from tools import ToolResult, ToolStatus


class ScopedToolRegistry:
    def __init__(self, inner_registry, allowed_paths: set = None):
        """allowed_paths=None means no additional restriction beyond the
        inner registry's own permission system (used when a failure's
        affected_files couldn't be determined)."""
        self._inner = inner_registry
        self.allowed_paths = allowed_paths

    @property
    def _specs(self):
        return self._inner._specs

    @property
    def log(self):
        return self._inner.log

    @property
    def impl(self):
        return self._inner.impl

    @property
    def permissions(self):
        return self._inner.permissions

    def available_tools(self):
        return self._inner.available_tools()

    def execute(self, call):
        if self.allowed_paths is not None and call.tool_name in ("write_file", "edit_file"):
            path = call.arguments.get("path")
            if path not in self.allowed_paths:
                result = ToolResult(
                    call_id=call.call_id, tool_name=call.tool_name, status=ToolStatus.DENIED,
                    error=f"Fix scope violation: '{path}' is not an authorized file for this repair "
                          f"(allowed: {sorted(self.allowed_paths)}).",
                )
                self._inner.log.record(call, result)
                return result
        return self._inner.execute(call)
