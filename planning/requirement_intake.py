"""
PLANNING LAYER — REQUIREMENT INTAKE (untrusted model output -> validated object)
------------------------------------------------------------------------------

Purpose: the SMALLEST clean bridge between raw user text and the existing
execution pipeline:

    raw user text
      -> AIRequest (this module; ZERO ToolDefinitions)
      -> AIProvider (Gemini when configured; never simulated here)
      -> STRICT validation ladder (fail-closed)
      -> RequirementObject (normalized objective built by OUR code)

Hard rules (by design, enforced, and tested):

  - Gemini is a requirement INTERPRETER at this stage, never an actor.
    The intake request declares no tools. If the model still returns
    functionCall parts, the response is rejected (hard security boundary);
    nothing is executed, forwarded, or converted into a ToolCall.
  - All model-produced strings (summary/features/constraints) are
    UNTRUSTED DATA: validated, length-capped, stripped of control
    characters, and never executed or passed to any tool. Actual
    execution stays with ToolRegistry/PermissionManager/classifier/
    sandbox/audit/QualityGate, unchanged.
  - normalized_objective is constructed BY THIS MODULE from validated
    fields. The model never dictates the executable objective string.
  - Standard library only. No global state. No API key handling
    (the provider owns key access). No filesystem/shell/registry access.
"""

import json
from dataclasses import dataclass, field

from providers.schemas import AIRequest

# ---------------------------------------------------------------------------
# limits / contract constants
# ---------------------------------------------------------------------------

MAX_RAW_REQUEST_CHARS = 1000
MAX_RESPONSE_CHARS = 4000
INTAKE_MAX_TOKENS = 512

ALLOWED_DELIVERABLE_TYPES = ("app", "game", "script", "research")
MVP_LANGUAGE = "python"

REQUIRED_KEYS = ("summary", "deliverable_type", "features", "constraints", "language")

SUMMARY_MIN, SUMMARY_MAX = 10, 300
FEATURES_MIN, FEATURES_MAX = 1, 8
FEATURE_ITEM_MIN, FEATURE_ITEM_MAX = 3, 80
CONSTRAINTS_MAX_ITEMS = 5
CONSTRAINT_ITEM_MAX = 100

INTAKE_SYSTEM_PROMPT = (
    "You are a requirements extraction component inside a software-building system. "
    "Read the user request and reply with EXACTLY ONE JSON object and nothing else.\n"
    "Schema: {\"summary\": string (10-300 chars, one line, concise description of the "
    "deliverable), \"deliverable_type\": one of \"app\" | \"game\" | \"script\" | "
    "\"research\", \"features\": array of 1-8 short strings (each 3-80 chars, the "
    "distinct features/functions the user asked for), \"constraints\": array of 0-5 "
    "short strings (each <= 100 chars, constraints such as 'must include tests'), "
    "\"language\": \"python\"}.\n"
    "Rules: no markdown, no code fences, no prose, no explanation, no tool calls, "
    "no function calls. Do not execute anything. Do not propose shell commands. "
    "Do not claim any verification or completion. Extract requirements only. "
    "If the request is ambiguous or not a software deliverable, still return the "
    "closest matching JSON and express the uncertainty inside the constraints array."
)


class IntakeError(ValueError):
    """Raised when user input or model output fails intake validation.

    Fail-closed by contract: raise for ANY violation; callers must treat this as
    'request not understood' and must never execute anything as a fallback.
    """


@dataclass
class RequirementObject:
    """Validated, system-trusted intake artifact. Model text travels ONLY as
    validated data fields; normalized_objective is built in code."""

    raw_request: str
    normalized_objective: str
    summary: str
    deliverable_type: str
    features: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    language: str = ""

    def as_dict(self) -> dict:
        return {
            "raw_request": self.raw_request,
            "normalized_objective": self.normalized_objective,
            "summary": self.summary,
            "deliverable_type": self.deliverable_type,
            "features": list(self.features),
            "constraints": list(self.constraints),
            "language": self.language,
        }


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------

def _reject(reason: str) -> None:
    raise IntakeError(reason)


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text)


def _clean_single_line(value: str, what: str, min_len: int, max_len: int) -> str:
    if not isinstance(value, str):
        _reject(f"{what} must be a string")
    value = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    value = " ".join(value.split())  # collapse whitespace
    if _has_control_chars(value):
        _reject(f"{what} contains control characters")
    if not (min_len <= len(value) <= max_len):
        _reject(f"{what} length must be {min_len}..{max_len} chars (got {len(value)})")
    return value


def _validate_raw_request(raw_text: str) -> str:
    if not isinstance(raw_text, str):
        _reject("request must be text")
    text = raw_text.strip()
    if not text:
        _reject("empty request")
    if len(text) > MAX_RAW_REQUEST_CHARS:
        _reject(f"request too long (max {MAX_RAW_REQUEST_CHARS} chars)")
    if _has_control_chars(text):
        _reject("request contains control characters")
    return text


