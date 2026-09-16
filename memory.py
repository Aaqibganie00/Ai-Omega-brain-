"""
MEMORY SYSTEM
-------------
Three layers, kept intentionally simple for the MVP:

  1. WorkingMemory     - state of the CURRENT task only (cleared per run)
  2. ProjectMemory      - persists across runs (JSON file on disk)
  3. ExecutionMemory     - log of what was tried, what worked/failed, and why

Design rule (per spec): memory is never treated as automatically true.
Anything read back via `recall()` comes tagged with its verification status,
so callers can decide whether to trust it.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MemoryEntry:
    key: str
    value: Any
    verified: bool = False
    source: str = "unknown"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self):
        return self.__dict__


class WorkingMemory:
    """Scratchpad for the task currently being executed. Not persisted."""

    def __init__(self):
        self._store: dict[str, MemoryEntry] = {}

    def set(self, key: str, value: Any, verified: bool = False, source: str = "worker"):
        self._store[key] = MemoryEntry(key, value, verified, source)

    def get(self, key: str) -> Optional[MemoryEntry]:
        return self._store.get(key)

    def dump(self):
        return {k: v.to_dict() for k, v in self._store.items()}


class ProjectMemory:
    """Long-term memory persisted to disk as JSON. One file per project."""

    def __init__(self, path: str = "project_memory.json"):
        self.path = path
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                self._store = json.load(f)
        else:
            self._store = {}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._store, f, indent=2)

    def remember(self, key: str, value: Any, verified: bool = False, source: str = "unknown"):
        self._store[key] = MemoryEntry(key, value, verified, source).to_dict()
        self._save()

    def recall(self, key: str) -> Optional[dict]:
        """Returns the entry INCLUDING its verified flag - caller decides trust."""
        return self._store.get(key)

    def all(self):
        return self._store


class ExecutionMemory:
    """
    Append-only log: what was attempted, the outcome, and why.
    This is what the Error Recovery Engine reads before deciding a new strategy.
    """

    def __init__(self):
        self.log: list[dict] = []

    def record(self, task_id: str, action: str, outcome: str, reason: str = ""):
        entry = {
            "task_id": task_id,
            "action": action,
            "outcome": outcome,   # "success" | "failure" | "partial"
            "reason": reason,
            "timestamp": time.time(),
        }
        self.log.append(entry)
        return entry

    def history_for(self, task_id: str):
        return [e for e in self.log if e["task_id"] == task_id]

    def failures(self):
        return [e for e in self.log if e["outcome"] == "failure"]
