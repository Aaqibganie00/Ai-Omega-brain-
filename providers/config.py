"""
AI PROVIDER LAYER — CONFIGURATION
------------------------------------
Central, environment-based configuration. No API key or model name is
ever hard-coded in source - everything here is read from os.environ with
sane defaults for the non-secret settings only.
"""

import os
from dataclasses import dataclass


@dataclass
class ClaudeConfig:
    api_key_env_var: str = "ANTHROPIC_API_KEY"
    model: str = None
    default_model: str = "claude-sonnet-4-6"
    timeout_seconds: float = 30.0
    max_tokens: int = 1024
    temperature: float = None
    base_url: str = "https://api.anthropic.com"
    api_version: str = "2023-06-01"

    @classmethod
    def from_env(cls) -> "ClaudeConfig":
        return cls(
            model=os.environ.get("ANTHROPIC_MODEL"),
            timeout_seconds=float(os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "30")),
            max_tokens=int(os.environ.get("ANTHROPIC_MAX_TOKENS", "1024")),
            temperature=(
                float(os.environ["ANTHROPIC_TEMPERATURE"])
                if "ANTHROPIC_TEMPERATURE" in os.environ else None
            ),
        )

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env_var)

    @property
    def resolved_model(self) -> str:
        return self.model or self.default_model

    def redacted(self) -> dict:
        """Safe-to-log view of this config - never includes the key itself."""
        return {
            "model": self.resolved_model,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "api_key_configured": bool(self.api_key),
        }
