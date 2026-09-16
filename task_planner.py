"""
TASK PLANNER
------------
Turns a user request into a DAG of subtasks with dependencies.

MVP honesty note: real decomposition of an arbitrary request ("build me a
3D multiplayer game") requires a strong reasoning model in the loop. Here we
implement the real DATA STRUCTURE (a proper DAG with dependency resolution
and parallel-batch scheduling) and a small rule-based planner for a few
task categories, so the mechanism is genuinely functional and testable.
Swapping the rule-based planner for an LLM call is a drop-in replacement
(see `LLMTaskPlanner` stub at the bottom).
"""

from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict, deque


@dataclass
class Task:
    id: str
    description: str
    worker_type: str          # which specialized worker should handle it
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"   # pending | running | done | failed


class TaskGraph:
    def __init__(self):
        self.tasks: dict[str, Task] = {}

    def add(self, task: Task):
        self.tasks[task.id] = task

    def ready_batch(self) -> list[Task]:
        """Returns all tasks whose dependencies are satisfied and that
        haven't run yet - i.e. what CAN run in parallel right now."""
        ready = []
        for t in self.tasks.values():
            if t.status != "pending":
                continue
            if all(self.tasks[d].status == "done" for d in t.depends_on):
                ready.append(t)
        return ready

    def mark(self, task_id: str, status: str):
        self.tasks[task_id].status = status

    def is_complete(self) -> bool:
        return all(t.status == "done" for t in self.tasks.values())

    def has_failed(self) -> bool:
        return any(t.status == "failed" for t in self.tasks.values())

    def execution_order_preview(self) -> list[list[str]]:
        """Topological batches, for display/debugging (Kahn's algorithm)."""
        indegree = {tid: len(t.depends_on) for tid, t in self.tasks.items()}
        deps_of = defaultdict(list)
        for tid, t in self.tasks.items():
            for d in t.depends_on:
                deps_of[d].append(tid)

        batches = []
        frontier = deque([tid for tid, d in indegree.items() if d == 0])
        seen_indegree = dict(indegree)
        while frontier:
            batch = list(frontier)
            batches.append(batch)
            frontier = deque()
            for tid in batch:
                for nxt in deps_of[tid]:
                    seen_indegree[nxt] -= 1
                    if seen_indegree[nxt] == 0:
                        frontier.append(nxt)
        return batches


class RuleBasedPlanner:
    """
    Small, honest rule-based planner covering three request categories
    from the spec's own benchmark (section 18): web app, game prototype,
    and a general research/analysis task. Anything else falls back to a
    generic single-task plan.
    """

    def plan(self, request: str) -> TaskGraph:
        request_l = request.lower()
        graph = TaskGraph()

        if any(k in request_l for k in ["web app", "website", "web application"]):
            graph.add(Task("requirements", "Clarify requirements", "planning_worker"))
            graph.add(Task("architecture", "Design architecture", "architecture_worker", ["requirements"]))
            graph.add(Task("backend", "Build backend", "coding_worker", ["architecture"]))
            graph.add(Task("frontend", "Build frontend", "coding_worker", ["architecture"]))
            graph.add(Task("tests", "Write & run tests", "testing_worker", ["backend", "frontend"]))
            graph.add(Task("review", "Code review", "review_worker", ["tests"]))

        elif any(k in request_l for k in ["game", "3d", "prototype"]):
            graph.add(Task("design", "Game design doc", "planning_worker"))
            graph.add(Task("engine", "Select engine/framework", "architecture_worker", ["design"]))
            graph.add(Task("controller", "Player controller", "game_dev_worker", ["engine"]))
            graph.add(Task("environment", "Environment/level", "game_dev_worker", ["engine"]))
            graph.add(Task("logic", "Core game logic", "game_dev_worker", ["controller", "environment"]))
            graph.add(Task("tests", "Playtest/verify", "testing_worker", ["logic"]))

        elif any(k in request_l for k in ["research", "analyze", "analysis", "report"]):
            graph.add(Task("scope", "Define research scope", "planning_worker"))
            graph.add(Task("gather", "Gather sources", "research_worker", ["scope"]))
            graph.add(Task("crosscheck", "Cross-check sources", "research_worker", ["gather"]))
            graph.add(Task("synthesize", "Synthesize findings", "planning_worker", ["crosscheck"]))
            graph.add(Task("verify", "Verify conclusions", "review_worker", ["synthesize"]))

        else:
            graph.add(Task("generic", request, "planning_worker"))

        return graph


class LLMTaskPlanner:
    """
    STUB: this is where a real reasoning-model call would replace the
    rule-based planner above. Not implemented in this MVP since no model
    API is wired in this environment. Interface kept identical so it's a
    drop-in swap: `planner = LLMTaskPlanner(client)` instead of
    `planner = RuleBasedPlanner()`.
    """

    def __init__(self, client=None):
        self.client = client

    def plan(self, request: str) -> TaskGraph:
        raise NotImplementedError(
            "Wire an actual model client here to replace RuleBasedPlanner."
        )
