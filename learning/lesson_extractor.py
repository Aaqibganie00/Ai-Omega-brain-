"""
LEARNING LAYER — LESSON EXTRACTOR
----------------------------------------
Deterministic, template-based. Every lesson is bound to a concrete piece
of evidence (a file name, a tool name, a test name) already present in the
record - no free-text generation that could produce a vague lesson.
"""

from .schemas import Lesson, LessonType


class LessonExtractor:
    def extract(self, record):
        lessons = []

        for failure in record.failures:
            failure_type = failure.get("failure_type", "UnknownFailure")
            root_cause = failure.get("root_cause", "")
            affected = failure.get("affected_files", [])
            if affected:
                text = f"When modifying {', '.join(affected)}, verify with the relevant test before declaring success (previously failed with: {failure_type})."
            else:
                text = f"Re-run the full test suite after changes related to '{record.task_summary[:60]}' - previously failed with {failure_type}."
            lessons.append(Lesson(lesson_type=LessonType.FAILURE_PATTERN.value, text=text, evidence=root_cause[:200]))

        if record.repair_attempts > 0 and record.quality_gate_result == "APPROVED":
            lessons.append(Lesson(
                lesson_type=LessonType.REPAIR_PATTERN.value,
                text=f"Task '{record.task_summary[:60]}' required {record.repair_attempts} repair attempt(s) before passing - budget at least that many attempts for similar tasks.",
                evidence=f"repair_attempts={record.repair_attempts}",
            ))

        if record.quality_gate_result == "APPROVED" and record.tools_used:
            tool_sequence = " -> ".join(record.tools_used[:6])
            lessons.append(Lesson(
                lesson_type=LessonType.TOOL_PATTERN.value,
                text=f"For tasks like '{record.task_summary[:60]}', the tool sequence [{tool_sequence}] led to a verified pass.",
                evidence="quality_gate_result=APPROVED",
            ))

        if record.quality_gate_result in ("REJECTED", "BLOCKED") and "integrity" in str(record.evidence).lower():
            lessons.append(Lesson(
                lesson_type=LessonType.VERIFICATION_PATTERN.value,
                text="Do not modify test files to resolve a failing test - Quality Gate blocks this; fix the implementation file instead.",
                evidence=str(record.evidence)[:200],
            ))

        return lessons
