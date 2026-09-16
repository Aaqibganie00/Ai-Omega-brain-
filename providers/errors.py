"""
AI PROVIDER LAYER — ERRORS
----------------------------
Structured application errors. Every error message here is checked to
never contain the API key - see tests/test_providers.py for a test that
actively asserts this.
"""


class ProviderError(Exception):
    """Base class for all provider-layer errors."""


class MissingAPIKeyError(ProviderError):
    def __init__(self, env_var_name: str):
        super().__init__(
            f"No API key found. Set the '{env_var_name}' environment variable."
        )


class AuthenticationError(ProviderError):
    def __init__(self, provider: str):
        super().__init__(f"{provider}: authentication failed (invalid or revoked API key).")


class RateLimitError(ProviderError):
    def __init__(self, provider: str, retry_after: float = None):
        msg = f"{provider}: rate limited."
        if retry_after:
            msg += f" Retry after {retry_after}s."
        super().__init__(msg)
        self.retry_after = retry_after


class ProviderTimeoutError(ProviderError):
    def __init__(self, provider: str, timeout_seconds: float):
        super().__init__(f"{provider}: request timed out after {timeout_seconds}s.")


class NetworkError(ProviderError):
    def __init__(self, provider: str, detail: str = ""):
        super().__init__(f"{provider}: network error.{(' ' + detail) if detail else ''}")


class MalformedResponseError(ProviderError):
    def __init__(self, provider: str, detail: str = ""):
        super().__init__(f"{provider}: malformed/unexpected response.{(' ' + detail) if detail else ''}")


class ProviderUnavailableError(ProviderError):
    def __init__(self, provider: str, detail: str = ""):
        super().__init__(f"{provider}: unavailable.{(' ' + detail) if detail else ''}")
