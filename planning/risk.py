"""
PLANNING LAYER — RISK ANALYSIS
------------------------------------
Deterministic pattern-based risk detection over the plan's objective and
steps, plus (optionally) prior repair history from ProjectMemory.
"""

import re
from .schemas import PlanRisk

_DESTRUCTIVE_PATTERNS = re.compile(r'\b(delete|remove|drop|rm -rf|truncate|wipe|destroy)\b', re.IGNORECASE)
_NETWORK_PATTERNS = re.compile(r'\b(download|fetch|api call|external service|internet|curl|http)\b', re.IGNORECASE)
_AMBIGUOUS_VERBS = re.compile(r'\b(better|improve|nice|good|optimize|enhance|fix things|clean up)\b', re.IGNORECASE)


class RiskAnalyzer:
    def analyze(self, plan, project_memory=None):
        risks = []

        if _DESTRUCTIVE_PATTERNS.search(plan.objective):
            risks.append(PlanRisk("destructive_operation", "HIGH",
                                   "Objective language suggests a destructive operation (delete/remove/drop/etc).",
                                   "Require explicit confirmation before any DESTRUCTIVE-permission tool call."))

        if len(plan.steps) > 10:
            risks.append(PlanRisk("large_file_changes", "MEDIUM",
                                   f"Plan has {len(plan.steps)} steps - larger surface area for unintended changes.",
                                   "Review file-change audit closely after execution."))

        if _NETWORK_PATTERNS.search(plan.objective):
            risks.append(PlanRisk("external_network_requirement", "MEDIUM",
                                   "Objective implies external network access.",
                                   "Confirm NETWORK permission is deliberately granted, not assumed."))

        if _AMBIGUOUS_VERBS.search(plan.objective):
            risks.append(PlanRisk("ambiguous_requirements", "MEDIUM",
                                   "Objective uses vague/subjective language.",
                                   "Run AmbiguityDetector before committing to this plan."))

        has_coding = any("coding" in s.capabilities for s in plan.steps)
        has_testing = any("testing" in s.capabilities for s in plan.steps)
        if has_coding and not has_testing:
            risks.append(PlanRisk("missing_tests", "HIGH",
                                   "Plan implements code but has no testing step.",
                                   "Add a testing step before execution, or verify separately."))

        dependency_edges = sum(len(s.dependencies) for s in plan.steps)
        if len(plan.steps) > 0 and dependency_edges / len(plan.steps) > 2:
            risks.append(PlanRisk("high_dependency_complexity", "MEDIUM",
                                   f"Average of {dependency_edges/len(plan.steps):.1f} dependencies per step.",
                                   "Consider whether all dependency edges are truly required."))

        if project_memory is not None:
            prior_repairs = self._count_prior_repairs(project_memory, plan.task_id)
            if prior_repairs >= 2:
                risks.append(PlanRisk("repeated_repair_history", "HIGH",
                                       f"{prior_repairs} prior repair session(s) recorded for related task(s).",
                                       "Consider revising the plan itself rather than repairing the same approach again."))

        return risks

    def _count_prior_repairs(self, project_memory, task_id):
        count = 0
        for key in project_memory.all().keys():
            if key.startswith("repair_session:") and task_id in key:
                count += 1
        return count
