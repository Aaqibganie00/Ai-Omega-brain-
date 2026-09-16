"""
OMEGA CORE
----------
Central coordinator. Receives a request, plans it, routes each task to the
right worker, verifies output, consults the AI Heart, recovers from errors,
and reports a final status - never silently pretending success.
"""

from task_planner import RuleBasedPlanner
from workers import build_default_router
from memory import WorkingMemory, ProjectMemory, ExecutionMemory
from verification import VerificationEngine, AIHeart, HeartConstraints
from error_recovery import ErrorRecoveryEngine


class OmegaCore:
    def __init__(self, project_memory_path: str = "project_memory.json"):
        self.planner = RuleBasedPlanner()
        self.router = build_default_router()
        self.working_memory = WorkingMemory()
        self.project_memory = ProjectMemory(project_memory_path)
        self.execution_memory = ExecutionMemory()
        self.verifier = VerificationEngine()
        self.heart = AIHeart(HeartConstraints(max_retries_per_task=2))
        self.recovery = ErrorRecoveryEngine(max_retries=2)

    def handle_request(self, request: str, verbose: bool = True) -> dict:
        if verbose:
            print(f"\n=== UNDERSTANDING REQUEST ===\n'{request}'")

        graph = self.planner.plan(request)

        if verbose:
            print("\n=== PLAN (execution batches - tasks in the same batch run in parallel) ===")
            for i, batch in enumerate(graph.execution_order_preview(), 1):
                print(f"  Batch {i}: {batch}")

        results = {}

        while not graph.is_complete() and not graph.has_failed():
            batch = graph.ready_batch()
            if not batch:
                break

            for task in batch:
                graph.mark(task.id, "running")
                if verbose:
                    print(f"\n--- Task '{task.id}' ({task.worker_type}): {task.description} ---")

                outcome = self._execute_with_recovery(task, graph, verbose)
                results[task.id] = outcome
                # Only a genuinely FAILED verification blocks the graph.
                # UNCERTAIN/PARTIALLY_VERIFIED still "complete" the task but
                # get surfaced to the user as needing attention (see task_results).
                graph.mark(task.id, "failed" if outcome["status"] == "FAILED" else "done")

        final_status = "COMPLETED" if graph.is_complete() else "FAILED"
        if verbose:
            print(f"\n=== FINAL STATUS: {final_status} ===")

        return {
            "status": final_status,
            "task_results": results,
            "execution_log": self.execution_memory.log,
        }

    def _execute_with_recovery(self, task, graph, verbose) -> dict:
        retries = 0
        while True:
            heart_eval = self.heart.evaluate(task.worker_type, retries)
            if not heart_eval["approved"]:
                self.execution_memory.record(task.id, "heart_check", "failure", "; ".join(heart_eval["concerns"]))
                return {"status": "FAILED", "detail": "; ".join(heart_eval["concerns"])}

            try:
                extra_kwargs = {}
                # feed generated code into the testing worker for real verification
                if task.worker_type == "testing_worker" and "coding" in str(graph.tasks):
                    pass  # kept simple for MVP; see main.py demo for explicit wiring

                backend_name, output = self.router.run(task.worker_type, task.description, **extra_kwargs)

                if task.worker_type == "coding_worker":
                    verdict = self.verifier.verify_code(output)
                else:
                    verdict = self.verifier.verify_text_output(output)

                self.working_memory.set(task.id, output, verified=(verdict.verdict == "CONFIRMED"), source=backend_name)

                if verdict.verdict == "FAILED":
                    self.execution_memory.record(task.id, f"run via {backend_name}", "failure", verdict.detail)
                    decision = self.recovery.decide(task.id, self.execution_memory, self.router, task.worker_type)
                    if verbose:
                        print(f"    verification FAILED ({verdict.detail}) -> recovery: {decision.action} ({decision.reason})")
                    if decision.action == "escalate":
                        return {"status": "FAILED", "detail": f"escalated: {decision.reason}"}
                    retries += 1
                    continue

                self.execution_memory.record(task.id, f"run via {backend_name}", "success", verdict.detail)
                if verbose:
                    print(f"    -> {backend_name}: {verdict.verdict} ({verdict.detail})")
                    print(f"    output: {output[:120]}{'...' if len(output) > 120 else ''}")

                return {"status": verdict.verdict, "output": output, "backend": backend_name}

            except RuntimeError as e:
                self.execution_memory.record(task.id, "route", "failure", str(e))
                return {"status": "FAILED", "detail": str(e)}

    # ------------------------------------------------------------------
    # AGENTIC (Claude + Tools) INTEGRATION — Phase 3
    #
    # Additive only: does not touch handle_request/_execute_with_recovery
    # above. Wires the existing planner/router-adjacent pieces to the new
    # agent.session.ToolUseSession without rewriting Omega Core.
    #
    #     OmegaCore.execute_agentic_task()
    #         -> agent.session.ToolUseSession   (model <-> tool loop)
    #         -> tools.registry.ToolRegistry    (existing, unmodified)
    #         -> self.project_memory            (existing, unmodified)
    # ------------------------------------------------------------------

    def execute_agentic_task(self, objective: str, provider, tool_registry,
                              allowed_tools: set = None, worker: str = "coding_agent_worker",
                              task_id: str = None, max_tool_turns: int = None) -> dict:
        """Runs objective through the real model+tool loop and records a
        structured summary in project memory. Returns the SessionResult as
        a dict. Caller supplies the provider (Claude or Mock) and an
        already-constructed ToolRegistry - Omega Core does not construct
        either, keeping this method a thin integration point."""
        from agent.coding_worker import CodingAgentWorker, DEFAULT_MAX_TOOL_TURNS
        from agent.session import ToolUseSession

        if worker == "coding_agent_worker":
            agent_worker = CodingAgentWorker(
                provider=provider, tool_registry=tool_registry,
                allowed_tools=allowed_tools,
                max_tool_turns=max_tool_turns or DEFAULT_MAX_TOOL_TURNS,
            )
            result = agent_worker.execute_task(objective, task_id=task_id)
        else:
            session = ToolUseSession(
                provider=provider, tool_registry=tool_registry,
                allowed_tools=allowed_tools or set(tool_registry.available_tools()),
                max_tool_turns=max_tool_turns or 6, worker_id=worker,
            )
            result = session.run(objective, task_id=task_id)

        self._record_agent_session_memory(result)
        return {
            "task_id": result.task_id,
            "status": result.status,
            "final_response": result.final_response,
            "turns_used": result.turns_used,
            "tool_calls_made": result.tool_calls_made,
            "events": result.events,
            "error": result.error,
        }

    def _record_agent_session_memory(self, result):
        """Stores a structured, non-sensitive summary — never raw messages,
        never API keys/credentials — in the existing ProjectMemory."""
        files_changed = sorted({
            call["arguments"].get("path")
            for call in result.tool_calls_made
            if call["success"] and call["tool_name"] in ("write_file", "edit_file", "create_project")
            and call["arguments"].get("path")
        })
        errors = [
            {"tool": call["tool_name"]} for call in result.tool_calls_made if not call["success"]
        ]
        summary = {
            "task_id": result.task_id,
            "tools_used": sorted({c["tool_name"] for c in result.tool_calls_made}),
            "files_changed": files_changed,
            "execution_status": result.status,
            "errors": errors,
            "final_result": (result.final_response or "")[:500],
            "turns_used": result.turns_used,
        }
        self.project_memory.remember(
            key=f"agent_session:{result.task_id}",
            value=summary,
            verified=(result.status == "COMPLETED"),
            source="agent_session",
        )
