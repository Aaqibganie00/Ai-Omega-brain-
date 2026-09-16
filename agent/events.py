"""
AGENT LAYER — EVENT LOG
---------------------------
Structured execution events for a tool-use session. Separate from
tools.registry.ExecutionLog (which only logs individual tool calls) - this
also captures the model round-trips (MODEL_REQUEST/MODEL_RESPONSE) around
them, so the full session trace can be reconstructed.
"""

import time
from dataclasses import dataclass, field


@dataclass
class AgentEvent:
    event: str            # e.g. TASK_STARTED, MODEL_REQUEST, TOOL_REQUESTED, ...
    task_id: str
    timestamp: float = field(default_factory=time.time)
    tool_call_id: str = None
    tool_name: str = None
    worker_id: str = None
    success: bool = None
    detail: str = None

    def to_dict(self):
        return {
            "event": self.event, "task_id": self.task_id, "timestamp": self.timestamp,
            "tool_call_id": self.tool_call_id, "tool_name": self.tool_name,
            "worker_id": self.worker_id, "success": self.success, "detail": self.detail,
        }


class AgentEventLog:
    def __init__(self):
        self.events: list[AgentEvent] = []

    def emit(self, event: str, task_id: str, **kwargs) -> AgentEvent:
        e = AgentEvent(event=event, task_id=task_id, **kwargs)
        self.events.append(e)
        return e

    def as_dicts(self):
        return [e.to_dict() for e in self.events]
