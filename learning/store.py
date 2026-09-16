"""
LEARNING LAYER — EXPERIENCE STORE
----------------------------------------
Built on the EXISTING ProjectMemory (memory.py, unmodified) - experiences
are just memory entries under an "experience:" key prefix, using the same
remember()/recall()/all() interface every prior phase already uses. No
second database.
"""

from dataclasses import asdict


class ExperienceStore:
    def __init__(self, project_memory):
        self.project_memory = project_memory

    def save(self, record):
        self.project_memory.remember(
            key=f"experience:{record.experience_id}", value=asdict(record),
            verified=(record.evidence_confidence == "HIGH"), source=record.source,
        )

    def get(self, experience_id):
        entry = self.project_memory.recall(f"experience:{experience_id}")
        return entry["value"] if entry else None

    def all_experiences(self):
        out = []
        for key, entry in self.project_memory.all().items():
            if key.startswith("experience:"):
                out.append(entry["value"])
        return out

    def search(self, task_type=None, success=None, tool=None, worker=None, min_confidence=None):
        confidence_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        results = []
        for value in self.all_experiences():
            if task_type is not None and value.get("task_type") != task_type:
                continue
            if success is not None:
                is_success = value.get("quality_gate_result") == "APPROVED"
                if is_success != success:
                    continue
            if tool is not None and tool not in (value.get("tools_used") or []):
                continue
            if worker is not None and worker not in (value.get("workers_used") or []):
                continue
            if min_confidence is not None:
                if confidence_rank.get(value.get("evidence_confidence", "LOW"), 0) < confidence_rank.get(min_confidence, 0):
                    continue
            results.append(value)
        return results

    def recent(self, n=10):
        return sorted(self.all_experiences(), key=lambda v: v.get("timestamp", 0), reverse=True)[:n]
