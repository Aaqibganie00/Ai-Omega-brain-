"""AI PROVIDER LAYER — GEMINI PROVIDER
--------------------------------------
Real implementation of the existing AIProvider interface for the Google
Generative Language REST API. Uses plain `requests` - the same dependency
and the same mockable seam (requests.post) as ClaudeProvider - so automated
tests run fully offline.

Authentication goes in the `x-goog-api-key` request header. The API key is
NEVER placed in the URL query string, NEVER logged, and NEVER included in
error messages, events, or normalized response metadata.

History translation contract
----------------------------
agent/session.py (ToolUseSession) keeps conversation history in the
canonical block shapes shared by the existing providers:

    {"type": "text",  "text": ...}
    {"type": "tool_use",    "id": ..., "name": ..., "input": {...}}
    {"type": "tool_result", "tool_use_id": ..., "content": "<json>", "is_error": bool}

This provider translates those blocks to Gemini `contents` parts
(text / functionCall / functionResponse) on the way in, and translates
Gemini responses back into the canonical blocks (exposed via
AIResponse.raw_metadata["content_blocks"]) on the way out - so
ToolUseSession, the ToolRegistry permission pipeline, MockAIProvider-based
tests, and every downstream component work unchanged. provider.format
details never leak out of this file.
"""

import json
import time

import requests

from .base import AIProvider
from .schemas import AIRequest, AIResponse, Usage, FinishReason, ToolUseRequest
from .gemini_config import GeminiConfig
from .errors import (
    MissingAPIKeyError, AuthenticationError, RateLimitError,
    ProviderTimeoutError, NetworkError, MalformedResponseError, ProviderUnavailableError,
)


