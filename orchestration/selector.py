"""
ORCHESTRATION LAYER — WORKER SELECTOR
-----------------------------------------
Selects a worker_type for a subtask based on required_capabilities,
checked against the WorkerRegistry (never a hard-coded mapping) and the
tool system's own PermissionManager.
"""


class WorkerSelector:
    def __init__(self, worker_registry):
        self.worker_registry = worker_registry

    def select(self, subtask, permission_manager=None):
        candidates = []
        for capability in subtask.required_capabilities:
            candidates.extend(self.worker_registry.find_by_capability(capability))

        if not candidates:
            return None, f"no worker registered for capabilities {subtask.required_capabilities}"

        for definition in candidates:
            if permission_manager is not None:
                allowed, missing = permission_manager.check(definition.required_permissions)
                if not allowed:
                    continue
            return definition.worker_type, "matched"

        return None, "candidate worker(s) found but permissions insufficient"
