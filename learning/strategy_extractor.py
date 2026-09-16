"""
LEARNING LAYER — STRATEGY EXTRACTOR
------------------------------------------
Only extracts a reusable Strategy from experiences the ExperienceValidator
rated HIGH confidence - a strategy is never built from an unverified
"success".
"""

import uuid
from .schemas import Strategy
from .validator import ExperienceValidator


class StrategyExtractor:
    def __init__(self, validator=None):
        self.validator = validator or ExperienceValidator()

    def extract(self, record):
        if not self.validator.is_reusable_as_positive_knowledge(record.evidence_confidence):
            return None

        step_sequence = [{"worker": w, "tools": record.tools_used} for w in record.workers_used] or \
                         [{"worker": "unknown", "tools": record.tools_used}]

        return Strategy(
            strategy_id=f"strat-{uuid.uuid4().hex[:8]}", task_type=record.task_type,
            step_sequence=step_sequence, outcome="SUCCESS",
        )
