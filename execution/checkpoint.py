"""
EXECUTION LAYER (PHASE 10) — SESSION CHECKPOINTS
------------------------------------------------------
Built ON the existing structures, not a new checkpoint architecture:

  - planning.schemas.Checkpoint / CheckpointManager (Phase 7, reused
    unchanged) produce the per-plan step-level checkpoint; the session
    checkpoint EMBEDS it as `plan_checkpoint`.
  - memory.ProjectMemory (Phase 1, reused unchanged) is the persistence
    mechanism, under a "session_checkpoint:" key prefix - the same JSON
    store every prior phase already records into.

Restore semantics are deliberately *reconstruction of recorded state*,
NOT execution resume: no existing phase implements resuming partial
execution, and inventing that here would be a new architecture. A
restored checkpoint yields a validated SessionCheckpoint whose fields a
caller can inspect; invalid/malformed/incompatible payloads are rejected
with explicit reasons, never raise, and never modify stored state.

Validation runs before BOTH persist and restore: a malformed checkpoint
can never be written (it cannot clobber a previously persisted valid one)
and a corrupted stored payload can never be materialized as trusted state.
"""

import time
import uuid
from dataclasses import asdict, dataclass, field

SUPPORTED_SCHEMA_VERSION = 1
KEY_PREFIX = "session_checkpoint:"

_STATE_VALUES = {"CREATED", "RUNNING", "COMPLETED", "FAILED", "REJECTED", "ERROR"}
_KNOWN_PERMISSIONS = {"READ", "WRITE", "EXECUTE", "NETWORK", "DESTRUCTIVE"}
_LIMIT_FIELDS = {"max_concurrent_workers", "max_total_workers", "max_task_depth", "worker_timeout_seconds",
                 "max_tool_turns"}


@dataclass
class SessionCheckpoint:
    checkpoint_id: str
    session_id: str
    task_id: str
    state: str                              # SessionState value
    schema_version: int = SUPPORTED_SCHEMA_VERSION
    plan_checkpoint: dict = None            # asdict of planning.schemas.Checkpoint (step-level state)
    completed_steps: list = field(default_factory=list)
    failed_steps: list = field(default_factory=list)
    remaining_steps: list = field(default_factory=list)
    permission_policy: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    quality_decision: str = None
    evidence: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


def validate_checkpoint_data(value) -> list:
    """Structural validation of a checkpoint payload as stored (dict form).
    Returns a list of rejection reasons; an empty list means valid.
    Never raises - malformed input yields reasons, not exceptions."""
    reasons = []
    if not isinstance(value, dict):
        return ["checkpoint payload is not a dict"]

    for key in ("checkpoint_id", "session_id", "task_id"):
        if not isinstance(value.get(key), str) or not value.get(key):
            reasons.append(f"missing/invalid '{key}'")

    if value.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        reasons.append(
            f"unsupported schema_version: {value.get('schema_version')!r} (supported: {SUPPORTED_SCHEMA_VERSION})"
        )

    if value.get("state") not in _STATE_VALUES:
        reasons.append(f"invalid state: {value.get('state')!r}")

    for key in ("completed_steps", "failed_steps", "remaining_steps"):
        v = value.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            reasons.append(f"'{key}' must be a list of step-id strings")

    policy = value.get("permission_policy", {})
    if not isinstance(policy, dict) or not isinstance(policy.get("granted", []), list):
        reasons.append("'permission_policy' must be a dict with a 'granted' list")
    else:
        unknown = set(policy.get("granted", [])) - _KNOWN_PERMISSIONS
        if unknown:
            reasons.append(f"permission_policy contains unknown permissions: {sorted(unknown)}")

    limits = value.get("limits", {})
    if not isinstance(limits, dict):
        reasons.append("'limits' must be a dict")
    else:
        unknown_keys = set(limits) - _LIMIT_FIELDS
        if unknown_keys:
            reasons.append(f"limits contains unknown fields: {sorted(unknown_keys)}")
        for key in _LIMIT_FIELDS & set(limits):
            v = limits[key]
            if v is None:
                if key != "worker_timeout_seconds":   # only timeout may be null (disabled)
                    reasons.append(f"limits['{key}'] must not be null")
            elif isinstance(v, bool) or not isinstance(v, (int, float)):
                reasons.append(f"limits['{key}'] must be a number")
            elif v < 0:
                reasons.append(f"limits['{key}'] must not be negative")

    pc = value.get("plan_checkpoint")
    if pc is not None:
        if not isinstance(pc, dict) or not isinstance(pc.get("plan_version"), int):
            reasons.append("'plan_checkpoint' must be a dict with an integer plan_version")
        else:
            for key in ("completed_steps", "failed_steps", "remaining_steps"):
                v = pc.get(key, [])
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    reasons.append(f"plan_checkpoint['{key}'] must be a list of step-id strings")

    if not isinstance(value.get("evidence", {}), dict):
        reasons.append("'evidence' must be a dict")

    return reasons


