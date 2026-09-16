"""
TOOL SYSTEM — SCHEMAS
----------------------
Structured request/response types for every tool call. No tool in this
system communicates via free-form text; everything is a ToolCall in,
ToolResult out.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Permission(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    NETWORK = "NETWORK"
    DESTRUCTIVE = "DESTRUCTIVE"


class ToolStatus(str, Enum):
    SUCCESS = "SUCCESS"
    DENIED = "DENIED"           # permission check failed
    INVALID = "INVALID"         # validation failed (bad args)
    ERROR = "ERROR"             # execution raised/failed
    TIMEOUT = "TIMEOUT"


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    requested_at: float = field(default_factory=time.time)
    requested_by: str = "unknown"   # which worker/component issued this call

    def to_dict(self):
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
        }


@dataclass
class ToolResult:
    call_id: str
    tool_name: str
    status: ToolStatus
    output: Any = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    permissions_used: list = field(default_factory=list)

    def to_dict(self):
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "permissions_used": [p.value for p in self.permissions_used],
        }

    @property
    def ok(self) -> bool:
        return self.status == ToolStatus.SUCCESS
