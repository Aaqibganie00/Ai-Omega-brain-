"""
AI PROVIDER LAYER — ROUTER INTEGRATION
-----------------------------------------
Minimal adapter so the EXISTING ModelRouter (router.py, unchanged) can
route to a real AIProvider. This satisfies router.py's ModelBackend
protocol (name, capabilities, run(prompt, **kwargs) -> str) without the
router needing to know anything about AIRequest/AIResponse or Anthropic
specifically.

    Omega Core -> ModelRouter (unchanged) -> ProviderBackend (this file) -> AIProvider -> ClaudeProvider -> Anthropic API

ModelRouter.run() already catches and re-raises exceptions while tracking
success/failure counts, so a provider error (MissingAPIKeyError,
AuthenticationError, etc.) propagates through unchanged.
"""

from .base import AIProvider
from .schemas import AIRequest


class ProviderBackend:
    """Wraps any AIProvider so it satisfies router.ModelBackend."""

    def __init__(self, provider: AIProvider, capabilities: set[str], system_prompt: str = None):
        self.provider = provider
        self.name = provider.name
        self.capabilities = capabilities
        self.system_prompt = system_prompt

    def run(self, prompt: str, **kwargs) -> str:
        request = AIRequest.simple(
            prompt,
            system_prompt=kwargs.get("system_prompt", self.system_prompt),
            model=kwargs.get("model"),
            max_tokens=kwargs.get("max_tokens", 1024),
            temperature=kwargs.get("temperature"),
        )
        response = self.provider.generate(request)
        return response.content
