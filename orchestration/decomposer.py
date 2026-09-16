"""
ORCHESTRATION LAYER — TASK DECOMPOSER
-----------------------------------------
Deterministic, task-dependent decomposition (mirrors the honesty of
Phase 1's RuleBasedPlanner - no fake "always spawn everything"). Produces
SubTasks with explicit dependencies.
"""

from .schemas import SubTask


class TaskDecomposer:
    def decompose(self, task_id, objective):
        lowered = objective.lower()

        if "research" in lowered and ("compare" in lowered or "and" in lowered):
            r1 = SubTask(f"{task_id}-research-a", task_id, f"Research angle A for: {objective}", {"research"})
            r2 = SubTask(f"{task_id}-research-b", task_id, f"Research angle B for: {objective}", {"research"})
            r3 = SubTask(f"{task_id}-research-c", task_id, f"Research angle C for: {objective}", {"research"})
            planner = SubTask(f"{task_id}-plan", task_id, f"Synthesize research for: {objective}", {"planning"},
                               dependencies=[r1.subtask_id, r2.subtask_id, r3.subtask_id])
            return [r1, r2, r3, planner]

        if any(k in lowered for k in ("calculator", "project", "implementation", "build")):
            coding = SubTask(f"{task_id}-code", task_id, objective, {"coding"})
            testing = SubTask(f"{task_id}-test", task_id, "Run the test suite.", {"testing"}, dependencies=[coding.subtask_id])
            # P0-1: the build/run stage is a real graph node - the produced
            # artifact is actually executed (via run_command) and its exit
            # code/output becomes gate-consumable evidence. No fake success:
            # a missing/unrunnable entry point fails this step honestly.
            runcheck = SubTask(f"{task_id}-runcheck", task_id, "Execute the built artifact and prove it launches.", {"build_run"}, dependencies=[testing.subtask_id])
            review = SubTask(f"{task_id}-review", task_id, "Review the implementation.", {"review"}, dependencies=[runcheck.subtask_id])
            verification = SubTask(f"{task_id}-verify", task_id, "Verify the completed task.", {"verification"}, dependencies=[review.subtask_id])
            return [coding, testing, runcheck, review, verification]

        return [SubTask(f"{task_id}-code", task_id, objective, {"coding"})]
