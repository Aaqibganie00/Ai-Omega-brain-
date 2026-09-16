"""
AI PROVIDER LAYER — SCHEMAS
-----------------------------
Provider-agnostic request/response types. Omega Core and the router only
ever see these - never a provider-specific payload shape.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FinishReason(str, Enum):
    STOP = "stop"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class Message:
    role: str    # "user" | "assistant"
    # str for plain text turns; list[dict] for turns carrying tool_use /
    # tool_result content blocks (Anthropic content-block format, passed
    # through as-is - kept provider-shaped only at this boundary).
    content: Any


@dataclass
class ToolDefinition:
    """Provider-independent tool definition offered to a model. Built by
    agent/tool_adapter.py from the existing ToolRegistry - never hand-built
    per provider."""
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolUseRequest:
    """A single tool invocation the MODEL asked for, parsed out of its
    response. id is the provider's own identifier for this call (e.g.
    Claude's tool_use block id) - preserved so the tool_result can be
    correlated back to it."""
    id: str
    name: str
    arguments: dict


@dataclass
class AIRequest:
    messages: list[Message]
    system_prompt: Optional[str] = None
    model: Optional[str] = None                    # None = provider default
    temperature: Optional[float] = None
    max_tokens: int = 1024
    timeout_seconds: float = 30.0
    structured_output_schema: Optional[dict] = None  # JSON schema, if the caller wants validated structured output
    tools: Optional[list[ToolDefinition]] = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def simple(cls, prompt: str, **kwargs) -> "AIRequest":
        """Convenience constructor for a single-turn user prompt."""
        return cls(messages=[Message(role="user", content=prompt)], **kwargs)


@dataclass
class Usage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass
class AIResponse:
    content: str
    model: str
    provider: str
    finish_reason: FinishReason
    request_id: str
    usage: Usage = field(default_factory=Usage)
    raw_metadata: dict = field(default_factory=dict)
    latency_seconds: float = 0.0
    generated_at: float = field(default_factory=time.time)
    tool_calls: list[ToolUseRequest] = field(default_factory=list)

    def to_dict(self):
        return {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "finish_reason": self.finish_reason.value,
            "request_id": self.request_id,
            "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens},
            "latency_seconds": self.latency_seconds,
            "tool_calls": [{"id": t.id, "name": t.name, "arguments": t.arguments} for t in self.tool_calls],
        }
