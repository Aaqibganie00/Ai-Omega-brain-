"""
ORCHESTRATION LAYER — RESULT AGGREGATOR
---------------------------------------------
Combines per-subtask WorkerResults into one task-level summary. Classifies
outcomes rather than concatenating raw output.
"""

from .schemas import WorkerStatus


class ResultAggregator:
    def aggregate(self, shared_state):
        successful, failed, blocked = [], [], []
        warnings, errors = [], []

        for subtask in shared_state.subtasks:
            result = shared_state.worker_results.get(subtask.subtask_id)
            if result is None:
                blocked.append(subtask.subtask_id)
                continue
            if result.status == WorkerStatus.COMPLETED.value:
                successful.append(subtask.subtask_id)
            elif result.status in (WorkerStatus.FAILED.value, WorkerStatus.LIMIT_REACHED.value, WorkerStatus.CANCELLED.value):
                failed.append(subtask.subtask_id)
                errors.extend(result.errors)
            else:
                blocked.append(subtask.subtask_id)

        return {
            "successful_workers": successful,
            "failed_workers": failed,
            "blocked_workers": blocked,
            "files_changed": sorted(shared_state.files_changed),
            "tests": shared_state.test_results,
            # P0-1 content (build/run evidence list)
            "runs": list(shared_state.run_results),
            "warnings": warnings,
            "errors": errors,
        }
