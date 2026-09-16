"""
ORCHESTRATION LAYER — WORKER REGISTRY
--------------------------------------------
Registers WorkerDefinitions and supports discovery by capability. Worker
selection anywhere else in the codebase goes through this registry -
never a hard-coded if/elif chain picking worker types.
"""


class WorkerRegistry:
    def __init__(self):
        self._workers = {}

    def register(self, definition):
        self._workers[definition.worker_type] = definition

    def get(self, worker_type):
        return self._workers.get(worker_type)

    def find_by_capability(self, capability):
        return [w for w in self._workers.values() if capability in w.capabilities]

    def all_worker_types(self):
        return sorted(self._workers.keys())

    def check_permissions(self, worker_type, permission_manager):
        """Real check against the SAME tools.PermissionManager the
        ToolRegistry itself uses - not a separate/duplicate concept."""
        definition = self.get(worker_type)
        if definition is None:
            return False, set()
        return permission_manager.check(definition.required_permissions)
