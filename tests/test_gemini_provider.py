"""Offline tests for the Gemini provider milestone.

Every HTTP interaction is mocked at the `providers.gemini_provider.requests.post`
seam (the same seam ClaudeProvider tests use). No network access, no real API
key, no real model responses are ever required or stored.

Sections:
    A. Config         G. Usage / finish-reason normalization
    B. Text           H. Error mapping (401/403/429/timeout/network/malformed/5xx)
    C. Tool defs      I. Secret safety
    D. Tool calls     J. is_configured()
    E. Tool results   K. Single injection test (ExecutionSession + GeminiProvider
    F. Multi-turn history      over mocked HTTP through the real permission pipeline)
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from providers import (
    AIRequest, Message, ToolDefinition, FinishReason,
    GeminiProvider, GeminiConfig,
    MissingAPIKeyError, AuthenticationError, RateLimitError,
    ProviderTimeoutError, NetworkError, MalformedResponseError, ProviderUnavailableError,
)

FAKE_KEY = "gkey-FAKE-test-only-not-a-real-key-000000000000"


def fake_response(status=200, body=None, headers=None):
    """Minimal stand-in for requests.Response."""
    resp = type("_Resp", (), {})()
    resp.ok = 200 <= status < 300
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = "" if body is None else json.dumps(body)

    def _json():
        if body is None:
            raise ValueError("invalid json")
        return body

    resp.json = _json
    return resp


def text_body(text, finish="STOP", input_tokens=5, output_tokens=3):
    return {
        "candidates": [{
            "finishReason": finish,
            "content": {"role": "model", "parts": [{"text": text}]},
        }],
        "usageMetadata": {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
            "totalTokenCount": input_tokens + output_tokens,
        },
        "responseId": "resp-fake-1",
        "modelVersion": "gemini-1.5-flash-001",
    }


def tool_call_body(calls, finish="STOP"):
    return {
        "candidates": [{
            "finishReason": finish,
            "content": {"role": "model", "parts": [
                {"functionCall": {"name": name, "args": args}} for name, args in calls
            ]},
        }],
        "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4, "totalTokenCount": 13},
        "responseId": "resp-fake-2",
    }


def make_provider(**config_overrides):
    cfg = GeminiConfig(**config_overrides)
    os.environ[cfg.api_key_env_var] = FAKE_KEY
    return GeminiProvider(cfg)


class TestGeminiConfig(unittest.TestCase):
    """A. Configuration loading, validation, redaction."""

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_from_env_loads_all_values(self):
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": FAKE_KEY,
            "GEMINI_MODEL": "gemini-pro-x",
            "GEMINI_TIMEOUT_SECONDS": "12.5",
            "GEMINI_MAX_TOKENS": "777",
            "GEMINI_TEMPERATURE": "0.7",
        }, clear=True):
            cfg = GeminiConfig.from_env()
            # api_key is a live env-backed property: snapshot inside the patch
            key = cfg.api_key
        self.assertEqual(key, FAKE_KEY)
        self.assertEqual(cfg.resolved_model, "gemini-pro-x")
        self.assertEqual(cfg.timeout_seconds, 12.5)
        self.assertEqual(cfg.max_tokens, 777)
        self.assertEqual(cfg.temperature, 0.7)

    def test_from_env_defaults_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = GeminiConfig.from_env()
            key = cfg.api_key
        self.assertIsNone(key)
        self.assertEqual(cfg.resolved_model, "gemini-3.8-flash")
        self.assertEqual(cfg.timeout_seconds, 30.0)
        self.assertEqual(cfg.max_tokens, 2048)
        self.assertIsNone(cfg.temperature)

    def test_invalid_timeout_raises(self):
        with patch.dict(os.environ, {"GEMINI_TIMEOUT_SECONDS": "abc"}, clear=True):
            with self.assertRaises(ValueError):
                GeminiConfig.from_env()

    def test_invalid_max_tokens_raises(self):
        with patch.dict(os.environ, {"GEMINI_MAX_TOKENS": "1.5"}, clear=True):
            with self.assertRaises(ValueError):
                GeminiConfig.from_env()

    def test_invalid_temperature_raises(self):
        with patch.dict(os.environ, {"GEMINI_TEMPERATURE": "hot"}, clear=True):
            with self.assertRaises(ValueError):
                GeminiConfig.from_env()

    def test_negative_timeout_raises(self):
        with patch.dict(os.environ, {"GEMINI_TIMEOUT_SECONDS": "-3"}, clear=True):
            with self.assertRaises(ValueError):
                GeminiConfig.from_env()

    def test_negative_temperature_raises(self):
        with patch.dict(os.environ, {"GEMINI_TEMPERATURE": "-0.5"}, clear=True):
            with self.assertRaises(ValueError):
                GeminiConfig.from_env()

    def test_missing_key_missing_key_error_names_env_var_not_value(self):
        os.environ.pop("GEMINI_API_KEY", None)
        provider = GeminiProvider(GeminiConfig())
        with self.assertRaises(MissingAPIKeyError) as ctx:
            provider.generate(AIRequest.simple("hello"))
        msg = str(ctx.exception)
        self.assertIn("GEMINI_API_KEY", msg)
        self.assertNotIn(FAKE_KEY, msg)

    def test_redacted_never_includes_key(self):
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        cfg = GeminiConfig.from_env()
        red = cfg.redacted()
        self.assertTrue(red["api_key_configured"])
        blob = json.dumps(red)
        self.assertNotIn(FAKE_KEY, blob)
        self.assertNotIn("api_key=", blob.lower())


class TestGeminiTextGeneration(unittest.TestCase):
    """B. Text generation: endpoint, headers, payload, normalization."""

    def setUp(self):
        self.provider = make_provider()

    def test_correct_endpoint_headers_and_payload(self):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("hi there"))
            req = AIRequest(
                messages=[Message(role="user", content="Say hi")],
                system_prompt="Be terse.",
                max_tokens=123,
                temperature=0.4,
            )
            self.provider.generate(req)

            args, kwargs = mock_post.call_args
            url = args[0]
            self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent")
            self.assertNotIn("key", url.lower())
            self.assertNotIn(FAKE_KEY, url)
            self.assertEqual(kwargs["headers"]["x-goog-api-key"], FAKE_KEY)
            self.assertEqual(kwargs["headers"]["content-type"], "application/json")
            payload = kwargs["json"]
            self.assertEqual(payload["contents"], [{"role": "user", "parts": [{"text": "Say hi"}]}])
            # REST field name is camelCase per the current API reference.
            self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "Be terse."}]})
            self.assertNotIn("system_instruction", payload)
            self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 123)
            self.assertEqual(payload["generationConfig"]["temperature"], 0.4)
            self.assertNotIn("tools", payload)

    def test_request_timeout_used(self):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("ok"))
            self.provider.generate(AIRequest.simple("x", timeout_seconds=7.5))
            self.assertEqual(mock_post.call_args.kwargs["timeout"], 7.5)

    def test_config_default_timeout_when_request_omits(self):
        provider = make_provider(timeout_seconds=44.0)
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("ok"))
            provider.generate(AIRequest(messages=[Message(role="user", content="x")], timeout_seconds=None))
            self.assertEqual(mock_post.call_args.kwargs["timeout"], 44.0)

    def test_text_response_normalization(self):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("It works.", input_tokens=11, output_tokens=6))
            resp = self.provider.generate(AIRequest.simple("ping"))
        self.assertEqual(resp.content, "It works.")
        self.assertEqual(resp.provider, "gemini")
        self.assertEqual(resp.finish_reason, FinishReason.STOP)
        self.assertEqual(resp.usage.input_tokens, 11)
        self.assertEqual(resp.usage.output_tokens, 6)
        self.assertEqual(resp.tool_calls, [])
        self.assertEqual(resp.raw_metadata["response_id"], "resp-fake-1")


class TestGeminiToolDefinitions(unittest.TestCase):
    """C. ToolDefinition -> Gemini functionDeclarations."""

    def setUp(self):
        self.provider = make_provider()

    def test_tool_definitions_mapped(self):
        tool = ToolDefinition(
            name="read_text_file",
            description="Read a UTF-8 text file.",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        )
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("ok"))
            self.provider.generate(AIRequest.simple("use the tool", tools=[tool]))
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["tools"], [{"functionDeclarations": [{
            "name": "read_text_file",
            "description": "Read a UTF-8 text file.",
            "parameters": tool.input_schema,
        }]}])


class TestGeminiToolCalls(unittest.TestCase):
    """D. Gemini functionCall -> ToolUseRequest + canonical blocks."""

    def setUp(self):
        self.provider = make_provider()

    def test_function_call_normalized(self):
        body = tool_call_body([("list_directory", {"path": "/tmp"})])
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, body)
            resp = self.provider.generate(AIRequest.simple("list /tmp"))
        self.assertEqual(resp.finish_reason, FinishReason.TOOL_USE)
        self.assertEqual(len(resp.tool_calls), 1)
        tc = resp.tool_calls[0]
        self.assertEqual(tc.name, "list_directory")
        self.assertEqual(tc.arguments, {"path": "/tmp"})
        self.assertTrue(tc.id)  # synthetic id exists for correlation
        blocks = resp.raw_metadata["content_blocks"]
        tool_blocks = [b for b in blocks if b.get("type") == "tool_use"]
        self.assertEqual(len(tool_blocks), 1)
        self.assertEqual(tool_blocks[0]["name"], "list_directory")
        self.assertEqual(tool_blocks[0]["input"], {"path": "/tmp"})

    def test_multiple_function_calls_all_normalized(self):
        body = tool_call_body([("a", {"x": 1}), ("b", {"y": 2})])
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, body)
            resp = self.provider.generate(AIRequest.simple("do two things"))
        self.assertEqual([tc.name for tc in resp.tool_calls], ["a", "b"])
        self.assertNotEqual(resp.tool_calls[0].id, resp.tool_calls[1].id)

    def test_text_plus_tool_call_both_present(self):
        body = tool_call_body([("a", {})])
        body["candidates"][0]["content"]["parts"].insert(0, {"text": "let me do that"})
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, body)
            resp = self.provider.generate(AIRequest.simple("x"))
        self.assertEqual(resp.content, "let me do that")
        self.assertEqual(resp.finish_reason, FinishReason.TOOL_USE)


class TestGeminiToolResults(unittest.TestCase):
    """E. tool_result history blocks -> functionResponse parts."""

    def setUp(self):
        self.provider = make_provider()

    def _history(self, use_json_payload=True):
        if use_json_payload:
            result_content = json.dumps({"success": True, "result": "FILE DATA", "error": None, "tool": "read_text_file"})
        else:
            result_content = "plain result text"
        return [
            Message(role="user", content="read the file"),
            Message(role="assistant", content=[
                {"type": "tool_use", "id": "gemini-call-1", "name": "read_text_file", "input": {"path": "/a.txt"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "gemini-call-1", "content": result_content, "is_error": False},
            ]),
        ]

    def test_tool_result_maps_to_function_response_with_name(self):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("done"))
            self.provider.generate(AIRequest(messages=self._history()))
        contents = mock_post.call_args.kwargs["json"]["contents"]
        self.assertEqual([c["role"] for c in contents], ["user", "model", "user"])
        fr = contents[2]["parts"][0]["functionResponse"]
        self.assertEqual(fr["name"], "read_text_file")
        self.assertTrue(fr["response"]["success"])
        self.assertEqual(fr["response"]["result"], "FILE DATA")

    def test_tool_result_name_falls_back_to_tool_use_history(self):
        history = self._history()
        # Payload JSON lacks the explicit "tool" key -> name resolved from
        # the preceding assistant tool_use block via tool_use_id.
        history[2].content[0]["content"] = json.dumps({"success": True, "result": "X"})
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("done"))
            self.provider.generate(AIRequest(messages=history))
        fr = mock_post.call_args.kwargs["json"]["contents"][2]["parts"][0]["functionResponse"]
        self.assertEqual(fr["name"], "read_text_file")

    def test_tool_error_state_preserved(self):
        history = [
            Message(role="assistant", content=[
                {"type": "tool_use", "id": "gemini-call-1", "name": "write_text_file", "input": {}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "gemini-call-1",
                 "content": json.dumps({"success": False, "error": "permission denied", "tool": "write_text_file"}),
                 "is_error": True},
            ]),
        ]
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("understood"))
            self.provider.generate(AIRequest(messages=history))
        fr = mock_post.call_args.kwargs["json"]["contents"][1]["parts"][0]["functionResponse"]
        self.assertEqual(fr["name"], "write_text_file")
        self.assertFalse(fr["response"]["success"])
        self.assertEqual(fr["response"]["error"], "permission denied")


class TestGeminiMultiTurnHistory(unittest.TestCase):
    """F. Multi-turn assistant-tool-call + tool-result translation."""

    def setUp(self):
        self.provider = make_provider()

    def test_round_trip_preserves_names_and_args(self):
        history = [
            Message(role="user", content="objective"),
            Message(role="assistant", content=[
                {"type": "text", "text": "working on it"},
                {"type": "tool_use", "id": "gemini-call-1", "name": "read_text_file", "input": {"path": "/x"}},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "gemini-call-1",
                 "content": json.dumps({"success": True, "result": "ABC", "tool": "read_text_file"}),
                 "is_error": False},
            ]),
        ]
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("final"))
            resp = self.provider.generate(AIRequest(messages=history))
        contents = mock_post.call_args.kwargs["json"]["contents"]
        assistant_parts = contents[1]["parts"]
        self.assertEqual(assistant_parts[0], {"text": "working on it"})
        self.assertEqual(assistant_parts[1], {"functionCall": {"name": "read_text_file", "args": {"path": "/x"}}})
        fr = contents[2]["parts"][0]["functionResponse"]
        self.assertEqual(fr["name"], "read_text_file")
        self.assertEqual(fr["response"]["result"], "ABC")
        self.assertEqual(resp.content, "final")


class TestGeminiThoughtSignatures(unittest.TestCase):
    """Gemini 3 requires thoughtSignature echo on functionCall parts in
    history (missing -> HTTP 400 on the follow-up turn). The provider must
    preserve them end-to-end inside the canonical content blocks without
    affecting providers/models that never emit them."""

    def setUp(self):
        self.provider = make_provider()

    def test_signature_captured_from_function_call_part(self):
        body = tool_call_body([("write_file", {"path": "a.txt", "content": "x"})])
        body["candidates"][0]["content"]["parts"][0]["thoughtSignature"] = "sig-fake-abc"
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, body)
            resp = self.provider.generate(AIRequest.simple("x"))
        tool_block = [b for b in resp.raw_metadata["content_blocks"] if b["type"] == "tool_use"][0]
        self.assertEqual(tool_block["thought_signature"], "sig-fake-abc")

    def test_signature_reemitted_camel_case_in_history(self):
        history = [
            Message(role="assistant", content=[
                {"type": "tool_use", "id": "gemini-call-1", "name": "read_file",
                 "input": {"path": "a.txt"}, "thought_signature": "sig-fake-abc"},
            ]),
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "gemini-call-1",
                 "content": json.dumps({"success": True, "result": "ok", "tool": "read_file"}),
                 "is_error": False},
            ]),
        ]
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("done"))
            self.provider.generate(AIRequest(messages=history))
        contents = mock_post.call_args.kwargs["json"]["contents"]
        fc_part = contents[0]["parts"][0]
        self.assertEqual(fc_part["functionCall"]["name"], "read_file")
        self.assertEqual(fc_part["thoughtSignature"], "sig-fake-abc")

    def test_no_signature_no_field_emitted(self):
        history = [Message(role="assistant", content=[
            {"type": "tool_use", "id": "gemini-call-1", "name": "read_file", "input": {}},
        ])]
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("done"))
            self.provider.generate(AIRequest(messages=history))
        fc_part = mock_post.call_args.kwargs["json"]["contents"][0]["parts"][0]
        self.assertNotIn("thoughtSignature", fc_part)

    def test_signature_on_text_part_also_round_trips(self):
        history = [Message(role="assistant", content=[
            {"type": "text", "text": "thinking out loud", "thought_signature": "sig-t-1"},
        ])]
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("done"))
            self.provider.generate(AIRequest(messages=history))
        part = mock_post.call_args.kwargs["json"]["contents"][0]["parts"][0]
        self.assertEqual(part, {"text": "thinking out loud", "thoughtSignature": "sig-t-1"})


class TestGeminiUsageAndFinishReasons(unittest.TestCase):
    """G. Usage + finish-reason normalization."""

    def setUp(self):
        self.provider = make_provider()

    def _finish(self, raw):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("x", finish=raw))
            return self.provider.generate(AIRequest.simple("x"))

    def test_max_tokens_maps(self):
        self.assertEqual(self._finish("MAX_TOKENS").finish_reason, FinishReason.MAX_TOKENS)

    def test_safety_maps_to_unknown_and_flags_metadata(self):
        resp = self._finish("SAFETY")
        self.assertEqual(resp.finish_reason, FinishReason.UNKNOWN)
        self.assertTrue(resp.raw_metadata["safety_blocked"])

    def test_unrecognized_finish_maps_to_unknown(self):
        resp = self._finish("SOME_FUTURE_REASON")
        self.assertEqual(resp.finish_reason, FinishReason.UNKNOWN)
        self.assertFalse(resp.raw_metadata["safety_blocked"])


class TestGeminiErrorMapping(unittest.TestCase):
    """H. HTTP/runtime failures -> existing ProviderError hierarchy."""

    def setUp(self):
        self.provider = make_provider()

    def _generate_with(self, response=None, side_effect=None):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            if side_effect is not None:
                mock_post.side_effect = side_effect
            else:
                mock_post.return_value = response
            return self.provider.generate(AIRequest.simple("x"))

    def test_401_authentication(self):
        with self.assertRaises(AuthenticationError):
            self._generate_with(fake_response(401, {"error": "bad key"}))

    def test_403_authentication(self):
        with self.assertRaises(AuthenticationError):
            self._generate_with(fake_response(403, {"error": "forbidden"}))

    def test_429_rate_limit_preserves_retry_after(self):
        with self.assertRaises(RateLimitError) as ctx:
            self._generate_with(fake_response(429, {"error": "slow down"}, headers={"retry-after": "12.5"}))
        self.assertEqual(ctx.exception.retry_after, 12.5)

    def test_429_without_header(self):
        with self.assertRaises(RateLimitError) as ctx:
            self._generate_with(fake_response(429, {"error": "slow down"}))
        self.assertIsNone(ctx.exception.retry_after)

    def test_requests_timeout_maps(self):
        import requests
        with self.assertRaises(ProviderTimeoutError):
            self._generate_with(side_effect=requests.exceptions.Timeout())

    def test_connection_error_maps(self):
        import requests
        with self.assertRaises(NetworkError):
            self._generate_with(side_effect=requests.exceptions.ConnectionError())

    def test_invalid_json_maps_to_malformed(self):
        with self.assertRaises(MalformedResponseError):
            self._generate_with(fake_response(200, None))

    def test_empty_candidates_malformed(self):
        with self.assertRaises(MalformedResponseError):
            self._generate_with(fake_response(200, {"candidates": [], "usageMetadata": {}}))

    def test_prompt_blocked_maps_to_unavailable(self):
        body = {"promptFeedback": {"blockReason": "SAFETY"}}
        with self.assertRaises(ProviderUnavailableError):
            self._generate_with(fake_response(200, body))

    def test_server_error_maps_to_unavailable(self):
        for status in (500, 502, 503, 504):
            with self.assertRaises(ProviderUnavailableError):
                self._generate_with(fake_response(status, {"error": "boom"}))

    def test_other_client_error_maps_to_malformed_with_status_only(self):
        with self.assertRaises(MalformedResponseError) as ctx:
            self._generate_with(fake_response(400, {"error": {"message": "bad request", "details": "..."}}))
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertNotIn("bad request", str(ctx.exception))


class TestGeminiSecretSafety(unittest.TestCase):
    """I. The fake key must never surface in errors, responses, URLs, or events."""

    def setUp(self):
        self.provider = make_provider()

    def test_key_only_in_header_never_in_url(self):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("ok"))
            self.provider.generate(AIRequest.simple("x"))
            args, kwargs = mock_post.call_args
        self.assertNotIn(FAKE_KEY, args[0])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], FAKE_KEY)
        self.assertNotIn(FAKE_KEY, json.dumps(kwargs["json"]))

    def test_key_never_in_error_messages(self):
        errors = []
        import requests as _rq
        cases = [
            (fake_response(401, {"error": {"message": f"key {FAKE_KEY} invalid"}}), None),
            (fake_response(403, {"error": {"message": FAKE_KEY}}), None),
            (fake_response(429, {"error": {"message": FAKE_KEY}}), None),
            (fake_response(500, {"error": {"message": FAKE_KEY}}), None),
            (fake_response(200, None), None),
            (None, _rq.exceptions.Timeout()),
            (None, _rq.exceptions.ConnectionError()),
        ]
        for response, side_effect in cases:
            with patch("providers.gemini_provider.requests.post") as mock_post:
                if side_effect is not None:
                    mock_post.side_effect = side_effect
                else:
                    mock_post.return_value = response
                try:
                    self.provider.generate(AIRequest.simple("x"))
                except Exception as e:  # noqa: BLE001 - we are testing error text safety
                    errors.append(str(e))
        self.assertEqual(len(errors), len(cases))
        for msg in errors:
            self.assertNotIn(FAKE_KEY, msg)

    def test_key_never_in_normalized_response(self):
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.return_value = fake_response(200, text_body("ok"))
            resp = self.provider.generate(AIRequest.simple("x"))
        self.assertNotIn(FAKE_KEY, json.dumps(resp.to_dict(), default=str))
        self.assertNotIn(FAKE_KEY, json.dumps(resp.raw_metadata, default=str))


class TestGeminiIsConfigured(unittest.TestCase):
    """J. is_configured() behavior."""

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_configured_true_with_key(self):
        p = make_provider()
        self.assertTrue(p.is_configured())

    def test_configured_false_without_key(self):
        os.environ.pop("GEMINI_API_KEY", None)
        p = GeminiProvider(GeminiConfig())
        self.assertFalse(p.is_configured())

    def test_is_configured_returns_plain_bool(self):
        p = make_provider()
        result = p.is_configured()
        self.assertIsInstance(result, bool)
        self.assertNotIn(FAKE_KEY, str(result))


class TestGeminiInjectionThroughExecutionSession(unittest.TestCase):
    """K. THE single injection test: GeminiProvider drives a real
    ExecutionSession -> orchestration -> ToolUseSession -> REAL ToolRegistry
    permission pipeline, with all Gemini HTTP mocked.

    Proves:
      - the provider plugs into the existing root entry point with zero
        changes outside providers/
      - every tool invocation still goes through ToolRegistry.execute()
        (spied via subclass, never bypassed)
      - the canonical history round trip works across two real turns
      - the fake key travels only in request headers
    """

    def setUp(self):
        self._saved = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_full_session_with_mocked_gemini_http(self):
        from execution import ExecutionSession, SessionState
        from memory import ProjectMemory
        from tools import ToolRegistry, Permission, PermissionConfig

        sandbox = tempfile.mkdtemp(prefix="omega-gemini-injection-")
        pm = ProjectMemory(os.path.join(sandbox, "pm.json"))
        # Full default grants (same registry the Phase-10 successful-session
        # flow uses) so the coding AND testing workers are both staffable and
        # the whole existing permission/audit pipeline is exercised.
        real_registry = ToolRegistry(base_dir=sandbox)
        assert Permission.READ in real_registry.permissions.config.granted

        executions = []

        class SpyRegistry(ToolRegistry):
            """Permission pipeline is untouched: execute() delegates to the
            real ToolRegistry.execute() after recording the call."""

            def __init__(self, source):
                pass

            def execute(self, call):
                executions.append(call)
                return super().execute(call)

        spy = SpyRegistry.__new__(SpyRegistry)
        spy.__dict__.update(real_registry.__dict__)

        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        provider = GeminiProvider(GeminiConfig())

        calc_src = "def add(a, b):\n    return a + b\n"
        test_src = (
            "import unittest\nfrom calculator import add\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        )
        scripted = [
            # P0-1: the build/run stage requires a runnable entry point, so
            # turn 1 also writes main.py (same modeled HTTP turn, 2 tool calls).
            fake_response(200, tool_call_body([
                ("write_file", {"path": "calculator.py", "content": calc_src}),
                ("write_file", {"path": "main.py", "content": "print('calculator entry ok')\n"}),
            ])),
            fake_response(200, tool_call_body([("write_file", {"path": "test_calculator.py", "content": test_src})])),
            fake_response(200, text_body("Calculator implemented and tested.")),
        ]
        seen_calls = []

        def scripted_post(url, **kwargs):
            seen_calls.append({"url": url, **kwargs})
            return scripted[min(len(seen_calls), len(scripted)) - 1]

        session = ExecutionSession(provider=provider, tool_registry=spy, project_memory=pm)
        with patch("providers.gemini_provider.requests.post") as mock_post:
            mock_post.side_effect = scripted_post
            result = session.run("Build a calculator that adds numbers.")

        # 1) The real HTTP seam was driven (3 scripted turns), offline.
        self.assertEqual(len(seen_calls), 3)

        # 2) The fake key traveled ONLY in request headers.
        for call in seen_calls:
            self.assertEqual(call["headers"]["x-goog-api-key"], FAKE_KEY)
            self.assertNotIn(FAKE_KEY, call["url"])
            self.assertNotIn(FAKE_KEY, json.dumps(call["json"], default=str))

        # 3) Every tool invocation went through the REAL ToolRegistry.execute()
        # (spied, not bypassed) - including the permission gate.
        write_calls = [c for c in executions if c.tool_name == "write_file"]
        self.assertEqual(len(write_calls), 3)
        self.assertTrue(
            os.path.exists(os.path.join(sandbox, "calculator.py")),
            "write_file did not execute through the registry",
        )
        self.assertTrue(
            os.path.exists(os.path.join(sandbox, "main.py")),
            "main.py write (build/run entry point) did not execute through the registry",
        )
        # ...and the registry's own permission/audit log recorded them,
        # proving the permission gate evaluated every model-driven call.
        self.assertTrue(
            any(e["call"]["tool_name"] == "write_file" for e in spy.log.entries),
            "registry audit log missing the tool executions",
        )

        # 4) Tool results round-tripped into Gemini history as functionResponse
        # parts with the correct function names (multi-turn contract).
        contents = seen_calls[1]["json"]["contents"]
        fr_parts = [p["functionResponse"] for c in contents for p in c.get("parts", []) if "functionResponse" in p]
        self.assertTrue(any(fr["name"] == "write_file" for fr in fr_parts))
        self.assertTrue(all(fr["response"]["success"] for fr in fr_parts))

        # 5) The session completed through the existing pipeline end-to-end.
        self.assertEqual(result.state, SessionState.COMPLETED.value)
        self.assertEqual(result.execution_status, "COMPLETED")
        self.assertEqual(result.quality_decision, "APPROVED")
        self.assertTrue(result.verification.get("passed"))
        self.assertEqual(result.errors, [])


if __name__ == "__main__":
    unittest.main()
