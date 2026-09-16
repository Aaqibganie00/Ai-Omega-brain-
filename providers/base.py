"""
AI PROVIDER LAYER — BASE INTERFACE
-------------------------------------
Every provider (Claude, and later OpenAI/Gemini) implements this same
interface. Omega Core and the router depend only on this class, never on
a provider-specific SDK or payload shape.
"""

from abc import ABC, abstractmethod
from .schemas import AIRequest, AIResponse


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, request: AIRequest) -> AIResponse:
        """Send a request, return a normalized AIResponse. Must raise one
        of the structured errors in providers.errors on failure - never
        return a fabricated/successful-looking response for a failure."""
        raise NotImplementedError

    @abstractmethod
    def is_configured(self) -> bool:
        """True if this provider has what it needs (e.g. an API key) to
        actually attempt a real call. Used by the router to skip providers
        that aren't set up rather than letting them fail at call time."""
        raise NotImplementedError
