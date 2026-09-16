"""
QUALITY CONTROL LAYER — SCHEMAS
-----------------------------------
Structured types for requirements extraction, evidence-based verification,
test-integrity checking, file-change auditing, scope checking, and the
final Quality Gate decision.
"""

from dataclasses import dataclass, field
from enum import Enum


class RequirementStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"           # never silently treated as PASS


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKING = "BLOCKING"


class QualityGateDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"


@dataclass
class TaskRequirements:
    objective: str
    required_files: list = field(default_factory=list)
    required_features: list = field(default_factory=list)
    required_tests: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    acceptance_criteria: list = field(default_factory=list)
    entry_command: str = None      # optional runtime-verification command


@dataclass
class RequirementCheck:
    requirement: str
    status: str            # RequirementStatus value
    evidence: str
    severity: str = Severity.MEDIUM.value


@dataclass
class TestIntegrityResult:
    tests_before: set = field(default_factory=set)
    tests_created: set = field(default_factory=set)
    tests_modified: set = field(default_factory=set)
    tests_deleted: set = field(default_factory=set)
    suspicious: list = field(default_factory=list)
    integrity_ok: bool = True


@dataclass
class FileChangeAudit:
    created: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    unchanged: list = field(default_factory=list)


@dataclass
class ScopeResult:
    in_scope_changes: list = field(default_factory=list)
    out_of_scope_changes: list = field(default_factory=list)
    severity: str = Severity.INFO.value


@dataclass
class RegressionResult:
    baseline_tests: dict = field(default_factory=dict)   # {test_id: "ok"|"FAIL"|"ERROR"}
    final_tests: dict = field(default_factory=dict)
    regressions: list = field(default_factory=list)       # tests that regressed
    regression_detected: bool = False


@dataclass
class CriticResultV2:
    approved: bool
    issues: list = field(default_factory=list)     # list[{"issue": str, "severity": str}]
    severity: str = Severity.INFO.value             # overall max severity across issues
    evidence: list = field(default_factory=list)
    confidence: dict = field(default_factory=lambda: {"model_confidence": None, "evidence_confidence": None})
    recommendations: list = field(default_factory=list)


@dataclass
class VerificationReport:
    task_id: str
    requirements: TaskRequirements
    requirement_checks: list
    files_created: list
    files_modified: list
    files_deleted: list
    tests_before: list
    tests_after: list
    regression: RegressionResult
    runtime_status: dict
    critic: CriticResultV2
    integrity: TestIntegrityResult
    scope: ScopeResult
    out_of_scope_changes: list
    final_decision: str
    events: list = field(default_factory=list)
