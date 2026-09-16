"""AI PROVIDER LAYER — GEMINI CONFIGURATION
-------------------------------------------
Same contract as ClaudeConfig: no API key or model name is ever hard-coded
in source - everything is read from os.environ, with sane defaults for the
non-secret settings only. Numeric environment overrides are validated so a
typo fails fast with a clear message (which never contains the key).

Environment variables:
    GEMINI_API_KEY           - required for real calls (never logged)
    GEMINI_MODEL             - optional model override
    GEMINI_TIMEOUT_SECONDS   - optional, must be a positive number
    GEMINI_MAX_TOKENS        - optional, must be a positive integer
    GEMINI_TEMPERATURE       - optional, must be a non-negative number
"""

import os
from dataclasses import dataclass


def _positive_float_env(var_name: str, default: float) -> float:
    raw = os.environ.get(var_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{var_name} must be a number.")
    if value <= 0:
        raise ValueError(f"{var_name} must be positive.")
    return value


def _positive_int_env(var_name: str, default: int) -> int:
    raw = os.environ.get(var_name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{var_name} must be an integer.")
    if value <= 0:
        raise ValueError(f"{var_name} must be positive.")
    return value


def _optional_float_env(var_name: str):
    raw = os.environ.get(var_name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{var_name} must be a number.")
    if value < 0:
        raise ValueError(f"{var_name} must be non-negative.")
    return value


@dataclass
class GeminiConfig:
    api_key_env_var: str = "GEMINI_API_KEY"
    model: str = None
    default_model: str = "gemini-3.8-flash"
    timeout_seconds: float = 30.0
    max_tokens: int = 2048
    temperature: float = None
    base_url: str = "https://generativelanguage.googleapis.com"
    api_version: str = "v1beta"

    @classmethod
    def from_env(cls) -> "GeminiConfig":
        return cls(
            model=os.environ.get("GEMINI_MODEL"),
            timeout_seconds=_positive_float_env("GEMINI_TIMEOUT_SECONDS", 30.0),
            max_tokens=_positive_int_env("GEMINI_MAX_TOKENS", 2048),
            temperature=_optional_float_env("GEMINI_TEMPERATURE"),
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
