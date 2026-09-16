"""
PHASE 9 — EXPERIENCE DEDUPLICATION
----------------------------------------
Merges only SUBSTANTIALLY EQUIVALENT experiences (same task_type, high
keyword overlap, AND same quality_gate_result). Records that agree on task
but DISAGREE on outcome are never merged - that's a contradiction, not a
duplicate, and stays fully preserved for contradiction.py.
"""

from .relevance import _keywords

DUPLICATE_KEYWORD_OVERLAP_THRESHOLD = 0.8


class ExperienceDeduplicator:
    def deduplicate(self, experiences):
        rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
        groups = []
        used = [False] * len(experiences)

        for i, exp in enumerate(experiences):
            if used[i]:
                continue
            group = [exp]
            used[i] = True
            kw_i = _keywords(exp.get("task_summary", ""))
            for j in range(i + 1, len(experiences)):
                if used[j]:
                    continue
                other = experiences[j]
                if other.get("task_type") != exp.get("task_type"):
                    continue
                if other.get("quality_gate_result") != exp.get("quality_gate_result"):
                    continue
                kw_j = _keywords(other.get("task_summary", ""))
                overlap = len(kw_i & kw_j) / max(len(kw_i | kw_j), 1)
                if overlap >= DUPLICATE_KEYWORD_OVERLAP_THRESHOLD:
                    group.append(other)
                    used[j] = True
            groups.append(group)

        deduped = []
        merged_count = 0
        for group in groups:
            if len(group) == 1:
                deduped.append(group[0])
                continue
            merged_count += len(group) - 1
            strongest = max(group, key=lambda e: (rank.get(e.get("evidence_confidence", "LOW"), 0), e.get("timestamp", 0)))
            deduped.append(strongest)

        return deduped, merged_count
