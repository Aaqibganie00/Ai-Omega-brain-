"""
QUALITY CONTROL LAYER — REQUIREMENTS EXTRACTION
-----------------------------------------------------
Deterministic extraction (per item 2's explicit allowance for simple
tasks). Explicit caller-supplied lists (required_files/features/tests) are
authoritative; a light keyword scan of the objective text adds obvious
feature hints on top, but never removes or overrides what the caller
stated. No model call here - nothing to treat as "proposal not truth"
since there's no model input.
"""

import re
from .schemas import TaskRequirements

_FEATURE_KEYWORDS = {
    "add": ["addition", "add "], "subtract": ["subtraction", "subtract"],
    "multiply": ["multiplication", "multiply"], "divide": ["division", "divide"],
}


def extract_requirements(objective: str, required_files=None, required_features=None,
                          required_tests=None, constraints=None, acceptance_criteria=None,
                          entry_command=None) -> TaskRequirements:
    features = list(required_features or [])
    lowered = objective.lower()
    for feature, hints in _FEATURE_KEYWORDS.items():
        if feature not in features and any(h in lowered for h in hints):
            features.append(feature)

    return TaskRequirements(
        objective=objective,
        required_files=list(required_files or []),
        required_features=features,
        required_tests=list(required_tests or []),
        constraints=list(constraints or []),
        acceptance_criteria=list(acceptance_criteria or []),
        entry_command=entry_command,
    )
