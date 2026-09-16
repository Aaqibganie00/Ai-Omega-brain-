"""
PLANNING LAYER — CHECKPOINTS
-----------------------------------
Records completed/failed/remaining steps with evidence, tagged to a plan
version, so execution state can be inspected or resumed safely.
"""

import uuid
from .schemas import Checkpoint


class CheckpointManager:
    def create(self, plan, completed_steps, failed_steps, evidence=None):
        all_ids = {s.step_id for s in plan.steps}
        remaining = sorted(all_ids - set(completed_steps) - set(failed_steps))
        return Checkpoint(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:8]}", task_id=plan.task_id, plan_version=plan.version,
            completed_steps=sorted(completed_steps), failed_steps=sorted(failed_steps),
            remaining_steps=remaining, evidence=evidence or {},
        )

    def milestones_completed(self, plan, completed_steps):
        completed_set = set(completed_steps)
        done = []
        for m in plan.milestones:
            if set(m.step_ids).issubset(completed_set):
                m.completed = True
                m.evidence = f"all steps completed: {m.step_ids}"
                done.append(m)
        return done
