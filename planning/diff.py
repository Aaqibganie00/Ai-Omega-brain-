"""
PLANNING LAYER — PLAN DIFF
--------------------------------
Structured comparison between two plan versions.
"""

from .schemas import PlanDiff


def diff_plans(old_plan, new_plan, reason=""):
    old_by_id = {s.step_id: s for s in old_plan.steps}
    new_by_id = {s.step_id: s for s in new_plan.steps}

    added = sorted(set(new_by_id) - set(old_by_id))
    removed = sorted(set(old_by_id) - set(new_by_id))
    common = set(old_by_id) & set(new_by_id)

    changed_steps = []
    changed_dependencies = []
    for sid in sorted(common):
        old_s, new_s = old_by_id[sid], new_by_id[sid]
        if old_s.description != new_s.description or old_s.capabilities != new_s.capabilities:
            changed_steps.append(sid)
        if sorted(old_s.dependencies) != sorted(new_s.dependencies):
            changed_dependencies.append({"step_id": sid, "old": old_s.dependencies, "new": new_s.dependencies})

    changed_requirements = []
    if sorted(old_plan.requirements) != sorted(new_plan.requirements):
        changed_requirements = [{"old": old_plan.requirements, "new": new_plan.requirements}]

    return PlanDiff(
        added_steps=added, removed_steps=removed, changed_steps=changed_steps,
        changed_dependencies=changed_dependencies, changed_requirements=changed_requirements, reason=reason,
    )
