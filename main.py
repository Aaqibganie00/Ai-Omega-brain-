"""
MAIN - Omega Brain entry point.

Usage:
  python main.py "Build me a Python calculator app with add subtract multiply divide and tests"
        -> REAL user request
        -> GeminiProvider-backed requirement intake (validated, fail-closed)
        -> existing ExecutionSession pipeline (PlannedOrchestrator -> Orchestrator
           -> real tools -> tests -> build/run -> repair -> verification -> QualityGate)

  python main.py
        -> reads ONE request line from stdin, then behaves exactly as above.

  python main.py benchmark
        -> ORIGINAL demo kept verbatim: 3 hardcoded objectives through the
           legacy OmegaCore prototype lane (unchanged behavior).

Honesty rules: without a working Gemini configuration (GEMINI_API_KEY) the
request path fails with a clear message and exit code != 0. It never falls
back to a mock provider, never fabricates requirements, and never routes a
user request through the legacy prototype lane.
"""

import os
import re
import sys
import uuid


def run_benchmark():
    """VERBATIM legacy demo (moved, behavior unchanged)."""
    from omega_core import OmegaCore

    requests_ = [
        "Create a simple web application with a backend and frontend",
        "Create a small 3D game prototype with a player controller",
        "Analyze the tradeoffs of microservices vs monolith and give a verified report",
    ]

    summary = []
    for req in requests_:
        core = OmegaCore(project_memory_path=f"memory_{abs(hash(req)) % 1000}.json")
        result = core.handle_request(req)
        summary.append({"request": req, "status": result["status"]})

    print("\n\n=== BENCHMARK SUMMARY ===")
    for s in summary:
        print(f"[{s['status']}] {s['request']}")


# ---------------------------------------------------------------------------
# REAL REQUEST LANE (Phase 2: user text -> validated intake -> ExecutionSession)
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 24) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:max_len].strip("-") or "request")


def run_request(request_text: str) -> int:
    """Bring one natural-language request into the REAL execution pipeline.

    Returns a process exit code: 0 on COMPLETED, 1 when the pipeline finished
    but did not complete, 2 for intake/provider (configuration) failures.
    Nothing is ever faked: provider errors and intake errors are reported as-is.
    """
    from providers.gemini_config import GeminiConfig
    from providers.gemini_provider import GeminiProvider
    from providers.errors import ProviderError
    from planning.requirement_intake import extract_and_validate, IntakeError

    print("=" * 64)
    print("OMEGA BRAIN - real request mode")
    print("=" * 64)
    print(f"request: {request_text.strip()}")

    # 1) requirement intake through Gemini (fail-closed; typed provider errors)
    try:
        provider = GeminiProvider(GeminiConfig())
        requirements = extract_and_validate(request_text, provider)
    except IntakeError as e:
        print(f"\nREQUEST NOT UNDERSTOOD (intake validation failed): {e}")
        return 2
    except ProviderError as e:
        print(f"\nTRUSTED INTAKE UNAVAILABLE ({type(e).__name__}): {e}")
        print("No fallback was used. Configure GEMINI_API_KEY for real intake.")
        return 2

    # validated model data (never executed; printed for transparency)
    print("\nvalidated requirements:")
    print(f"  summary        : {requirements.summary}")
    print(f"  deliverable    : {requirements.deliverable_type} (language={requirements.language})")
    print(f"  features       : {', '.join(requirements.features)}")
    if requirements.constraints:
        print(f"  constraints    : {', '.join(requirements.constraints)}")
    print(f"  objective      : {requirements.normalized_objective!r}  (constructed in code)")

    # 2) isolated per-request workspace (existing ToolRegistry sandbox governs it)
    from tools import ToolRegistry
    from memory import ProjectMemory
    from execution.session import ExecutionSession
    from execution.schemas import SessionState

    workspace = os.path.abspath(os.path.join(
        os.getcwd(), "workspace", f"{_slugify(requirements.summary)}-{uuid.uuid4().hex[:8]}"))
    os.makedirs(workspace, exist_ok=False)
    registry = ToolRegistry(base_dir=workspace)
    memory = ProjectMemory(path=os.path.join(workspace, "project_memory.json"))

    # 3) REAL execution lane: ExecutionSession -> learning -> PlannedOrchestrator
    #    -> Orchestrator -> workers/tools -> tests -> build/run -> repair -> verify -> gate
    print(f"\nworkspace: {workspace}")
    print("executing through ExecutionSession (existing pipeline)...")
    try:
        session = ExecutionSession(provider=provider, tool_registry=registry, project_memory=memory)
        result = session.run(requirements.normalized_objective)
    except ProviderError as e:
        print(f"\nEXECUTION STOPPED BY PROVIDER ERROR ({type(e).__name__}): {e}")
        return 2
    except Exception as e:  # session records its own errors as well; still report honestly
        print(f"\nEXECUTION ERROR ({type(e).__name__}): {e}")
        return 1

    completed = result.state == SessionState.COMPLETED.value
    plan_summary = (result.plan_summary or {})

    print("\n" + "=" * 64)
    print("RESULT")
    print("=" * 64)
    print(f"  state          : {result.state}")
    print(f"  quality gate   : {result.quality_decision}")
    print(f"  steps          : {plan_summary.get('step_count')}")
    print(f"  artifact dir   : {workspace}")
    files = sorted(f for f in os.listdir(workspace) if os.path.isfile(os.path.join(workspace, f)))
    print(f"  files          : {', '.join(files) if files else '(none)'}")
    if result.errors:
        print(f"  errors         : {'; '.join(list(result.errors)[:3])}")
    print(f"  checkpoint     : {result.checkpoint_id}")
    if completed:
        print("\nFINAL: COMPLETED AND GATE-APPROVED (real files in the artifact dir).")
        return 0
    print(f"\nFINAL: pipeline ended honestly as {result.state} (gate={result.quality_decision}).")
    return 1


def _read_stdin_request() -> str:
    """Read ONE request from stdin. TTY: prompt for a line. Piped: take all stdin."""
    if sys.stdin.isatty():
        try:
            return input("Enter your build request: ")
        except EOFError:
            return ""
    return sys.stdin.read().strip()


def main(argv) -> int:
    args = [a for a in argv[1:]]
    if args and args[0].lower() == "benchmark":
        run_benchmark()
        return 0

    if args:
        request_text = " ".join(args)
    else:
        request_text = _read_stdin_request()

    request_text = (request_text or "").strip()
    if not request_text:
        print("usage:", file=sys.stderr)
        print('  python main.py "Build me a Python calculator app with tests"   # real request', file=sys.stderr)
        print("  python main.py benchmark                                          # legacy demo", file=sys.stderr)
        print("  python main.py                                                    # stdin request", file=sys.stderr)
        return 2

    return run_request(request_text)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
