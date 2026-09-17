"""Offline tests for Gemini's bounded transient retry policy."""

import os
import unittest
from unittest.mock import patch

import requests

from providers import (
    AIRequest,
    AuthenticationError,
    GeminiConfig,
    GeminiProvider,
    MissingAPIKeyError,
    NetworkError,
    ProviderTimeoutError,
    RateLimitError,
)

FAKE_KEY = "gkey-FAKE-retry-test-only"


def response(status=200, body=None, headers=None):
    result = type("Response", (), {})()
    result.status_code = status
    result.headers = headers or {}

    def json_body():
        if body is None:
            raise ValueError("invalid json")
        return body

    result.json = json_body
    return result


def success():
    return response(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ok"}]}}]})


class TestGeminiRetryPolicy(unittest.TestCase):
    def setUp(self):
        self.saved_env = dict(os.environ)
        os.environ["GEMINI_API_KEY"] = FAKE_KEY
        self.provider = GeminiProvider(GeminiConfig())

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)

    def test_default_timeout_is_120_seconds(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(GeminiConfig.from_env().timeout_seconds, 120.0)

    @patch("providers.gemini_provider.time.sleep")
    @patch("providers.gemini_provider.requests.post")
    def test_timeout_retries_then_succeeds(self, post, sleep):
        post.side_effect = [requests.exceptions.Timeout(), requests.exceptions.Timeout(), success()]
        result = self.provider.generate(AIRequest.simple("hello"))
        self.assertEqual(result.content, "ok")
        self.assertEqual(post.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1.0])

    @patch("providers.gemini_provider.time.sleep")
    @patch("providers.gemini_provider.requests.post")
    def test_network_failure_is_bounded(self, post, sleep):
        post.side_effect = requests.exceptions.ConnectionError()
        with self.assertRaises(NetworkError):
            self.provider.generate(AIRequest.simple("hello"))
        self.assertEqual(post.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.5, 1.0, 2.0])

    @patch("providers.gemini_provider.time.sleep")
    @patch("providers.gemini_provider.requests.post")
    def test_rate_limit_retry_after_is_capped(self, post, sleep):
        post.side_effect = [response(429, {"error": "slow"}, {"retry-after": "999"}), success()]
        self.provider.generate(AIRequest.simple("hello"))
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_args.args[0], 8.0)

    @patch("providers.gemini_provider.time.sleep")
    @patch("providers.gemini_provider.requests.post")
    def test_authentication_error_is_not_retried(self, post, sleep):
        post.return_value = response(401, {"error": "unauthorized"})
        with self.assertRaises(AuthenticationError):
            self.provider.generate(AIRequest.simple("hello"))
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    @patch("providers.gemini_provider.time.sleep")
    @patch("providers.gemini_provider.requests.post")
    def test_missing_key_is_not_retried(self, post, sleep):
        os.environ.pop("GEMINI_API_KEY")
        with self.assertRaises(MissingAPIKeyError):
            self.provider.generate(AIRequest.simple("hello"))
        post.assert_not_called()
        sleep.assert_not_called()

    @patch("providers.gemini_provider.time.sleep")
    @patch("providers.gemini_provider.requests.post")
    def test_final_timeout_preserves_typed_error(self, post, sleep):
        post.side_effect = requests.exceptions.Timeout()
        with self.assertRaises(ProviderTimeoutError):
            self.provider.generate(AIRequest.simple("hello"))
        self.assertEqual(post.call_count, 4)


if __name__ == "__main__":
    unittest.main()
