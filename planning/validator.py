"""
PLANNING LAYER — PLAN VALIDATOR
------------------------------------
Deterministic. An invalid plan never reaches execution.
"""

from .schemas import ValidationIssue, PlanValidationResult

KNOWN_CAPABILITIES = {"planning", "research", "coding", "testing", "build_run", "debugging", "review", "verification"}
KNOWN_PERMISSIONS = {"READ", "WRITE", "EXECUTE", "NETWORK", "DESTRUCTIVE"}
MAX_REASONABLE_DEPTH = 10
MAX_REASONABLE_STEPS = 50


class PlanValidator:
    def validate(self, plan):
        issues = []

        if not plan.steps:
            issues.append(ValidationIssue("empty_plan", "Plan has zero steps."))
            return PlanValidationResult(valid=False, issues=issues)

        step_ids = [s.step_id for s in plan.steps]
        seen = set()
        for sid in step_ids:
            if sid in seen:
                issues.append(ValidationIssue("duplicate_step_id", f"Duplicate step_id: {sid}"))
            seen.add(sid)

        valid_ids = set(step_ids)
        for step in plan.steps:
            for dep in step.dependencies:
                if dep not in valid_ids:
                    issues.append(ValidationIssue("missing_dependency", f"Step '{step.step_id}' depends on unknown step '{dep}'"))
                if dep == step.step_id:
                    issues.append(ValidationIssue("impossible_dependency", f"Step '{step.step_id}' depends on itself"))

        circular = self._find_cycle(plan.steps)
        if circular:
            issues.append(ValidationIssue("circular_dependency", f"Circular dependency detected: {' -> '.join(circular)}"))

        for step in plan.steps:
            unknown_caps = step.capabilities - KNOWN_CAPABILITIES
            if unknown_caps:
                issues.append(ValidationIssue("invalid_capability", f"Step '{step.step_id}' requires unknown capabilities: {unknown_caps}"))

        unknown_perms = {str(p) for p in plan.required_permissions} - KNOWN_PERMISSIONS
        if unknown_perms:
            issues.append(ValidationIssue("invalid_permission", f"Plan requires unknown permissions: {unknown_perms}"))

        depth = self._max_depth(plan.steps)
        if depth > MAX_REASONABLE_DEPTH:
            issues.append(ValidationIssue("excessive_task_depth", f"Dependency chain depth {depth} exceeds {MAX_REASONABLE_DEPTH}"))

        if len(plan.steps) > MAX_REASONABLE_STEPS:
            issues.append(ValidationIssue("excessive_worker_requirements", f"{len(plan.steps)} steps exceeds {MAX_REASONABLE_STEPS}"))

        for step in plan.steps:
            for criterion in step.acceptance_criteria:
                if not isinstance(criterion, str) or not criterion.strip():
                    issues.append(ValidationIssue("malformed_acceptance_criteria", f"Step '{step.step_id}' has an empty/invalid acceptance criterion"))

        return PlanValidationResult(valid=(len(issues) == 0), issues=issues)

    def _find_cycle(self, steps):
        graph = {s.step_id: s.dependencies for s in steps}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {sid: WHITE for sid in graph}
        path = []

        def dfs(node):
            if node not in graph:
                return None
            color[node] = GRAY
            path.append(node)
            for dep in graph[node]:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    cycle_start = path.index(dep)
                    return path[cycle_start:] + [dep]
                if color[dep] == WHITE:
                    result = dfs(dep)
                    if result:
                        return result
            path.pop()
            color[node] = BLACK
            return None

        for sid in graph:
            if color[sid] == WHITE:
                result = dfs(sid)
                if result:
                    return result
        return None

    def _max_depth(self, steps):
        graph = {s.step_id: s.dependencies for s in steps}
        memo = {}

        def depth_of(sid, visiting=None):
            visiting = visiting or set()
            if sid in memo:
                return memo[sid]
            if sid in visiting or sid not in graph:
                return 0
            visiting.add(sid)
            deps = graph[sid]
            result = 1 + max((depth_of(d, visiting) for d in deps), default=0)
            memo[sid] = result
            return result

        return max((depth_of(sid) for sid in graph), default=0)
