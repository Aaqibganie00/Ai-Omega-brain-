"""
PLANNING LAYER — PLAN OPTIMIZER
------------------------------------
Identifies opportunities (independent steps, redundant steps) without ever
removing a dependency that encodes an actual requirement. "Optimization"
here means REPORTING opportunities and merging only exact duplicate steps.
"""

from .schemas import ExecutionPlan


class PlanOptimizer:
    def optimize(self, plan):
        independent = [s.step_id for s in plan.steps if not s.dependencies]

        seen = {}
        redundant_ids = set()
        for step in plan.steps:
            key = (step.description, frozenset(step.capabilities), tuple(sorted(step.dependencies)))
            if key in seen:
                redundant_ids.add(step.step_id)
            else:
                seen[key] = step.step_id

        kept_steps = []
        removed = []
        id_remap = {}
        for step in plan.steps:
            key = (step.description, frozenset(step.capabilities), tuple(sorted(step.dependencies)))
            canonical_id = seen[key]
            if step.step_id == canonical_id:
                kept_steps.append(step)
            else:
                removed.append(step.step_id)
                id_remap[step.step_id] = canonical_id

        for step in kept_steps:
            step.dependencies = [id_remap.get(d, d) for d in step.dependencies]

        parallel_groups = self._find_parallel_groups(kept_steps)

        optimized = ExecutionPlan(
            plan_id=plan.plan_id, task_id=plan.task_id, objective=plan.objective, version=plan.version,
            assumptions=list(plan.assumptions) + ([f"merged redundant steps: {removed}"] if removed else []),
            requirements=list(plan.requirements), steps=kept_steps, milestones=plan.milestones,
            risks=plan.risks, estimated_complexity=plan.estimated_complexity, resource_estimate=plan.resource_estimate,
            required_capabilities=plan.required_capabilities, required_permissions=plan.required_permissions,
            acceptance_criteria=plan.acceptance_criteria, status="OPTIMIZED",
        )
        optimized.independent_steps = independent
        optimized.parallel_groups = parallel_groups
        optimized.redundant_steps_removed = removed
        return optimized

    def _find_parallel_groups(self, steps):
        groups = {}
        for step in steps:
            key = tuple(sorted(step.dependencies))
            groups.setdefault(key, []).append(step.step_id)
        return [g for g in groups.values() if len(g) > 1]
