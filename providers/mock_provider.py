"""
AI PROVIDER LAYER — MOCK PROVIDER
------------------------------------
Deterministic local provider for testing the ARCHITECTURE (routing,
schemas, error handling, Omega Core integration, tool-use loops) without
needing an API key or network. This is NEVER used on the production path -
ClaudeProvider is. `is_configured()` always returns True since it needs
nothing external, which is exactly why it must never be silently
substituted for a real provider - callers select it explicitly.
"""

import time
from .base import AIProvider
from .schemas import AIRequest, AIResponse, Usage, FinishReason, ToolUseRequest
from .errors import ProviderTimeoutError


class MockAIProvider(AIProvider):
    name = "mock"

    def __init__(self, canned_response: str = None, simulate_timeout: bool = False, script: list[dict] = None):
        """
        canned_response / simulate_timeout: original single-turn behavior,
        unchanged.

        script: optional list of turn specs for deterministic multi-turn
        tool-use testing. Each entry:
            {"text": "...", "tool_calls": [{"id": "...", "name": "...", "arguments": {...}}]}
        Consumed one per generate() call, in order; the last entry repeats
        if generate() is called more times than the script has entries.
        """
        self.canned_response = canned_response
        self.simulate_timeout = simulate_timeout
        self.script = script
        self._script_index = 0
        self.calls_received: list[AIRequest] = []

    def is_configured(self) -> bool:
        return True

    def generate(self, request: AIRequest) -> AIResponse:
        self.calls_received.append(request)

        if self.simulate_timeout:
            raise ProviderTimeoutError(self.name, request.timeout_seconds)

        start = time.time()

        if self.script:
            turn = self.script[min(self._script_index, len(self.script) - 1)]
            self._script_index += 1
            text = turn.get("text", "")
            raw_tool_calls = turn.get("tool_calls", [])
            tool_calls = [ToolUseRequest(id=tc["id"], name=tc["name"], arguments=tc.get("arguments", {})) for tc in raw_tool_calls]
            finish_reason = FinishReason.TOOL_USE if tool_calls else FinishReason.STOP

            content_blocks = []
            if text:
                content_blocks.append({"type": "text", "text": text})
            for tc in raw_tool_calls:
                content_blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("arguments", {})})

            return AIResponse(
                content=text,
                model=request.model or "mock-model-v1",
                provider=self.name,
                finish_reason=finish_reason,
                request_id=request.request_id,
                usage=Usage(input_tokens=1, output_tokens=1),
                raw_metadata={"mock": True, "content_blocks": content_blocks},
                latency_seconds=time.time() - start,
                tool_calls=tool_calls,
            )

        last_user_msg = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        if not isinstance(last_user_msg, str):
            last_user_msg = str(last_user_msg)
        content = self.canned_response or f"[MOCK RESPONSE to: {last_user_msg[:80]}]"

        return AIResponse(
            content=content,
            model=request.model or "mock-model-v1",
            provider=self.name,
            finish_reason=FinishReason.STOP,
            request_id=request.request_id,
            usage=Usage(input_tokens=len(last_user_msg.split()), output_tokens=len(content.split())),
            raw_metadata={"mock": True},
            latency_seconds=time.time() - start,
        )
