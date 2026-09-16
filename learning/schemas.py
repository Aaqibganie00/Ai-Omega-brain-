"""
LEARNING LAYER — SCHEMAS
-----------------------------
Structured experience representation. model_confidence and
evidence_confidence are ALWAYS separate fields on ExperienceRecord -
nothing in this package ever derives evidence_confidence from
model_confidence (see confidence.py).
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class KnowledgeConfidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class KnowledgeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    INVALID = "INVALID"
    SUPERSEDED = "SUPERSEDED"


class LessonType(str, Enum):
    SUCCESS_PATTERN = "SUCCESS_PATTERN"
    FAILURE_PATTERN = "FAILURE_PATTERN"
    TOOL_PATTERN = "TOOL_PATTERN"
    PLANNING_PATTERN = "PLANNING_PATTERN"
    REPAIR_PATTERN = "REPAIR_PATTERN"
    VERIFICATION_PATTERN = "VERIFICATION_PATTERN"
    RISK_PATTERN = "RISK_PATTERN"


@dataclass
class Lesson:
    lesson_type: str
    text: str
    evidence: str


@dataclass
class Strategy:
    strategy_id: str
    task_type: str
    step_sequence: list
    outcome: str
    times_reused: int = 0
    times_reuse_succeeded: int = 0


@dataclass
class ExperienceRecord:
    experience_id: str
    task_id: str
    task_type: str
    task_summary: str
    plan_summary: str
    timestamp: float = field(default_factory=time.time)
    workers_used: list = field(default_factory=list)
    tools_used: list = field(default_factory=list)
    files_changed: list = field(default_factory=list)
    execution_result: str = None
    verification_result: bool = None
    quality_gate_result: str = None
    repair_attempts: int = 0
    failures: list = field(default_factory=list)
    successful_actions: list = field(default_factory=list)
    failed_actions: list = field(default_factory=list)
    lessons: list = field(default_factory=list)
    reusable_patterns: list = field(default_factory=list)
    model_claimed_success: bool = None
    evidence_confidence: str = KnowledgeConfidence.LOW.value
    model_confidence: float = None
    evidence: dict = field(default_factory=dict)
    source: str = "learning_layer"
    version: int = 1
    status: str = KnowledgeStatus.ACTIVE.value

    @staticmethod
    def new_id():
        return f"exp-{uuid.uuid4().hex[:10]}"


@dataclass
class RelevanceScore:
    experience_id: str
    score: float
    explanation: dict
    method: str = "keyword_overlap_heuristic"


@dataclass
class ContradictionResult:
    conflicting: bool
    conflicting_experience_ids: list = field(default_factory=list)
    reason: str = ""