class SessionCheckpointManager:
    """Create / persist / restore session checkpoints on top of ProjectMemory."""

    def __init__(self, project_memory):
        self.project_memory = project_memory

    def create(self, *, session_id, task_id, state, controls=None, plan_checkpoint=None,
               quality_decision=None, evidence=None) -> SessionCheckpoint:
        completed, failed, remaining, plan_cp = [], [], [], None
        if plan_checkpoint is not None:
            plan_cp = plan_checkpoint if isinstance(plan_checkpoint, dict) else asdict(plan_checkpoint)
            completed = list(plan_cp.get("completed_steps", []))
            failed = list(plan_cp.get("failed_steps", []))
            remaining = list(plan_cp.get("remaining_steps", []))
        controls = controls or {}
        return SessionCheckpoint(
            checkpoint_id=f"sc-{uuid.uuid4().hex[:8]}",
            session_id=session_id, task_id=task_id, state=state,
            plan_checkpoint=plan_cp,
            completed_steps=completed, failed_steps=failed, remaining_steps=remaining,
            permission_policy=dict(controls.get("permission_policy", {})),
            limits=dict(controls.get("limits", {})),
            quality_decision=quality_decision,
            evidence=dict(evidence or {}),
        )

    def stored_key(self, session_id) -> str:
        return f"{KEY_PREFIX}{session_id}"

    def persist(self, checkpoint) -> tuple:
        """Persist via the EXISTING ProjectMemory mechanism. Validates BEFORE
        writing - an invalid checkpoint is never written, so a bad payload
        cannot clobber a previously persisted valid checkpoint.
        Returns (True, []) on success, (False, reasons) on rejection.
        Never raises."""
        data = checkpoint if isinstance(checkpoint, dict) else asdict(checkpoint)
        reasons = validate_checkpoint_data(data)
        if reasons:
            return False, reasons
        session_id = data["session_id"]
        self.project_memory.remember(
            key=self.stored_key(session_id),
            value=data,
            verified=(data.get("state") == "COMPLETED"),
            source="execution_session",
        )
        return True, []

    def restore(self, session_id) -> tuple:
        """Reconstruct session state from a persisted checkpoint.
        Returns (SessionCheckpoint, []) on success; (None, reasons) on any
        problem (missing entry, malformed payload, incompatible schema).
        Read-only: restore NEVER mutates stored state, even on failure."""
        entry = self.project_memory.recall(self.stored_key(session_id))
        if entry is None:
            return None, [f"no checkpoint stored for session_id='{session_id}'"]
        value = entry.get("value")
        reasons = validate_checkpoint_data(value)
        if reasons:
            return None, reasons
        checkpoint = SessionCheckpoint(
            checkpoint_id=value["checkpoint_id"],
            session_id=value["session_id"],
            task_id=value["task_id"],
            state=value["state"],
            schema_version=value.get("schema_version", SUPPORTED_SCHEMA_VERSION),
            plan_checkpoint=value.get("plan_checkpoint"),
            completed_steps=list(value.get("completed_steps", [])),
            failed_steps=list(value.get("failed_steps", [])),
            remaining_steps=list(value.get("remaining_steps", [])),
            permission_policy=dict(value.get("permission_policy", {})),
            limits=dict(value.get("limits", {})),
            quality_decision=value.get("quality_decision"),
            evidence=dict(value.get("evidence", {})),
            created_at=value.get("created_at", time.time()),
        )
        return checkpoint, []
