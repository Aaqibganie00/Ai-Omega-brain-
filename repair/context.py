"""
SELF-CORRECTION LAYER — BOUNDED REPAIR CONTEXT
----------------------------------------------------
Builds the (small) prompt handed to the fixer for one repair attempt.
Deliberately excludes full conversation history - only the current
failure's evidence, the affected files, and a one-line summary of the
immediately previous attempt (if any) are included. This keeps each
repair call's input bounded regardless of how many attempts came before.
"""

from .schemas import FailureAnalysis


def build_repair_prompt(objective: str, analysis: FailureAnalysis, previous_attempt_summary: str = None) -> str:
    parts = [
        f"Original task: {objective}",
        f"Failing test evidence:\n{analysis.evidence[-500:]}",
        f"Failure type: {analysis.failure_type}",
        f"Root cause (best assessment, confidence={analysis.confidence:.1f}): {analysis.root_cause}",
    ]
    if analysis.affected_files:
        parts.append(f"Files to inspect/fix: {', '.join(analysis.affected_files)}")
    if analysis.recommended_fix:
        parts.append(f"Suggested direction: {analysis.recommended_fix}")
    if previous_attempt_summary:
        parts.append(f"Previous attempt did not resolve it: {previous_attempt_summary}")
    parts.append(
        "Use the available tools to inspect the relevant file(s) and apply the minimal fix "
        "needed to make the failing test pass. Do not modify unrelated files."
    )
    return "\n\n".join(parts)
