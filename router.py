"""
MODEL / AGENT ROUTER
---------------------
Provider-agnostic abstraction. Real backends (OpenAI, Anthropic, local
models, etc.) implement `ModelBackend`. The router picks a backend per
task based on declared capability, and tracks rolling performance so
future routing decisions improve over time.

No hard-coded "you must have N models" - add/remove backends freely.
"""

import time
from dataclasses import dataclass, field
from typing import Protocol, Optional


class ModelBackend(Protocol):
    name: str
    capabilities: set[str]

    def run(self, prompt: str, **kwargs) -> str: ...


@dataclass
class PerformanceRecord:
    successes: int = 0
    failures: int = 0
    total_latency: float = 0.0
    calls: int = 0

    @property
    def success_rate(self) -> float:
        return self.successes / self.calls if self.calls else 0.5  # neutral prior

    @property
    def avg_latency(self) -> float:
        return self.total_latency / self.calls if self.calls else 0.0


class ModelRouter:
    def __init__(self):
        self.backends: dict[str, ModelBackend] = {}
        self.performance: dict[str, PerformanceRecord] = {}

    def register(self, backend: ModelBackend):
        self.backends[backend.name] = backend
        self.performance[backend.name] = PerformanceRecord()

    def candidates_for(self, required_capability: str) -> list[str]:
        return [
            name for name, b in self.backends.items()
            if required_capability in b.capabilities
        ]

    def select(self, required_capability: str) -> Optional[str]:
        """
        Pick the best backend for a capability using:
          score = success_rate  -  latency_penalty
        Backends with no track record yet get a neutral prior so they're
        still eligible (exploration vs exploitation, kept deliberately simple).
        """
        candidates = self.candidates_for(required_capability)
        if not candidates:
            return None

        def score(name):
            perf = self.performance[name]
            latency_penalty = min(perf.avg_latency / 10.0, 0.3)
            return perf.success_rate - latency_penalty

        return max(candidates, key=score)

    def run(self, required_capability: str, prompt: str, **kwargs) -> tuple[str, str]:
        """Returns (backend_name, output). Raises if nothing can handle it."""
        backend_name = self.select(required_capability)
        if backend_name is None:
            raise RuntimeError(f"No registered backend supports capability '{required_capability}'")

        backend = self.backends[backend_name]
        perf = self.performance[backend_name]

        start = time.time()
        try:
            output = backend.run(prompt, **kwargs)
            perf.successes += 1
            return backend_name, output
        except Exception as e:
            perf.failures += 1
            raise
        finally:
            perf.calls += 1
            perf.total_latency += (time.time() - start)