class GeminiProvider(AIProvider):
    name = "gemini"

    # ---- bounded transient-retry policy ----
    # Only transient failures are retried: request timeouts, connection errors,
    # HTTP 429 and HTTP 5xx. Deterministic failures - missing key, auth errors
    # (401/403), malformed responses, prompt/safety blocks - are raised on the
    # first attempt, because retrying them can only burn quota. Backoff is
    # exponential and always capped, so one bad minute at the API can never
    # stall the caller indefinitely.
    MAX_ATTEMPTS = 4                    # 1 initial attempt + 3 retries
    INITIAL_BACKOFF_SECONDS = 0.5
    BACKOFF_MULTIPLIER = 2.0
    MAX_BACKOFF_SECONDS = 8.0
    RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)

    # Gemini finishReason -> existing FinishReason enum. The enum has no
    # content-filter value, so safety stops map to UNKNOWN and are flagged
    # via raw_metadata["safety_blocked"].
    _FINISH_MAP = {
        "STOP": FinishReason.STOP,
        "MAX_TOKENS": FinishReason.MAX_TOKENS,
    }
    _SAFETY_FINISH_REASONS = {
        "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
    }

    # Key used inside the canonical (provider-neutral) content blocks to carry
    # a Gemini thoughtSignature across the ToolUseSession history echo. Kept
    # snake_case on the canonical side; emitted as the API's camelCase
    # "thoughtSignature" on requests. Never logged.
    _SIGNATURE_BLOCK_KEY = "thought_signature"

    def __init__(self, config: GeminiConfig = None):
        self.config = config or GeminiConfig.from_env()

    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def generate(self, request: AIRequest) -> AIResponse:
        api_key = self.config.api_key
        if not api_key:
            raise MissingAPIKeyError(self.config.api_key_env_var)

        payload = self._build_payload(request)
        headers = {
            "x-goog-api-key": api_key,
            "content-type": "application/json",
        }
        timeout = request.timeout_seconds or self.config.timeout_seconds

        last_error = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            if attempt > 1:
                time.sleep(self._retry_delay(attempt, last_error))

            attempt_start = time.time()
            try:
                resp = requests.post(
                    self._endpoint(),
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
            except requests.exceptions.Timeout:
                last_error = ProviderTimeoutError(self.name, timeout)
                continue
            except requests.exceptions.ConnectionError:
                # deliberately generic - raw connection errors can echo request
                # internals that must never leak
                last_error = NetworkError(self.name, "connection failed")
                continue
            except requests.exceptions.RequestException as e:
                last_error = NetworkError(self.name, type(e).__name__)
                continue

            latency = time.time() - attempt_start
            try:
                return self._parse_response(resp, request, latency)
            except (RateLimitError, ProviderUnavailableError) as e:
                # _parse_response also raises ProviderUnavailableError for
                # safety-blocked prompts at HTTP 200; the status check keeps
                # that deterministic case out of the retry loop.
                if resp.status_code not in self.RETRYABLE_STATUS_CODES:
                    raise
                last_error = e

        raise last_error

    def _retry_delay(self, attempt: int, last_error) -> float:
        """Seconds to wait before `attempt` (1-based) after `last_error`.

        An explicit Retry-After from the API wins over the local backoff, but
        is still capped so a hostile/large value cannot stall the process.
        """
        if isinstance(last_error, RateLimitError) and last_error.retry_after is not None:
            return min(float(last_error.retry_after), self.MAX_BACKOFF_SECONDS)
        backoff = self.INITIAL_BACKOFF_SECONDS * (
            self.BACKOFF_MULTIPLIER ** (attempt - 2)
        )
        return min(backoff, self.MAX_BACKOFF_SECONDS)

    # ---- endpoint / payload ----

    def _endpoint(self) -> str:
        # Key must never appear in the URL - it travels only in the header.
        return (
            f"{self.config.base_url}/{self.config.api_version}/models/"
            f"{self.config.resolved_model}:generateContent"
        )

    def _build_payload(self, request: AIRequest) -> dict:
        payload = {"contents": self._translate_messages(request.messages)}
        if request.system_prompt:
            # REST field name is camelCase per the API reference.
            payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        if request.tools:
            payload["tools"] = [{"functionDeclarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema or {"type": "object", "properties": {}},
                }
                for t in request.tools
            ]}]
        generation_config = {}
        max_tokens = request.max_tokens or self.config.max_tokens
        if max_tokens:
            generation_config["maxOutputTokens"] = max_tokens
        temperature = request.temperature if request.temperature is not None else self.config.temperature
        if temperature is not None:
            generation_config["temperature"] = temperature
        if generation_config:
            payload["generationConfig"] = generation_config
        return payload

    # ---- history translation (canonical blocks -> Gemini contents) ----

    def _translate_messages(self, messages) -> list:
        # Build tool_use_id -> tool-name index first (from all assistant
        # tool_use blocks) so tool_result blocks can become Gemini
        # functionResponse parts, which require the function name.
        tool_names_by_id = {}
        for msg in messages or []:
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if block.get("id") and block.get("name"):
                            tool_names_by_id[block["id"]] = block["name"]

        contents = []
        for msg in messages or []:
            role = "user" if msg.role == "user" else "model"
            contents.append({"role": role, "parts": self._content_to_parts(msg.content, tool_names_by_id)})
        return contents

    def _content_to_parts(self, content, tool_names_by_id: dict) -> list:
        parts = []
        if isinstance(content, str):
            if content:
                parts.append({"text": content})
            return parts
        if not isinstance(content, list):
            return parts
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                part = {"text": block["text"]}
                if block.get(self._SIGNATURE_BLOCK_KEY):
                    part["thoughtSignature"] = block[self._SIGNATURE_BLOCK_KEY]
                parts.append(part)
            elif btype == "tool_use":
                part = {
                    "functionCall": {
                        "name": block.get("name") or "unknown",
                        "args": block.get("input") or {},
                    }
                }
                # Gemini 3 models require the thoughtSignature emitted with a
                # functionCall to be echoed back in subsequent history, or the
                # request fails validation. Preserved verbatim.
                if block.get(self._SIGNATURE_BLOCK_KEY):
                    part["thoughtSignature"] = block[self._SIGNATURE_BLOCK_KEY]
                parts.append(part)
            elif btype == "tool_result":
                parts.append({"functionResponse": self._tool_result_to_response(block, tool_names_by_id)})
        return parts

    def _tool_result_to_response(self, block: dict, tool_names_by_id: dict) -> dict:
        raw_content = block.get("content")
        payload_obj = None
        if isinstance(raw_content, str):
            try:
                parsed = json.loads(raw_content)
                if isinstance(parsed, dict):
                    payload_obj = parsed
            except (ValueError, TypeError):
                payload_obj = None
        elif isinstance(raw_content, dict):
            payload_obj = raw_content

        # Name resolution: 1) explicit tool name embedded in the payload by
        # ToolUseSession, 2) the tool_use_id -> name index built from the
        # preceding assistant tool_use blocks, 3) last-resort placeholder.
        name = None
        if payload_obj and isinstance(payload_obj.get("tool"), str) and payload_obj.get("tool"):
            name = payload_obj["tool"]
        elif block.get("tool_use_id") in tool_names_by_id:
            name = tool_names_by_id[block["tool_use_id"]]
        if not name:
            name = "unknown_tool"

        if payload_obj is not None:
            response_obj = {
                "success": bool(payload_obj.get("success", not block.get("is_error"))),
                "result": payload_obj.get("result"),
                "error": payload_obj.get("error"),
            }
        else:
            response_obj = {
                "success": not block.get("is_error", False),
                "result": raw_content if raw_content is not None else "",
                "error": None,
            }
        if block.get("is_error") and not response_obj.get("error"):
            response_obj["error"] = "tool execution failed"
            response_obj["success"] = False

        return {"name": name, "response": response_obj}

    # ---- response normalization (Gemini -> existing AIResponse) ----

    def _parse_response(self, resp: "requests.Response", request: AIRequest, latency: float) -> AIResponse:
        status = resp.status_code

        if status in (401, 403):
            raise AuthenticationError(self.name)
        if status == 429:
            retry_after = resp.headers.get("retry-after")
            raise RateLimitError(self.name, retry_after=float(retry_after) if retry_after else None)
        if status in (500, 502, 503, 504):
            raise ProviderUnavailableError(self.name, f"HTTP {status}")
        if status != 200:
            # never include the response body verbatim - it could echo back
            # request content; status code alone is safe
            raise MalformedResponseError(self.name, f"HTTP {status}")

        try:
            data = resp.json()
        except ValueError:
            raise MalformedResponseError(self.name, "response body was not valid JSON")
        if not isinstance(data, dict):
            raise MalformedResponseError(self.name, "unexpected response shape: not an object")

        candidates = data.get("candidates")
        if not candidates:
            block_reason = (data.get("promptFeedback") or {}).get("blockReason")
            if block_reason:
                raise ProviderUnavailableError(self.name, f"prompt blocked (reason: {block_reason})")
            raise MalformedResponseError(self.name, "response contained no candidates")
        if not isinstance(candidates, list) or not isinstance(candidates[0], dict):
            raise MalformedResponseError(self.name, "unexpected response shape: invalid candidates")

        candidate = candidates[0]
        raw_finish = candidate.get("finishReason")
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        if raw_finish is None and not parts:
            raise MalformedResponseError(self.name, "candidate had neither content nor finish reason")

        usage_data = data.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=usage_data.get("promptTokenCount"),
            output_tokens=usage_data.get("candidatesTokenCount"),
        )

        # Canonical content blocks - same shape ClaudeProvider/MockAIProvider
        # emit - so the existing tool-use loop can echo history unchanged.
        content_blocks = []
        text_parts = []
        tool_calls = []
        call_index = 0
        for part in parts:
            if not isinstance(part, dict):
                continue
            thought_signature = part.get("thoughtSignature")
            if isinstance(part.get("text"), str):
                block = {"type": "text", "text": part["text"]}
                if thought_signature:
                    block[self._SIGNATURE_BLOCK_KEY] = thought_signature
                content_blocks.append(block)
                text_parts.append(part["text"])
            elif isinstance(part.get("functionCall"), dict):
                fc = part["functionCall"]
                call_index += 1
                call_id = f"gemini-call-{call_index}"
                tc_name = fc.get("name") or "unknown"
                args = fc.get("args") or {}
                if not isinstance(args, dict):
                    args = {"value": args}
                block = {"type": "tool_use", "id": call_id, "name": tc_name, "input": args}
                if thought_signature:
                    block[self._SIGNATURE_BLOCK_KEY] = thought_signature
                content_blocks.append(block)
                tool_calls.append(ToolUseRequest(id=call_id, name=tc_name, arguments=args))

        finish_reason = self._FINISH_MAP.get(raw_finish, FinishReason.UNKNOWN)
        if tool_calls:
            finish_reason = FinishReason.TOOL_USE

        return AIResponse(
            content="".join(text_parts),
            model=data.get("modelVersion") or f"gemini/{self.config.resolved_model}",
            provider=self.name,
            finish_reason=finish_reason,
            request_id=request.request_id,
            usage=usage,
            raw_metadata={
                "response_id": data.get("responseId"),
                "model_version": data.get("modelVersion"),
                "finish_reason": raw_finish,
                "safety_blocked": raw_finish in self._SAFETY_FINISH_REASONS,
                "content_blocks": content_blocks,
            },
            latency_seconds=latency,
            tool_calls=tool_calls,
        )