def _strip_single_fence(text: str) -> str:
    """Allow ONLY the whole-content single ```json ... ``` fence form.
    Anything else (prose around, multiple fences) fails closed."""
    if not text.startswith("```"):
        return text
    if text.count("```") != 2 or not text.endswith("```"):
        _reject("model output uses an unsafe fence format")
    first_nl = text.find("\n")
    if first_nl == -1:
        _reject("malformed fence format")
    inner = text[first_nl + 1:-3].strip()
    if "```" in inner:
        _reject("nested/multiple fences in model output")
    return inner


def _validate_features(value) -> list:
    if not isinstance(value, list):
        _reject("'features' must be a list of strings")
    if not (FEATURES_MIN <= len(value) <= FEATURES_MAX):
        _reject(f"'features' must have {FEATURES_MIN}..{FEATURES_MAX} items (got {len(value)})")
    out = []
    for i, item in enumerate(value):
        out.append(_clean_single_line(item, f"features[{i}]", FEATURE_ITEM_MIN, FEATURE_ITEM_MAX))
    return out


def _validate_constraints(value) -> list:
    if not isinstance(value, list):
        _reject("'constraints' must be a list of strings")
    if len(value) > CONSTRAINTS_MAX_ITEMS:
        _reject(f"'constraints' must have at most {CONSTRAINTS_MAX_ITEMS} items (got {len(value)})")
    out = []
    for i, item in enumerate(value):
        out.append(_clean_single_line(item, f"constraints[{i}]", 1, CONSTRAINT_ITEM_MAX))
    return out


# ---------------------------------------------------------------------------
# intake stages
# ---------------------------------------------------------------------------

def build_intake_request(raw_text: str) -> AIRequest:
    """Construct the single requirement-extraction request.

    CRITICAL: NO ToolDefinition is attached. The provider may therefore only
    produce plain text; any tool-call-shaped response is treated as a hostile
    anomaly by extract_and_validate().
    """
    text = _validate_raw_request(raw_text)
    prompt = (
        "Extract structured requirements from the following user request.\n"
        "Reply with ONLY one JSON object. User request:\n"
        f"<<<\n{text}\n>>>"
    )
    return AIRequest.simple(prompt, system_prompt=INTAKE_SYSTEM_PROMPT, max_tokens=INTAKE_MAX_TOKENS, temperature=0)


def extract_and_validate(raw_text: str, provider) -> RequirementObject:
    """raw user text -> provider -> strictly validated RequirementObject.

    Provider errors (MissingAPIKeyError, AuthenticationError, RateLimitError,
    ProviderTimeoutError, NetworkError, ProviderUnavailableError) propagate
    unchanged - callers report them honestly; they are never converted into a
    fake successful intake. Malformed/hallucinated/injecting CONTENT raises
    IntakeError. Nothing from the raw response is ever executed.
    """
    text = _validate_raw_request(raw_text)
    request = build_intake_request(text)
    response = provider.generate(request)  # typed provider errors propagate raw

    # HARD SECURITY BOUNDARY: intake declared zero tools. A response carrying
    # any tool call is an injection/malfunction and must die here.
    if getattr(response, "tool_calls", None):
        _reject("model response attempted tool calls during requirement intake")

    content = getattr(response, "content", None)
    if not isinstance(content, str) or not content.strip():
        _reject("empty model response")
    if len(content) > MAX_RESPONSE_CHARS:
        _reject(f"model response exceeds {MAX_RESPONSE_CHARS} chars")

    candidate = _strip_single_fence(content.strip())

    try:
        data = json.loads(candidate)
    except (ValueError, TypeError) as e:
        _reject(f"model output is not a single JSON object ({e})")
    if not isinstance(data, dict):
        _reject("model output must be a JSON object")

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        _reject(f"model output missing required fields: {missing}")

    summary = _clean_single_line(data["summary"], "summary", SUMMARY_MIN, SUMMARY_MAX)

    deliverable_type = data["deliverable_type"]
    if not isinstance(deliverable_type, str) or deliverable_type.strip().lower() not in ALLOWED_DELIVERABLE_TYPES:
        _reject(f"'deliverable_type' must be one of {ALLOWED_DELIVERABLE_TYPES}")
    deliverable_type = deliverable_type.strip().lower()

    features = _validate_features(data["features"])
    constraints = _validate_constraints(data["constraints"])

    language = data["language"]
    if not isinstance(language, str) or language.strip().lower() != MVP_LANGUAGE:
        _reject(f"unsupported language: only '{MVP_LANGUAGE}' is in scope for this MVP")
    language = language.strip().lower()

    # Constructed HERE, never quoted from the model: the executable objective
    # is deterministic given validated fields.
    normalized_objective = f"Build {deliverable_type}: {summary}"

    return RequirementObject(
        raw_request=text,
        normalized_objective=normalized_objective,
        summary=summary,
        deliverable_type=deliverable_type,
        features=features,
        constraints=constraints,
        language=language,
    )
