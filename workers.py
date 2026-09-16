"""
SPECIALIZED WORKERS
--------------------
Each worker is a real Python object implementing the ModelBackend
protocol. Since no external model API is wired into this environment,
these backends produce deterministic, honestly-labeled MOCK output -
but the CONTROL FLOW (routing to them, running them, checking their
output) is completely real, not simulated.

To go from "MVP demo" to "production": replace the body of `run()` in
each class with an actual API call (see anthropic_api_in_artifacts docs
for the request shape). Everything else in this project stays the same.
"""

import random


class BaseWorker:
    name: str = "base"
    capabilities: set[str] = set()

    def run(self, prompt: str, **kwargs) -> str:
        raise NotImplementedError


class PlanningWorker(BaseWorker):
    name = "planning_model"
    capabilities = {"planning_worker"}

    def run(self, prompt: str, **kwargs) -> str:
        return f"[PLAN] Breakdown for: '{prompt}'\n- Step 1: clarify scope\n- Step 2: identify constraints\n- Step 3: define done-criteria"


class ArchitectureWorker(BaseWorker):
    name = "architecture_model"
    capabilities = {"architecture_worker"}

    def run(self, prompt: str, **kwargs) -> str:
        return f"[ARCHITECTURE] Proposed structure for: '{prompt}'\n- Layered design (presentation/logic/data)\n- Chosen stack: minimal, dependency-light"


class CodingWorker(BaseWorker):
    name = "coding_model"
    capabilities = {"coding_worker"}

    def run(self, prompt: str, **kwargs) -> str:
        # Produces REAL runnable code for a couple of known request shapes,
        # so the verification engine downstream has something genuine to check.
        if "backend" in prompt.lower():
            return (
                "from flask import Flask, jsonify\n"
                "app = Flask(__name__)\n\n"
                "@app.route('/health')\n"
                "def health():\n"
                "    return jsonify(status='ok')\n\n"
                "if __name__ == '__main__':\n"
                "    app.run(port=5000)\n"
            )
        if "frontend" in prompt.lower():
            return (
                "<!doctype html>\n<html><body>\n"
                "<h1>Hello from Omega Brain MVP</h1>\n"
                "</body></html>\n"
            )
        return f"# code stub for: {prompt}\nprint('implement: {prompt}')\n"


class GameDevWorker(BaseWorker):
    name = "game_dev_model"
    capabilities = {"game_dev_worker"}

    def run(self, prompt: str, **kwargs) -> str:
        return f"[GAME-DEV] Pseudocode module for: '{prompt}'\n(engine-specific implementation would go here - Unity/Godot/Web3D)"


class ResearchWorker(BaseWorker):
    name = "research_model"
    capabilities = {"research_worker"}

    def run(self, prompt: str, **kwargs) -> str:
        return f"[RESEARCH] Findings summary for: '{prompt}'\n(sources would be listed and cross-checked here)"


class TestingWorker(BaseWorker):
    name = "testing_model"
    capabilities = {"testing_worker"}

    def run(self, prompt: str, **kwargs) -> str:
        code = kwargs.get("code_to_test", "")
        if not code:
            return "[TEST] No code provided to test."
        try:
            compile(code, "<submitted>", "exec")
            return "[TEST] Syntax check: PASSED"
        except SyntaxError as e:
            return f"[TEST] Syntax check: FAILED ({e})"


class ReviewWorker(BaseWorker):
    name = "review_model"
    capabilities = {"review_worker"}

    def run(self, prompt: str, **kwargs) -> str:
        # Deliberately introduces occasional "uncertain" results so the
        # verification engine has something real to differentiate.
        confidence = random.choice(["CONFIRMED", "PARTIALLY VERIFIED", "UNCERTAIN"])
        return f"[REVIEW] Result for '{prompt}': {confidence}"


def build_default_router():
    from router import ModelRouter
    router = ModelRouter()
    for worker_cls in [
        PlanningWorker, ArchitectureWorker, CodingWorker,
        GameDevWorker, ResearchWorker, TestingWorker, ReviewWorker
    ]:
        router.register(worker_cls())
    return router
