"""
SELF-CORRECTION LAYER — DEBUGGER / FIXER WORKER
-----------------------------------------------------
Reuses agent.session.ToolUseSession (Phase 3, unmodified) rather than
building a new tool-use loop. The only new behavior here is: (1) the
prompt is the bounded repair context, not the raw objective, and (2) the
tool registry it's given is wrapped in ScopedToolRegistry so it can only
write/edit the files the failure analysis identified as relevant.

Never touches the filesystem directly - every change still goes through
the existing ToolRegistry.execute() pipeline (validate -> permission ->
execute -> log), same as every other tool call in this project.
"""

from agent.session import ToolUseSession, DEFAULT_MAX_TOOL_TURNS
from .scoped_registry import ScopedToolRegistry
from .context import build_repair_prompt
from .schemas import FailureAnalysis

DEFAULT_FIXER_TOOLS = {"read_file", "edit_file", "write_file", "list_files", "search_files"}


class DebuggerFixerWorker:
    name = "debugger_fixer_worker"

    def __init__(self, provider, tool_registry, allowed_tools=None, max_tool_turns=DEFAULT_MAX_TOOL_TURNS):
        self.provider = provider
        self.tool_registry = tool_registry
        self.allowed_tools = allowed_tools or DEFAULT_FIXER_TOOLS
        self.max_tool_turns = max_tool_turns

    def propose_fix(self, objective, analysis, task_id=None, previous_attempt_summary=None):
        scoped_registry = ScopedToolRegistry(
            self.tool_registry,
            allowed_paths=set(analysis.affected_files) if analysis.affected_files else None,
        )

        session = ToolUseSession(
            provider=self.provider,
            tool_registry=scoped_registry,
            allowed_tools=self.allowed_tools,
            max_tool_turns=self.max_tool_turns,
            worker_id=self.name,
            system_prompt=(
                "You are a debugger. You are given a failing test's evidence and root-cause "
                "assessment. Inspect only the relevant file(s) and apply the minimal fix needed "
                "to make the test pass. Do not modify files unrelated to the failure. When done, "
                "reply with a short plain-text summary and no further tool calls."
            ),
        )

        prompt = build_repair_prompt(objective, analysis, previous_attempt_summary)
        return session.run(prompt, task_id=task_id)
