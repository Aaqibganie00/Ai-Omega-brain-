"""
ORCHESTRATION LAYER — SCHEMAS
---------------------------------
Structured types for dynamic multi-worker orchestration. Reuses
task_planner.Task/TaskGraph (Phase 1, unmodified) for dependency
resolution rather than reimplementing a DAG - SubTask below is a parallel,
richer record (capabilities, priority, worker assignment) kept in sync
with a Task of the same id in a TaskGraph instance.
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class WorkerStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass
class WorkerDefinition:
    worker_type: str
    capabilities: set
    required_permissions: set
    description: str
    factory: object
    status: str = "AVAILABLE"


@dataclass
class WorkerResult:
    worker_id: str
    task_id: str
    status: str
    output: str = None
    files_changed: list = field(default_factory=list)
    tools_used: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    execution_time: float = 0.0


@dataclass
class SubTask:
    subtask_id: str
    parent_task_id: str
    description: str
    required_capabilities: set
    dependencies: list = field(default_factory=list)
    priority: int = 0
    status: str = "pending"
    worker_type: str = None
    depth: int = 0


@dataclass
class WorkerMessage:
    sender: str
    receiver: str
    task_id: str
    message_type: str
    payload: dict
    timestamp: float = field(default_factory=time.time)


@dataclass
class ResourceLimits:
    max_concurrent_workers: int = 3
    max_total_workers: int = 8
    max_task_depth: int = 3
    worker_timeout_seconds: float = 30.0
    # P0-5: tool-call turn budget for model-driven coding sessions on the
    # orchestration path. Existing constructor seam; the ToolUseSession's own
    # default (6) remains for directly-constructed sessions/repair fixers.
    # 12 is a conservative MVP budget for realistic multi-file creates.
    max_tool_turns: int = 12


@dataclass
class OrchestrationResult:
    task_id: str
    status: str
    subtasks: list = field(default_factory=list)
    worker_results: dict = field(default_factory=dict)
    aggregation: dict = field(default_factory=dict)
    quality_decision: str = None
    events: list = field(default_factory=list)
    performance: dict = field(default_factory=dict)
