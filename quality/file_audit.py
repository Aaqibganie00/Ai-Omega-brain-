"""
QUALITY CONTROL LAYER — FILE-CHANGE AUDIT
-----------------------------------------------
Snapshots the whole project (read-only) before/after and classifies every
file as created/modified/deleted/unchanged.
"""

from tools import ToolCall
from .schemas import FileChangeAudit


class FileChangeAuditor:
    def snapshot(self, tool_registry) -> dict:
        """Read-only: every file operation goes through the registry's
        permission/audit pipeline (ToolCall -> execute()), never impl directly.
        Unreadable/denied files are skipped, exactly as unreadable files were
        skipped by the previous exception-based implementation."""
        listing = tool_registry.execute(ToolCall(
            tool_name="list_files", arguments={"path": ".", "recursive": True},
            requested_by="quality.file_audit",
        ))
        files = {}
        if not listing.ok:
            return files
        for path in listing.output["entries"]:
            read = tool_registry.execute(ToolCall(
                tool_name="read_file", arguments={"path": path},
                requested_by="quality.file_audit",
            ))
            if read.ok:
                files[path] = read.output["content"]
        return files

    def audit(self, before: dict, after: dict) -> FileChangeAudit:
        before_keys, after_keys = set(before), set(after)
        created = sorted(after_keys - before_keys)
        deleted = sorted(before_keys - after_keys)
        common = before_keys & after_keys
        modified = sorted(p for p in common if before[p] != after[p])
        unchanged = sorted(p for p in common if before[p] == after[p])
        return FileChangeAudit(created=created, modified=modified, deleted=deleted, unchanged=unchanged)
