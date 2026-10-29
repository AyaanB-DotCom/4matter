"""The only place subprocesses are started. Always argument arrays, never a shell."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import NamedTuple

SEARCH_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")


class ToolMissing(Exception):
    def __init__(self, tool: str):
        super().__init__(f"required tool not found: {tool}")
        self.tool = tool


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def find_tool(name: str) -> str | None:
    """Like shutil.which, but also looks in sbin dirs that unprivileged PATHs often omit."""
    if "/" in name:
        return name if os.access(name, os.X_OK) else None
    return shutil.which(name) or shutil.which(name, path=os.pathsep.join(SEARCH_DIRS))


class CommandRunner:
    """Runs real commands. `mutating=True` marks anything that can change a disk."""

    dry_run = False

    def run(self, argv: list[str], *, mutating: bool = False, input: str | None = None,
            timeout: float | None = 600, token=None) -> CommandResult:
        if mutating:  # no token, wrong target or disallowed tool => nothing is executed
            from .guard import check_argv
            check_argv(token, argv)
        exe = find_tool(argv[0])
        if exe is None:
            raise ToolMissing(argv[0])
        env = {"PATH": os.pathsep.join(SEARCH_DIRS), "LC_ALL": "C", "LANG": "C"}
        try:
            p = subprocess.run([exe, *argv[1:]], input=input, capture_output=True, text=True,
                               timeout=timeout, env=env, shell=False, check=False)
        except subprocess.TimeoutExpired as e:
            return CommandResult(124, "", f"timed out after {e.timeout}s: {argv[0]}")
        return CommandResult(p.returncode, p.stdout, p.stderr)


class DryRunRunner(CommandRunner):
    """Executes read-only commands for real; records (never runs) mutating ones."""

    dry_run = True

    def __init__(self) -> None:
        self.planned: list[list[str]] = []

    def run(self, argv, *, mutating=False, input=None, timeout=600, token=None):
        if mutating:
            from .guard import check_argv
            check_argv(token, argv)  # dry-run validates exactly like a real run, then only records
            self.planned.append(list(argv))
            return CommandResult(0, "", "")
        return super().run(argv, mutating=False, input=input, timeout=timeout)
