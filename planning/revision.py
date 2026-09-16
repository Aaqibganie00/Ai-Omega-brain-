"""
PLANNING LAYER — PLAN REVISION
------------------------------------
Given evidence that invalidates the current plan, produces a new plan
version rather than restarting the whole system. The new version is
re-validated before use - a revision is never trusted more than an
original plan.
"""

import copy
from .diff import diff_plans


class PlanReviser:
    def revise(self, plan, evidence, reason):
        new_plan = copy.deepcopy(plan)
        new_plan.version = plan.version + 1
        new_plan.status = "DRAFT"

        failed_step_id = evidence.get("failed_step")
        if failed_step_id:
            failed_step = next((s for s in new_plan.steps if s.step_id == failed_step_id), None)
            if failed_step is not None:
                debug_step_id = f"{failed_step_id}-debug"
                if not any(s.step_id == debug_step_id for s in new_plan.steps):
                    from .schemas import PlanStep
                    debug_step = PlanStep(
                        step_id=debug_step_id, description=f"Repair failure in: {failed_step.description}",
                        capabilities={"debugging"}, dependencies=[failed_step_id],
                        acceptance_criteria=["failure evidence addressed", "tests pass"],
                    )
                    new_plan.steps.append(debug_step)
                    for s in new_plan.steps:
                        if s.step_id != debug_step_id and failed_step_id in s.dependencies:
                            s.dependencies = [debug_step_id if d == failed_step_id else d for d in s.dependencies]

        new_plan.assumptions = list(plan.assumptions) + [f"revised due to: {reason}"]
        plan_diff = diff_plans(plan, new_plan, reason=reason)
        return new_plan, plan_diff
