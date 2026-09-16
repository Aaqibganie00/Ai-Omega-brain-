"""
PHASE 9 — STRATEGY LIFECYCLE
-----------------------------------
Explicit states and explicit transitions. A strategy NEVER moves to
ACTIVE (or back from CONTRADICTED) merely because a model claims it
works - every transition function takes an `evidence` dict and checks it.
"""

import time
from dataclasses import dataclass
from enum import Enum

from .decay import DECAY_AFTER_SECONDS


class StrategyState(str, Enum):
    NEW = "NEW"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    CONTRADICTED = "CONTRADICTED"
    DEPRECATED = "DEPRECATED"


@dataclass
class TransitionResult:
    new_state: str
    changed: bool
    reason: str


class StrategyLifecycleManager:
    def transition(self, current_state, evidence):
        if evidence.get("deprecate"):
            return self._result(current_state, StrategyState.DEPRECATED.value, "explicit deprecation")

        if evidence.get("contradiction_detected"):
            if current_state in (StrategyState.VALIDATED.value, StrategyState.ACTIVE.value):
                return self._result(current_state, StrategyState.CONTRADICTED.value, "contradiction detected against verified evidence")

        if current_state == StrategyState.NEW.value:
            if evidence.get("evidence_confidence") == "HIGH":
                return self._result(current_state, StrategyState.VALIDATED.value, "HIGH evidence confidence achieved (Quality Gate APPROVED)")
            return self._result(current_state, current_state, "insufficient evidence to validate")

        if current_state == StrategyState.VALIDATED.value:
            if evidence.get("reused") and evidence.get("reuse_succeeded"):
                return self._result(current_state, StrategyState.ACTIVE.value, "reused at least once with a successful, Quality-Gate-verified outcome")
            return self._result(current_state, current_state, "not yet reused successfully")

        if current_state == StrategyState.ACTIVE.value:
            last_used = evidence.get("last_used_timestamp")
            if last_used is not None and (time.time() - last_used) > DECAY_AFTER_SECONDS:
                return self._result(current_state, StrategyState.STALE.value, "not reused within the decay window")
            return self._result(current_state, current_state, "still active")

        if current_state == StrategyState.STALE.value:
            if evidence.get("reused") and evidence.get("reuse_succeeded"):
                return self._result(current_state, StrategyState.ACTIVE.value, "re-verified by a successful recent reuse")
            return self._result(current_state, current_state, "remains stale - no recent re-verification")

        return self._result(current_state, current_state, f"{current_state} is terminal in this MVP - no automatic exit")

    def _result(self, old_state, new_state, reason):
        return TransitionResult(new_state=new_state, changed=(old_state != new_state), reason=reason)
