"""
PLANNING LAYER — COMPLEXITY & RESOURCE ESTIMATION
--------------------------------------------------------
Deterministic, threshold-based. Explicitly labeled as estimates - never
treated as guarantees anywhere downstream.
"""

from collections import defaultdict
from .schemas import ComplexityLevel, ResourceEstimate


def estimate_complexity(plan):
    steps = len(plan.steps)
    dependency_edges = sum(len(s.dependencies) for s in plan.steps)
    distinct_tools_needed = len({c for s in plan.steps for c in s.capabilities})
    has_testing = any("testing" in s.capabilities for s in plan.steps)
    has_debugging = any("debugging" in s.capabilities for s in plan.steps)

    score = steps + dependency_edges + distinct_tools_needed
    if has_testing:
        score += 1
    if has_debugging:
        score += 2

    if score <= 3:
        return ComplexityLevel.LOW.value
    if score <= 7:
        return ComplexityLevel.MEDIUM.value
    if score <= 14:
        return ComplexityLevel.HIGH.value
    return ComplexityLevel.VERY_HIGH.value


def estimate_resources(plan):
    steps = plan.steps
    worker_count = len(steps)
    per_step_calls = {"coding": 4, "debugging": 6, "testing": 1, "review": 1, "verification": 2, "planning": 1, "research": 1}
    tool_calls = sum(max((per_step_calls.get(c, 2) for c in s.capabilities), default=2) for s in steps)

    execution_time = sum(s.estimated_time for s in steps)

    groups = defaultdict(int)
    for s in steps:
        groups[tuple(sorted(s.dependencies))] += 1
    parallelism = max(groups.values(), default=1)

    context_size = "SMALL" if worker_count <= 3 else ("MEDIUM" if worker_count <= 8 else "LARGE")

    return ResourceEstimate(
        estimated_worker_count=worker_count, estimated_tool_calls=tool_calls,
        estimated_execution_time=execution_time, estimated_parallelism=parallelism,
        estimated_context_size=context_size,
    )
