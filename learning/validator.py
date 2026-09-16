"""
LEARNING LAYER — EXPERIENCE VALIDATOR
--------------------------------------------
The core safety property of Phase 8: an experience NEVER receives HIGH
evidence_confidence on the strength of a model's claim alone. Every signal
here comes from something a prior phase already verified independently -
Quality Gate decision, real test result, TestIntegrityChecker result -
never from `model_claimed_success`, which is recorded but explicitly
excluded from the confidence computation.
"""

from .schemas import KnowledgeConfidence


class ExperienceValidator:
    def validate(self, evidence):
        reasons = []

        if evidence.get("permission_denied_occurred"):
            reasons.append("a permission was denied during execution - cannot be trusted as a clean success")
            return KnowledgeConfidence.LOW.value, reasons

        if evidence.get("integrity_ok") is False:
            reasons.append("test-integrity violation detected - never eligible for positive reusable knowledge")
            return KnowledgeConfidence.LOW.value, reasons

        quality_gate = evidence.get("quality_gate_result")
        tests_passed = evidence.get("tests_passed")
        files_exist = evidence.get("required_files_exist")

        if quality_gate == "APPROVED" and tests_passed is True and files_exist is not False:
            reasons.append("Quality Gate APPROVED with passing tests and required files present")
            return KnowledgeConfidence.HIGH.value, reasons

        if quality_gate in ("REJECTED", "BLOCKED", "INCOMPLETE"):
            reasons.append(f"Quality Gate did not approve (result={quality_gate}) - not eligible for high confidence")
            return KnowledgeConfidence.LOW.value, reasons

        if tests_passed is True and quality_gate is None:
            reasons.append("tests passed but no Quality Gate decision available - capped at MEDIUM")
            return KnowledgeConfidence.MEDIUM.value, reasons

        if tests_passed is False:
            reasons.append("tests did not pass")
            return KnowledgeConfidence.LOW.value, reasons

        reasons.append("insufficient evidence to establish confidence")
        return KnowledgeConfidence.LOW.value, reasons

    def is_reusable_as_positive_knowledge(self, evidence_confidence):
        return evidence_confidence == KnowledgeConfidence.HIGH.value
