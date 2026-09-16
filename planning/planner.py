"""
PLANNING LAYER — ADVANCED PLANNER
----------------------------------------
Builds a full ExecutionPlan on top of orchestration.TaskDecomposer (Phase
6, reused unmodified) rather than reimplementing decomposition.
"""

import uuid
from orchestration.decomposer import TaskDecomposer
from .schemas import ExecutionPlan, PlanStep, Milestone
from .complexity import estimate_complexity, estimate_resources
from .risk import RiskAnalyzer
from .ambiguity import AmbiguityDetector


class AdvancedPlanner:
    def __init__(self, task_decomposer=None, risk_analyzer=None, ambiguity_detector=None):
        self.task_decomposer = task_decomposer or TaskDecomposer()
        self.risk_analyzer = risk_analyzer or RiskAnalyzer()
        self.ambiguity_detector = ambiguity_detector or AmbiguityDetector()

    def create_plan(self, objective, task_id=None, project_memory=None, prior_knowledge=None):
        """prior_knowledge (optional): list of retrieved-experience guidance
        dicts (see learning.retriever.KnowledgeRetriever) - PURELY
        informational. Appended to plan.assumptions as text only; never
        read by PlanValidator/PlanOptimizer and never used to alter steps,
        dependencies, capabilities, or permissions. Advisory, not authority."""
        task_id = task_id or uuid.uuid4().hex[:12]
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"

        ambiguity = self.ambiguity_detector.detect(objective)

        subtasks = self.task_decomposer.decompose(task_id, objective)
        steps = [
            PlanStep(
                step_id=st.subtask_id, description=st.description, capabilities=set(st.required_capabilities),
                dependencies=list(st.dependencies), priority=st.priority,
                acceptance_criteria=self._default_acceptance_criteria(st.required_capabilities),
            )
            for st in subtasks
        ]

        milestones = self._build_milestones(steps)
        required_capabilities = {c for s in steps for c in s.capabilities}

        plan = ExecutionPlan(
            plan_id=plan_id, task_id=task_id, objective=objective,
            assumptions=(["objective is well-specified enough to plan concretely"] if not ambiguity.ambiguous else ambiguity.assumptions),
            requirements=[], steps=steps, milestones=milestones,
            required_capabilities=required_capabilities,
            acceptance_criteria=self._top_level_acceptance_criteria(required_capabilities),
        )
        plan.ambiguity = ambiguity

        if prior_knowledge:
            for item in prior_knowledge[:3]:
                lesson_texts = [l.get("text", "") for l in item.get("lessons", [])][:2]
                note = f"Prior experience ({item.get('evidence_confidence', 'LOW')} confidence, relevance={item.get('relevance_score')}): {item.get('task_summary', '')[:100]}"
                if lesson_texts:
                    note += f" | lessons: {'; '.join(lesson_texts)}"
                plan.assumptions.append(note)

        plan.risks = self.risk_analyzer.analyze(plan, project_memory=project_memory)
        plan.estimated_complexity = estimate_complexity(plan)
        plan.resource_estimate = estimate_resources(plan)

        return plan

    def _default_acceptance_criteria(self, capabilities):
        criteria = []
        if "coding" in capabilities:
            criteria.append("relevant files were created or modified")
        if "testing" in capabilities:
            criteria.append("test suite passes")
        if "build_run" in capabilities:
            criteria.append("built artifact executes successfully")
        if "review" in capabilities:
            criteria.append("critic approves with no blocking issues")
        if "verification" in capabilities:
            criteria.append("required files exist and tests pass")
        return criteria or ["step completes without error"]

    def _top_level_acceptance_criteria(self, required_capabilities):
        criteria = ["all steps complete or are explicitly accounted for"]
        if "testing" in required_capabilities:
            criteria.append("tests pass")
        if "build_run" in required_capabilities:
            criteria.append("built artifact executes successfully")
        if "coding" in required_capabilities:
            criteria.append("files were actually changed")
        return criteria

    def _build_milestones(self, steps):
        order = ["planning", "research", "coding", "testing", "build_run", "debugging", "review", "verification"]
        names = {"planning": "Planning", "research": "Research", "coding": "Implementation",
                 "testing": "Testing", "build_run": "Build/Run", "debugging": "Debugging",
                 "review": "Review", "verification": "Verification"}
        milestones = []
        for capability in order:
            step_ids = [s.step_id for s in steps if capability in s.capabilities]
            if step_ids:
                milestones.append(Milestone(milestone_id=f"m-{capability}", name=names[capability], step_ids=step_ids))
        return milestones
