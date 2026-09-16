"""
AGENT LAYER — TOOL-USE SESSION
----------------------------------
The actual loop:

    model request -> model response -> (tool requested?) -> Tool Registry
    -> permission gate -> execution -> tool result -> back to model -> ...
    -> final answer (or turn limit reached)

Security invariant enforced here: the model NEVER touches the filesystem,
shell, or anything else directly. It can only emit a ToolUseRequest, which
this session converts into a tools.ToolCall and hands to the EXISTING
ToolRegistry.execute() - which is the only thing that actually runs
anything, permission-gated as always. This file never bypasses that.
"""

import json
import time
import uuid
from dataclasses import dataclass, field

from providers import AIRequest, Message
from providers.base import AIProvider
from tools import ToolCall, ToolRegistry
from .tool_adapter import build_tool_definitions
from .events import AgentEventLog

DEFAULT_MAX_TOOL_TURNS = 6


@dataclass
class SessionResult:
    task_id: str
    status: str                    # "COMPLETED" | "INCOMPLETE" | "ERROR"
    final_response: str = None
    turns_used: int = 0
    tool_calls_made: list = field(default_factory=list)   # list of {tool_name, arguments, success}
    events: list = field(default_factory=list)
    error: str = None


class ToolUseSession:
    def __init__(
        self,
        provider: AIProvider,
        tool_registry: ToolRegistry,
        allowed_tools: set,
        max_tool_turns: int = DEFAULT_MAX_TOOL_TURNS,
        system_prompt: str = None,
        worker_id: str = "agent",
    ):
        self.provider = provider
        self.tool_registry = tool_registry
        self.allowed_tools = set(allowed_tools)
        self.max_tool_turns = max_tool_turns
        self.system_prompt = system_prompt or (
            "You are a coding assistant with access to a small set of tools. "
            "Use them to accomplish the user's objective. When you are done, "
            "reply with a final plain-text answer and no further tool calls."
        )
        self.worker_id = worker_id

    def run(self, objective: str, task_id: str = None) -> SessionResult:
        task_id = task_id or uuid.uuid4().hex[:12]
        log = AgentEventLog()
        log.emit("TASK_STARTED", task_id, worker_id=self.worker_id, detail=objective[:200])

        tool_defs = build_tool_definitions(self.tool_registry, self.allowed_tools)
        messages = [Message(role="user", content=objective)]
        tool_calls_made = []
        turn = 0

        try:
            while True:
                log.emit("MODEL_REQUEST", task_id, worker_id=self.worker_id)
                response = self.provider.generate(AIRequest(
                    messages=messages,
                    system_prompt=self.system_prompt,
                    tools=tool_defs if tool_defs else None,
                    request_id=task_id,
                ))
                log.emit("MODEL_RESPONSE", task_id, worker_id=self.worker_id,
                         detail=f"finish_reason={response.finish_reason.value}")

                if not response.tool_calls:
                    log.emit("FINAL_RESPONSE", task_id, worker_id=self.worker_id)
                    return SessionResult(
                        task_id=task_id, status="COMPLETED", final_response=response.content,
                        turns_used=turn, tool_calls_made=tool_calls_made, events=log.as_dicts(),
                    )

                if turn >= self.max_tool_turns:
                    log.emit("TURN_LIMIT_REACHED", task_id, worker_id=self.worker_id,
                             detail=f"max_tool_turns={self.max_tool_turns}")
                    return SessionResult(
                        task_id=task_id, status="INCOMPLETE",
                        final_response="Stopped: maximum tool-call turns reached before a final answer.",
                        turns_used=turn, tool_calls_made=tool_calls_made, events=log.as_dicts(),
                    )

                # Echo the model's own turn (with its tool_use blocks) back
                # into the conversation - required so the next request has
                # the correct history for the tool_result to attach to.
                assistant_blocks = response.raw_metadata.get("content_blocks")
                messages.append(Message(role="assistant", content=assistant_blocks if assistant_blocks else response.content))

                tool_result_blocks = []
                for tc in response.tool_calls:
                    log.emit("TOOL_REQUESTED", task_id, worker_id=self.worker_id,
                             tool_call_id=tc.id, tool_name=tc.name)

                    if tc.name not in self.allowed_tools:
                        # Model asked for a tool it was never offered / not
                        # permitted for this task - denied without ever
                        # reaching the registry's own permission gate.
                        result_payload = {"success": False, "error": f"Tool '{tc.name}' is not allowed for this task.", "tool": tc.name}
                        log.emit("PERMISSION_CHECK", task_id, worker_id=self.worker_id,
                                 tool_call_id=tc.id, tool_name=tc.name, success=False, detail="not in allowed_tools")
                        log.emit("TOOL_RESULT", task_id, worker_id=self.worker_id,
                                 tool_call_id=tc.id, tool_name=tc.name, success=False, detail="not in allowed_tools")
                        tool_calls_made.append({"tool_name": tc.name, "arguments": tc.arguments, "success": False})
                    else:
                        call = ToolCall(tool_name=tc.name, arguments=tc.arguments, call_id=tc.id, requested_by=self.worker_id)
                        log.emit("PERMISSION_CHECK", task_id, worker_id=self.worker_id,
                                 tool_call_id=tc.id, tool_name=tc.name)
                        result = self.tool_registry.execute(call)
                        log.emit("TOOL_EXECUTED", task_id, worker_id=self.worker_id,
                                 tool_call_id=tc.id, tool_name=tc.name, success=result.ok,
                                 detail=result.error if not result.ok else None)
                        result_payload = {
                            "success": result.ok,
                            "result": result.output if result.ok else None,
                            "error": result.error,
                            "tool": tc.name,
                        }
                        log.emit("TOOL_RESULT", task_id, worker_id=self.worker_id,
                                 tool_call_id=tc.id, tool_name=tc.name, success=result.ok)
                        tool_calls_made.append({"tool_name": tc.name, "arguments": tc.arguments, "success": result.ok})

                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": json.dumps(result_payload, default=str),
                        "is_error": not result_payload["success"],
                    })

                messages.append(Message(role="user", content=tool_result_blocks))
                turn += 1

        except Exception as e:
            log.emit("ERROR", task_id, worker_id=self.worker_id, detail=str(e))
            return SessionResult(
                task_id=task_id, status="ERROR", error=str(e),
                turns_used=turn, tool_calls_made=tool_calls_made, events=log.as_dicts(),
            )
