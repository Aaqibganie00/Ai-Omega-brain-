"""
AI PROVIDER LAYER — CLAUDE PROVIDER
--------------------------------------
Real implementation. Talks to the actual Anthropic /v1/messages endpoint.

IMPLEMENTATION NOTE: the official `anthropic` Python SDK could not be
installed in this sandbox (no PyPI access on this network egress - see
providers/README.md for the exact error). Rather than fake it, this
provider calls the documented REST API directly via `requests`, which
produces the same real HTTP request/response the SDK would make. If the
official SDK is available in the deployment environment, swapping the
`_call_api` method to use `anthropic.Anthropic(...)` is a drop-in change -
nothing else in this file or its callers would need to change, since the
rest of the class only depends on AIRequest/AIResponse.
"""

import time
import requests

from .base import AIProvider
from .schemas import AIRequest, AIResponse, Usage, FinishReason, Message, ToolUseRequest
from .config import ClaudeConfig
from .errors import (
    MissingAPIKeyError, AuthenticationError, RateLimitError,
    ProviderTimeoutError, NetworkError, MalformedResponseError, ProviderUnavailableError,
)


class ClaudeProvider(AIProvider):
    name = "claude"

    def __init__(self, config: ClaudeConfig = None):
        self.config = config or ClaudeConfig.from_env()

    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def generate(self, request: AIRequest) -> AIResponse:
        api_key = self.config.api_key
        if not api_key:
            raise MissingAPIKeyError(self.config.api_key_env_var)

        payload = self._build_payload(request)
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self.config.api_version,
            "content-type": "application/json",
        }

        start = time.time()
        try:
            resp = requests.post(
                f"{self.config.base_url}/v1/messages",
                json=payload,
                headers=headers,
                timeout=request.timeout_seconds or self.config.timeout_seconds,
            )
        except requests.exceptions.Timeout:
            raise ProviderTimeoutError(self.name, request.timeout_seconds or self.config.timeout_seconds)
        except requests.exceptions.ConnectionError as e:
            # deliberately do not include the raw exception text - it can
            # sometimes echo request internals; keep the message generic.
            raise NetworkError(self.name, "connection failed")
        except requests.exceptions.RequestException as e:
            raise NetworkError(self.name, type(e).__name__)

        latency = time.time() - start
        return self._parse_response(resp, request, latency)

    # ---- internals ----

    def _build_payload(self, request: AIRequest) -> dict:
        payload = {
            "model": request.model or self.config.resolved_model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt
        temp = request.temperature if request.temperature is not None else self.config.temperature
        if temp is not None:
            payload["temperature"] = temp
        if request.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]
        return payload

    def _parse_response(self, resp: "requests.Response", request: AIRequest, latency: float) -> AIResponse:
        status = resp.status_code

        if status == 401:
            raise AuthenticationError(self.name)
        if status == 429:
            retry_after = resp.headers.get("retry-after")
            raise RateLimitError(self.name, retry_after=float(retry_after) if retry_after else None)
        if status in (500, 502, 503, 504):
            raise ProviderUnavailableError(self.name, f"HTTP {status}")
        if status != 200:
            # never include response body verbatim in the error - it could
            # theoretically echo back sensitive request content
            raise MalformedResponseError(self.name, f"HTTP {status}")

        try:
            data = resp.json()
        except ValueError:
            raise MalformedResponseError(self.name, "response body was not valid JSON")

        try:
            content_blocks = data["content"]
            text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
            tool_calls = [
                ToolUseRequest(id=b["id"], name=b["name"], arguments=b.get("input", {}))
                for b in content_blocks if b.get("type") == "tool_use"
            ]
            stop_reason = data.get("stop_reason", "")
            usage_data = data.get("usage", {})
        except (KeyError, TypeError) as e:
            raise MalformedResponseError(self.name, f"unexpected response shape: missing {e}")

        finish_map = {
            "end_turn": FinishReason.STOP,
            "max_tokens": FinishReason.MAX_TOKENS,
            "tool_use": FinishReason.TOOL_USE,
        }

        return AIResponse(
            content=text,
            model=data.get("model", request.model or self.config.resolved_model),
            provider=self.name,
            finish_reason=finish_map.get(stop_reason, FinishReason.UNKNOWN),
            request_id=request.request_id,
            usage=Usage(
                input_tokens=usage_data.get("input_tokens"),
                output_tokens=usage_data.get("output_tokens"),
            ),
            raw_metadata={"stop_reason": stop_reason, "response_id": data.get("id"), "content_blocks": content_blocks},
            latency_seconds=latency,
            tool_calls=tool_calls,
        )
