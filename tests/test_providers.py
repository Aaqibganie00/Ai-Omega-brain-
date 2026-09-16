"""
Tests for the AI provider layer. Everything here runs offline - no API
key or network required. Run with:  python3 -m unittest tests.test_providers -v
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import (
    AIRequest, AIResponse, Message, FinishReason,
    ClaudeConfig, ClaudeProvider, MockAIProvider, ProviderBackend,
    MissingAPIKeyError, AuthenticationError, RateLimitError,
    ProviderTimeoutError, NetworkError, MalformedResponseError, ProviderUnavailableError,
)
from router import ModelRouter


class TestAIRequestSchema(unittest.TestCase):
    def test_simple_constructor(self):
        req = AIRequest.simple("hello there")
        self.assertEqual(len(req.messages), 1)
        self.assertEqual(req.messages[0].role, "user")
        self.assertEqual(req.messages[0].content, "hello there")

    def test_request_has_unique_id(self):
        r1 = AIRequest.simple("a")
        r2 = AIRequest.simple("b")
        self.assertNotEqual(r1.request_id, r2.request_id)

    def test_defaults(self):
        req = AIRequest.simple("hi")
        self.assertEqual(req.max_tokens, 1024)
        self.assertIsNone(req.model)


class TestClaudeConfig(unittest.TestCase):
    def test_no_key_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = ClaudeConfig.from_env()
            self.assertIsNone(cfg.api_key)

    def test_key_read_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-12345"}, clear=True):
            cfg = ClaudeConfig.from_env()
            self.assertEqual(cfg.api_key, "sk-test-12345")

    def test_model_override_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-custom-model"}, clear=True):
            cfg = ClaudeConfig.from_env()
            self.assertEqual(cfg.resolved_model, "claude-custom-model")

    def test_default_model_used_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = ClaudeConfig.from_env()
            self.assertEqual(cfg.resolved_model, cfg.default_model)

    def test_redacted_never_includes_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-super-secret-value"}, clear=True):
            cfg = ClaudeConfig.from_env()
            redacted = cfg.redacted()
            self.assertNotIn("sk-super-secret-value", str(redacted))
            self.assertTrue(redacted["api_key_configured"])


class TestClaudeProviderMissingKey(unittest.TestCase):
    def test_is_configured_false_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
            self.assertFalse(provider.is_configured())

    def test_generate_raises_missing_key_error(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
            with self.assertRaises(MissingAPIKeyError):
                provider.generate(AIRequest.simple("hello"))

    def test_missing_key_error_names_env_var_not_value(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
            try:
                provider.generate(AIRequest.simple("hello"))
            except MissingAPIKeyError as e:
                self.assertIn("ANTHROPIC_API_KEY", str(e))

    def test_is_configured_true_with_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
            self.assertTrue(provider.is_configured())


class TestClaudeProviderResponseHandling(unittest.TestCase):
    """These mock the HTTP layer (requests.post) so they run fully offline
    but exercise the REAL parsing/error-mapping logic in claude_provider.py."""

    def setUp(self):
        self._env_patcher = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"}, clear=True)
        self._env_patcher.start()
        self.provider = ClaudeProvider(ClaudeConfig.from_env())

    def tearDown(self):
        self._env_patcher.stop()

    def _provider(self):
        return self.provider

    @patch("providers.claude_provider.requests.post")
    def test_successful_response_is_normalized(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "id": "msg_123", "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "Hello back!"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )
        provider = self._provider()
        response = provider.generate(AIRequest.simple("hi"))
        self.assertIsInstance(response, AIResponse)
        self.assertEqual(response.content, "Hello back!")
        self.assertEqual(response.provider, "claude")
        self.assertEqual(response.finish_reason, FinishReason.STOP)
        self.assertEqual(response.usage.input_tokens, 5)

    @patch("providers.claude_provider.requests.post")
    def test_401_raises_authentication_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401, json=lambda: {})
        provider = self._provider()
        with self.assertRaises(AuthenticationError):
            provider.generate(AIRequest.simple("hi"))

    @patch("providers.claude_provider.requests.post")
    def test_429_raises_rate_limit_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=429, headers={"retry-after": "2"}, json=lambda: {})
        provider = self._provider()
        with self.assertRaises(RateLimitError) as ctx:
            provider.generate(AIRequest.simple("hi"))
        self.assertEqual(ctx.exception.retry_after, 2.0)

    @patch("providers.claude_provider.requests.post")
    def test_503_raises_unavailable_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=503, json=lambda: {})
        provider = self._provider()
        with self.assertRaises(ProviderUnavailableError):
            provider.generate(AIRequest.simple("hi"))

    @patch("providers.claude_provider.requests.post")
    def test_malformed_json_raises_malformed_response_error(self, mock_post):
        def raise_value_error():
            raise ValueError("no json")
        mock_post.return_value = MagicMock(status_code=200, json=raise_value_error)
        provider = self._provider()
        with self.assertRaises(MalformedResponseError):
            provider.generate(AIRequest.simple("hi"))

    @patch("providers.claude_provider.requests.post")
    def test_unexpected_response_shape_raises_malformed_response_error(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"unexpected": "shape"})
        provider = self._provider()
        with self.assertRaises(MalformedResponseError):
            provider.generate(AIRequest.simple("hi"))

    @patch("providers.claude_provider.requests.post")
    def test_timeout_raises_provider_timeout_error(self, mock_post):
        import requests as requests_module
        mock_post.side_effect = requests_module.exceptions.Timeout()
        provider = self._provider()
        with self.assertRaises(ProviderTimeoutError):
            provider.generate(AIRequest.simple("hi"))

    @patch("providers.claude_provider.requests.post")
    def test_connection_error_raises_network_error(self, mock_post):
        import requests as requests_module
        mock_post.side_effect = requests_module.exceptions.ConnectionError()
        provider = self._provider()
        with self.assertRaises(NetworkError):
            provider.generate(AIRequest.simple("hi"))

    @patch("providers.claude_provider.requests.post")
    def test_api_key_never_appears_in_raised_error_text(self, mock_post):
        mock_post.return_value = MagicMock(status_code=401, json=lambda: {})
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-VERY-SECRET-VALUE"}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
            try:
                provider.generate(AIRequest.simple("hi"))
                self.fail("expected AuthenticationError")
            except AuthenticationError as e:
                self.assertNotIn("sk-VERY-SECRET-VALUE", str(e))

    @patch("providers.claude_provider.requests.post")
    def test_api_key_sent_in_header_not_body(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "id": "msg_1", "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn", "usage": {},
            },
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-header-check"}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
            provider.generate(AIRequest.simple("hi"))
            _, kwargs = mock_post.call_args
            self.assertEqual(kwargs["headers"]["x-api-key"], "sk-header-check")
            self.assertNotIn("sk-header-check", str(kwargs["json"]))


class TestMockProvider(unittest.TestCase):
    def test_is_always_configured(self):
        self.assertTrue(MockAIProvider().is_configured())

    def test_returns_canned_response(self):
        provider = MockAIProvider(canned_response="fixed answer")
        response = provider.generate(AIRequest.simple("anything"))
        self.assertEqual(response.content, "fixed answer")
        self.assertEqual(response.provider, "mock")

    def test_records_calls_received(self):
        provider = MockAIProvider()
        provider.generate(AIRequest.simple("call one"))
        provider.generate(AIRequest.simple("call two"))
        self.assertEqual(len(provider.calls_received), 2)

    def test_can_simulate_timeout(self):
        provider = MockAIProvider(simulate_timeout=True)
        with self.assertRaises(ProviderTimeoutError):
            provider.generate(AIRequest.simple("hi"))

    def test_default_response_echoes_prompt(self):
        provider = MockAIProvider()
        response = provider.generate(AIRequest.simple("what is 2+2"))
        self.assertIn("what is 2+2", response.content)


class TestRouterIntegration(unittest.TestCase):
    """Confirms the EXISTING ModelRouter (unmodified) can route to a
    provider through the ProviderBackend adapter."""

    def test_mock_provider_routes_through_existing_router(self):
        router = ModelRouter()
        backend = ProviderBackend(MockAIProvider(canned_response="42"), capabilities={"planning_worker"})
        router.register(backend)

        backend_name, output = router.run("planning_worker", "what is the answer?")
        self.assertEqual(backend_name, "mock")
        self.assertEqual(output, "42")

    def test_router_tracks_failure_on_provider_error(self):
        router = ModelRouter()
        backend = ProviderBackend(MockAIProvider(simulate_timeout=True), capabilities={"coding_worker"})
        router.register(backend)

        with self.assertRaises(ProviderTimeoutError):
            router.run("coding_worker", "write code")

        self.assertEqual(router.performance["mock"].failures, 1)

    def test_claude_backend_registers_with_correct_capability(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
        backend = ProviderBackend(provider, capabilities={"coding_worker", "planning_worker"})
        router = ModelRouter()
        router.register(backend)
        self.assertIn("claude", router.candidates_for("coding_worker"))
        self.assertIn("claude", router.candidates_for("planning_worker"))

    def test_unconfigured_claude_backend_raises_missing_key_through_router(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = ClaudeProvider(ClaudeConfig.from_env())
        backend = ProviderBackend(provider, capabilities={"planning_worker"})
        router = ModelRouter()
        router.register(backend)
        with self.assertRaises(MissingAPIKeyError):
            router.run("planning_worker", "plan something")


if __name__ == "__main__":
    unittest.main()
