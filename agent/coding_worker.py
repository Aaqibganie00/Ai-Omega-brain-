"""
AGENT LAYER — CODING WORKER (tool-using)
---------------------------------------------
The one worker upgraded per this phase to use the real Claude + Tools
loop, as instructed - workers.py's mock CodingWorker is left untouched,
this is an additive alternative selected explicitly by whoever's wiring
up an agentic task.
"""

from .session import ToolUseSession, SessionResult, DEFAULT_MAX_TOOL_TURNS

# P0-3: run_command is available to the coding flow. This changes ONLY the
# tool allowlist - every invocation still passes through the unchanged
# ToolRegistry permission pipeline (EXECUTE permission + CommandClassifier +
# sandbox + timeout + audit). The default registry policy grants
# {READ, WRITE, EXECUTE}: plain shell commands (python3 main.py, listing,
# linting) work out of the box; NETWORK-classified and DESTRUCTIVE-classified
# commands remain denied unless explicitly granted, exactly as before.
DEFAULT_CODING_TOOLS = {"read_file", "write_file", "edit_file", "list_files", "search_files", "run_tests", "inspect_project", "run_command"}


class CodingAgentWorker:
    name = "coding_agent_worker"

    def __init__(self, provider, tool_registry, allowed_tools: set = None, max_tool_turns: int = DEFAULT_MAX_TOOL_TURNS):
        self.session = ToolUseSession(
            provider=provider,
            tool_registry=tool_registry,
            allowed_tools=allowed_tools or DEFAULT_CODING_TOOLS,
            max_tool_turns=max_tool_turns,
            worker_id=self.name,
            system_prompt=(
                "You are a coding assistant. You can read, write, and edit files, "
                "list/search the project, and run its tests - all through the tools "
                "provided. Use them to accomplish the objective, then reply with a "
                "final plain-text summary and no further tool calls."
            ),
        )

    def execute_task(self, objective: str, task_id: str = None) -> SessionResult:
        return self.session.run(objective, task_id=task_id)
