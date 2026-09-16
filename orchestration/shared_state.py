"""
ORCHESTRATION LAYER — SHARED TASK STATE
---------------------------------------------
Controlled shared state passed to every worker. Only structured fields are
shared - never full conversation history. Access is via explicit methods
so mutations are traceable, not free-for-all attribute writes from
concurrent worker threads.
"""

import threading
from dataclasses import dataclass, field


@dataclass
class SharedTaskState:
    task_id: str
    objective: str
    subtasks: list = field(default_factory=list)
    worker_results: dict = field(default_factory=dict)
    files_changed: set = field(default_factory=set)
    test_results: list = field(default_factory=list)
    # P0-1: structured build/run evidence (one dict per run-check execution).
    run_results: list = field(default_factory=list)
    # P0-5: orchestrator-provided tool-turn budget for coding workers (None =
    # worker default). Read-only to workers; set once by the Orchestrator.
    max_tool_turns: int = None
    decisions: list = field(default_factory=list)
    status: str = "CREATED"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record_result(self, subtask_id, result):
        with self._lock:
            self.worker_results[subtask_id] = result
            self.files_changed.update(result.files_changed)

    def record_test_result(self, test_result):
        with self._lock:
            self.test_results.append(test_result)

    def record_run_result(self, run_result):
        with self._lock:
            self.run_results.append(run_result)

    def record_decision(self, decision):
        with self._lock:
            self.decisions.append(decision)

    def snapshot(self):
        with self._lock:
            return {
                "task_id": self.task_id, "objective": self.objective,
                "files_changed": sorted(self.files_changed),
                "worker_result_count": len(self.worker_results),
                "status": self.status,
            }
