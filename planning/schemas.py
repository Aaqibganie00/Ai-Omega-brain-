"""
PLANNING LAYER — SCHEMAS
-----------------------------
Structured plan representation. ExecutionPlan wraps a set of PlanSteps
(each corresponding 1:1 with an orchestration.SubTask once validated) plus
the metadata (risks, complexity, milestones, acceptance criteria) needed
to reason about a plan before committing to executing it.
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class ComplexityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    OPTIMIZED = "OPTIMIZED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class PlanStep:
    step_id: str
    description: str
    capabilities: set
    dependencies: list = field(default_factory=list)
    priority: int = 0
    estimated_cost: float = 1.0
    estimated_time: float = 1.0
    risk: str = "LOW"
    acceptance_criteria: list = field(default_factory=list)


@dataclass
class PlanRisk:
    category: str
    severity: str
    description: str
    mitigation: str = ""


@dataclass
class AmbiguityResult:
    ambiguous: bool
    missing_information: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)


@dataclass
class Milestone:
    milestone_id: str
    name: str
    step_ids: list
    completed: bool = False
    evidence: str = None


@dataclass
class ResourceEstimate:
    estimated_worker_count: int
    estimated_tool_calls: int
    estimated_execution_time: float
    estimated_parallelism: int
    estimated_context_size: str
    note: str = "Estimates only - not guarantees."


@dataclass
class ExecutionPlan:
    plan_id: str
    task_id: str
    objective: str
    version: int = 1
    assumptions: list = field(default_factory=list)
    requirements: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    milestones: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    estimated_complexity: str = ComplexityLevel.LOW.value
    resource_estimate: object = None
    required_capabilities: set = field(default_factory=set)
    required_permissions: set = field(default_factory=set)
    acceptance_criteria: list = field(default_factory=list)
    status: str = PlanStatus.DRAFT.value
    created_at: float = field(default_factory=time.time)


@dataclass
class ValidationIssue:
    check: str
    detail: str
    severity: str = "BLOCKING"


@dataclass
class PlanValidationResult:
    valid: bool
    issues: list = field(default_factory=list)


@dataclass
class PlanDiff:
    added_steps: list = field(default_factory=list)
    removed_steps: list = field(default_factory=list)
    changed_steps: list = field(default_factory=list)
    changed_dependencies: list = field(default_factory=list)
    changed_requirements: list = field(default_factory=list)
    reason: str = ""


@dataclass
class Checkpoint:
    checkpoint_id: str
    task_id: str
    plan_version: int
    completed_steps: list
    failed_steps: list
    remaining_steps: list
    evidence: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
