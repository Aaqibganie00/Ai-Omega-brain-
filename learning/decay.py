"""
PHASE 9 — CONFIDENCE DECAY & RECOVERY
--------------------------------------------
CRITICAL SAFETY RULE (tested explicitly): decay/recovery only ever moves
confidence WITHIN the LOW/MEDIUM/HIGH ordering established by real
evidence (quality_gate_result). A record whose quality_gate_result was not
APPROVED can never be decayed/recovered INTO HIGH - only records that were
genuinely APPROVED can ever reach or return to HIGH.
"""

import time

_CONFIDENCE_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
_RANK_CONFIDENCE = {v: k for k, v in _CONFIDENCE_RANK.items()}

DECAY_AFTER_SECONDS = 60 * 60 * 24 * 30
DECAY_FULL_SECONDS = 60 * 60 * 24 * 180


def compute_decayed_confidence(experience, related_experiences=None):
    reasons = []
    was_approved = experience.get("quality_gate_result") == "APPROVED"
    original_rank = _CONFIDENCE_RANK.get(experience.get("evidence_confidence", "LOW"), 0)
    ceiling = original_rank

    if not was_approved:
        reasons.append("quality_gate_result was not APPROVED - confidence cannot be raised by age or reuse, only preserved or lowered")
        return experience.get("evidence_confidence", "LOW"), reasons

    age = max(time.time() - experience.get("timestamp", time.time()), 0)
    current_rank = original_rank

    if age > DECAY_FULL_SECONDS:
        current_rank = 0
        reasons.append(f"age {age/86400:.0f}d exceeds full-decay threshold - confidence decayed to LOW")
    elif age > DECAY_AFTER_SECONDS:
        decay_fraction = (age - DECAY_AFTER_SECONDS) / (DECAY_FULL_SECONDS - DECAY_AFTER_SECONDS)
        current_rank = max(0, round(original_rank - decay_fraction * original_rank))
        if current_rank < original_rank:
            reasons.append(f"age {age/86400:.0f}d triggered partial decay ({_RANK_CONFIDENCE[original_rank]} -> {_RANK_CONFIDENCE[current_rank]})")

    related = related_experiences or []
    same_type_approved = [e for e in related if e.get("task_type") == experience.get("task_type") and e.get("quality_gate_result") == "APPROVED"]
    same_type_rejected_after = [
        e for e in related
        if e.get("task_type") == experience.get("task_type")
        and e.get("quality_gate_result") in ("REJECTED", "BLOCKED")
        and e.get("timestamp", 0) > experience.get("timestamp", 0)
    ]

    if len(same_type_approved) >= 2:
        if current_rank < ceiling:
            current_rank = ceiling
            reasons.append(f"{len(same_type_approved)} repeated verified success(es) for this task_type recovered confidence to {_RANK_CONFIDENCE[ceiling]}")

    if same_type_rejected_after:
        current_rank = min(current_rank, _CONFIDENCE_RANK["MEDIUM"])
        reasons.append(f"{len(same_type_rejected_after)} later verified failure(s) for this task_type reduced confidence (capped at MEDIUM)")

    if not reasons:
        reasons.append("no decay/recovery signal applied - confidence unchanged")

    return _RANK_CONFIDENCE[current_rank], reasons
