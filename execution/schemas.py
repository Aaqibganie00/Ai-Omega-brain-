"""
EXECUTION LAYER (PHASE 10) — SCHEMAS
----------------------------------------
Structured types for the execution/session coordination layer. One
ExecutionSessionResult carries the complete, session-scoped record of a
single end-to-end run: request, controls, planning, learning/context,
execution, verification, quality, errors/recovery, and checkpoint state.
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class SessionState(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"      # plan rejected by PlanValidator before any execution
    ERROR = "ERROR"            # infrastructure-level exception


@dataclass
class SessionControls:
    """Immutable snapshot of the execution controls bound to one session.

    This is NOT a second policy engine - it is the recorded evidence that
    the session ran under the SAME permission policy the ToolRegistry
    enforces, the SAME ResourceLimits the Orchestrator enforces, and the
    tool set actually registered. Deterministic binding, recorded once."
    """
    permission_policy: dict = field(default_factory=dict)    # {"granted": [sorted Permission values]}
    limits: dict = field(default_factory=dict)               # ResourceLimits fields
    tool_restrictions: dict = field(default_factory=dict)    # {"registered_tools": [...]}

    def to_dict(self):
        return {
            "permission_policy": self.permission_policy,
            "limits": self.limits,
            "tool_restrictions": self.tool_restrictions,
        }


@dataclass
class ExecutionSessionResult:
    session_id: str
    task_id: str
    request: str
    state: str                              # SessionState value
    controls: SessionControls = None
    plan_summary: dict = field(default_factory=dict)
    learning: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    execution_status: str = None
    verification: dict = field(default_factory=dict)
    quality_decision: str = None
    integrity: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    recovery: dict = field(default_factory=dict)
    checkpoint_id: str = None
    events: list = field(default_factory=list)
    duration_seconds: float = 0.0
    raw_result: object = None               # deep handle for advanced callers (never serialized)

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "request": self.request,
            "state": self.state,
            "controls": self.controls.to_dict() if self.controls else None,
            "plan_summary": self.plan_summary,
            "learning": self.learning,
            "context": self.context,
            "execution_status": self.execution_status,
            "verification": self.verification,
            "quality_decision": self.quality_decision,
            "integrity": self.integrity,
            "errors": self.errors,
            "recovery": self.recovery,
            "checkpoint_id": self.checkpoint_id,
            "duration_seconds": self.duration_seconds,
        }
