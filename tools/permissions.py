"""
TOOL SYSTEM — PERMISSIONS
---------------------------
Real, code-level enforcement. A tool call is rejected here BEFORE any
execution happens if the required permission isn't granted. This is not
a suggestion to a model - it's a hard gate the registry calls on every
single invocation.
"""

from dataclasses import dataclass, field
from .schemas import Permission


@dataclass
class PermissionConfig:
    """What's actually granted for this run. Configurable per deployment -
    e.g. a CI sandbox might grant READ+WRITE+EXECUTE but never DESTRUCTIVE
    or NETWORK."""
    granted: set = field(default_factory=lambda: {Permission.READ, Permission.WRITE, Permission.EXECUTE})

    def grant(self, permission: Permission):
        self.granted.add(permission)

    def revoke(self, permission: Permission):
        self.granted.discard(permission)

    def has(self, permission: Permission) -> bool:
        return permission in self.granted


class PermissionDenied(Exception):
    def __init__(self, missing: set):
        self.missing = missing
        super().__init__(f"Missing permissions: {sorted(p.value for p in missing)}")


class PermissionManager:
    def __init__(self, config: PermissionConfig = None):
        self.config = config or PermissionConfig()

    def check(self, required: set) -> tuple[bool, set]:
        """Returns (allowed, missing_permissions). Never raises - caller
        decides how to report the denial."""
        missing = {p for p in required if not self.config.has(p)}
        return (len(missing) == 0, missing)

    def enforce(self, required: set):
        """Raises PermissionDenied if anything is missing. Used internally
        by the registry so a denial can never be silently skipped."""
        allowed, missing = self.check(required)
        if not allowed:
            raise PermissionDenied(missing)
