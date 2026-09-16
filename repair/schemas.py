"""
SELF-CORRECTION LAYER — SCHEMAS
----------------------------------
Explicit task state machine plus structured types for failure analysis,
critic review, and verification. State is tracked explicitly on
ExecutionResult.final_state - never inferred from log text.
"""

from dataclasses import dataclass, field
from enum import Enum


class TaskState(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    TESTING = "TESTING"
    FAILED = "FAILED"
    ANALYZING = "ANALYZING"
    FIXING = "FIXING"
    RETESTING = "RETESTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED_FINAL = "FAILED_FINAL"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass
class FailureAnalysis:
    failure_type: str              # e.g. "AssertionError", "UnknownFailure"
    root_cause: str                # short, evidence-grounded description
    affected_files: list = field(default_factory=list)
    evidence: str = ""             # raw excerpt the conclusion is drawn from
    recommended_fix: str = ""
    confidence: float = 0.0        # heuristic-based analysis is never claimed as certain


@dataclass
class CriticResult:
    approved: bool
    issues: list = field(default_factory=list)
    severity: str = "none"         # "none" | "minor" | "blocking"
    evidence: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)


@dataclass
class TaskVerificationResult:
    passed: bool
    checks: dict = field(default_factory=dict)   # {check_name: bool}
    detail: str = ""


@dataclass
class RepairAttempt:
    attempt_number: int
    failure_type: str
    root_cause: str
    files_changed: list
    test_passed: bool
    state_after: str


@dataclass
class ExecutionResult:
    task_id: str
    final_state: str
    attempts: list = field(default_factory=list)          # list[RepairAttempt]
    files_changed_total: list = field(default_factory=list)
    final_test_result: dict = field(default_factory=dict)
    critic: CriticResult = None
    verification: TaskVerificationResult = None
    events: list = field(default_factory=list)
    status_reason: str = ""
