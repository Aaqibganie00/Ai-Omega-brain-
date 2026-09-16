"""
PLANNING LAYER — AMBIGUITY DETECTION
------------------------------------------
Deterministic. Flags objectives that are too vague to plan concretely,
rather than silently inventing requirements to fill the gap.
"""

import re
from .schemas import AmbiguityResult

_VAGUE_TERMS = re.compile(r'\b(better|nicer|good|improve|enhance|optimize|clean up|fix things|more robust)\b', re.IGNORECASE)
_CONCRETE_SIGNALS = re.compile(r'\b(add|create|implement|test|fix|calculator|function|file|class|api|endpoint)\b', re.IGNORECASE)


class AmbiguityDetector:
    def detect(self, objective):
        missing = []
        word_count = len(objective.split())

        if word_count <= 3:
            missing.append("objective is very short - unclear what specifically should change")

        vague_match = _VAGUE_TERMS.search(objective)
        concrete_match = _CONCRETE_SIGNALS.search(objective)
        if vague_match and not concrete_match:
            missing.append(f"uses subjective/vague language ('{vague_match.group(0)}') without a concrete target")

        if not concrete_match and word_count < 8:
            missing.append("no concrete deliverable (file, feature, function) is named")

        ambiguous = len(missing) > 0
        assumptions = []
        if ambiguous:
            assumptions.append("no scope assumption was made - plan generation should be deferred until clarified")

        return AmbiguityResult(ambiguous=ambiguous, missing_information=missing, assumptions=assumptions)
