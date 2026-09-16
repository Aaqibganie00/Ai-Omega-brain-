"""
TOOL SYSTEM — IMPLEMENTATIONS
--------------------------------
The actual operations. Every file-touching tool is sandboxed to a
configured base_dir — path traversal outside it is rejected in code,
not left to the model to "know better". run_command additionally
classifies commands for NETWORK / DESTRUCTIVE permission requirements
so the permission gate (not a prompt) decides whether it's allowed.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .schemas import Permission

DEFAULT_TIMEOUT_SECONDS = 30

# Command substrings that mark a shell command as needing NETWORK permission.
_NETWORK_PATTERNS = [
    "curl", "wget", "ssh", "scp", "ftp", "pip install", "npm install",
    "npm ci", "git clone", "git pull", "git push", "git fetch",
]

# Command substrings that mark a shell command as DESTRUCTIVE.
_DESTRUCTIVE_PATTERNS = [
    "rm -rf", "rm -r", "sudo", "mkfs", "dd if=", "> /dev", "shutdown",
    "reboot", ":(){:|:&};:", "chmod -r", "chown -r", "drop table",
    "truncate -s 0",
]


class SandboxViolation(Exception):
    pass


def _resolve_in_sandbox(base_dir: str, relative_path: str) -> Path:
    """Resolves relative_path against base_dir and raises if the result
    escapes base_dir (path traversal via '..', symlinks, or absolute
    paths pointing elsewhere)."""
    base = Path(base_dir).resolve()
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise SandboxViolation(
            f"Path '{relative_path}' resolves outside the sandbox root '{base_dir}'"
        )
    return candidate


def classify_command(command: str) -> set:
    """Returns the set of permissions a shell command needs beyond EXECUTE."""
    needed = set()
    lower = command.lower()
    if any(p in lower for p in _NETWORK_PATTERNS):
        needed.add(Permission.NETWORK)
    if any(p in lower for p in _DESTRUCTIVE_PATTERNS):
        needed.add(Permission.DESTRUCTIVE)
    return needed


class ToolImplementations:
    """All tools are sandboxed to base_dir. This is a hard boundary enforced
    in _resolve_in_sandbox, not a convention the caller has to respect."""

    def __init__(self, base_dir: str):
        self.base_dir = str(Path(base_dir).resolve())
        Path(self.base_dir).mkdir(parents=True, exist_ok=True)

    # ---- READ tools ----

    def read_file(self, path: str, max_bytes: int = 200_000) -> dict:
        target = _resolve_in_sandbox(self.base_dir, path)
        if not target.exists():
            raise FileNotFoundError(f"No such file: {path}")
        if not target.is_file():
            raise IsADirectoryError(f"Not a file: {path}")
        data = target.read_bytes()[:max_bytes]
        return {
            "path": path,
            "content": data.decode("utf-8", errors="replace"),
            "truncated": target.stat().st_size > max_bytes,
            "size_bytes": target.stat().st_size,
        }

    def list_files(self, path: str = ".", recursive: bool = False) -> dict:
        target = _resolve_in_sandbox(self.base_dir, path)
        if not target.exists():
            raise FileNotFoundError(f"No such path: {path}")
        if not target.is_dir():
            raise NotADirectoryError(f"Not a directory: {path}")

        entries = []
        if recursive:
            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".git")]
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), self.base_dir)
                    entries.append(rel)
        else:
            for entry in sorted(target.iterdir()):
                entries.append(str(entry.relative_to(self.base_dir)) + ("/" if entry.is_dir() else ""))
        return {"path": path, "entries": entries}

    def search_files(self, pattern: str, path: str = ".", max_results: int = 100) -> dict:
        target = _resolve_in_sandbox(self.base_dir, path)
        if not target.exists():
            raise FileNotFoundError(f"No such path: {path}")

        regex = re.compile(pattern)
        matches = []
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".git")]
            for fname in files:
                fpath = Path(root) / fname
                try:
                    text = fpath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        matches.append({
                            "file": str(fpath.relative_to(self.base_dir)),
                            "line": lineno,
                            "text": line.strip()[:200],
                        })
                        if len(matches) >= max_results:
                            return {"pattern": pattern, "matches": matches, "truncated": True}
        return {"pattern": pattern, "matches": matches, "truncated": False}

    def inspect_project(self, path: str = ".") -> dict:
        target = _resolve_in_sandbox(self.base_dir, path)
        if not target.exists():
            raise FileNotFoundError(f"No such path: {path}")

        file_count = 0
        dir_count = 0
        by_extension = {}
        total_bytes = 0
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".git")]
            dir_count += len(dirs)
            for f in files:
                file_count += 1
                fpath = Path(root) / f
                ext = fpath.suffix or "(none)"
                by_extension[ext] = by_extension.get(ext, 0) + 1
                try:
                    total_bytes += fpath.stat().st_size
                except OSError:
                    pass

        entry_points = []
        for candidate in ("main.py", "app.py", "index.js", "package.json", "requirements.txt", "pyproject.toml"):
            if (target / candidate).exists():
                entry_points.append(candidate)

        return {
            "path": path,
            "file_count": file_count,
            "directory_count": dir_count,
            "total_bytes": total_bytes,
            "by_extension": by_extension,
            "detected_entry_points": entry_points,
        }

    # ---- WRITE tools ----

    def write_file(self, path: str, content: str, overwrite: bool = True) -> dict:
        target = _resolve_in_sandbox(self.base_dir, path)
        existed = target.exists()
        if existed and not overwrite:
            raise FileExistsError(f"File already exists and overwrite=False: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": path, "bytes_written": len(content.encode("utf-8")), "overwritten": existed}

    def edit_file(self, path: str, old_str: str, new_str: str) -> dict:
        target = _resolve_in_sandbox(self.base_dir, path)
        if not target.exists():
            raise FileNotFoundError(f"No such file: {path}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old_str)
        if count == 0:
            raise ValueError(f"old_str not found in {path}")
        if count > 1:
            raise ValueError(f"old_str is not unique in {path} ({count} occurrences)")
        target.write_text(text.replace(old_str, new_str, 1), encoding="utf-8")
        return {"path": path, "replaced": True}

    def create_directory(self, path: str) -> dict:
        target = _resolve_in_sandbox(self.base_dir, path)
        created = not target.exists()
        target.mkdir(parents=True, exist_ok=True)
        return {"path": path, "created": created}

    def create_project(self, path: str, files: dict) -> dict:
        """Convenience WRITE tool: creates a directory and multiple files
        in one call (used by workers scaffolding a new project)."""
        target = _resolve_in_sandbox(self.base_dir, path)
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for rel_path, content in files.items():
            full_rel = os.path.join(path, rel_path)
            self.write_file(full_rel, content, overwrite=True)
            written.append(full_rel)
        return {"path": path, "files_written": written}

    # ---- EXECUTE tools ----

    def run_command(self, command: str, cwd: str = ".", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
        target_cwd = _resolve_in_sandbox(self.base_dir, cwd)
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(target_cwd),
                capture_output=True, text=True, timeout=timeout,
            )
            return {
                "command": command,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-10_000:],
                "stderr": proc.stderr[-10_000:],
            }
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Command exceeded {timeout}s timeout: {command}")

    def run_tests(self, path: str = ".", test_command: Optional[str] = None, timeout: int = 60) -> dict:
        """NOTE: default runner is stdlib unittest (no pip install required).
        unittest discover only finds unittest.TestCase subclasses in
        test_*.py files - plain pytest-style `def test_x(): assert ...`
        functions will NOT be discovered under the default. If pytest is
        available in the target environment, pass
        test_command='python3 -m pytest -q' explicitly."""
        target_cwd = _resolve_in_sandbox(self.base_dir, path)
        # Stale-bytecode immunity: the default runner starts Python with -B,
        # so a same-mtime/same-size source edit between test runs (e.g. a
        # repair edit executed in the same filesystem second) can never cause
        # the test subprocess to import a stale .pyc. Custom test_command
        # values are still executed verbatim by design.
        cmd = test_command or "python3 -B -m unittest discover -s . -p 'test_*.py' -v"
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(target_cwd),
                capture_output=True, text=True, timeout=timeout,
            )
            return {
                "command": cmd,
                "returncode": proc.returncode,
                "passed": proc.returncode == 0,
                "stdout": proc.stdout[-10_000:],
                "stderr": proc.stderr[-10_000:],
            }
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Test run exceeded {timeout}s timeout: {cmd}")
