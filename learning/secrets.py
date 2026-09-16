"""
PHASE 9 — SECRET PROTECTION
--------------------------------
Pattern-based secret redaction used by ContextBuilder and PriorKnowledgePack
construction before any text derived from execution output enters memory,
context, or events. Matches common secret SHAPES, not one literal key.
"""

import re

_SECRET_PATTERNS = [
    re.compile(r'sk-[A-Za-z0-9_-]{10,}'),
    re.compile(r'AKIA[0-9A-Z]{16}'),
    re.compile(r'ghp_[A-Za-z0-9]{20,}'),
    re.compile(r'Bearer\s+[A-Za-z0-9_\-.=]{16,}', re.IGNORECASE),
    re.compile(r'(?:api[_-]?key|token|secret|password)\s*[:=]\s*["\']?[A-Za-z0-9_\-.]{8,}["\']?', re.IGNORECASE),
    re.compile(r'[A-Za-z0-9+/]{40,}={0,2}'),
]

REDACTED_PLACEHOLDER = "[REDACTED]"


def redact_secrets(text):
    if not text:
        return text, False
    found = False
    clean = text
    for pattern in _SECRET_PATTERNS:
        if pattern.search(clean):
            found = True
            clean = pattern.sub(REDACTED_PLACEHOLDER, clean)
    return clean, found


def redact_secrets_deep(value):
    if isinstance(value, str):
        clean, _ = redact_secrets(value)
        return clean
    if isinstance(value, dict):
        return {k: redact_secrets_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets_deep(v) for v in value]
    return value
