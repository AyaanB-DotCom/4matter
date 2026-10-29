"""Privileged helper. Runs as root, reads ONE JSON request on stdin and re-validates
everything itself (it trusts nothing from the UI), then streams JSON events on stdout.

stdin after the request: a line 'cancel' (or EOF) requests cancellation.
Launch: python -I -m fourmatter.privilege.helper   (via sudo, or directly as root)
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable

from ..core.models import OperationRequest, OperationResult

Emit = Callable[[dict], None]


def main() -> int:
    def emit(ev: dict) -> None:
        sys.stdout.write(json.dumps(ev) + "\n")
        sys.stdout.flush()

    def fail(msg: str, code: int) -> int:
        emit({"event": "result", **OperationResult(False, msg, msg).__dict__})
        return code

    if os.geteuid() != 0:
        return fail("Insufficient privileges: the helper must run as root.", 3)
    try:
        req = OperationRequest.from_dict(json.loads(sys.stdin.readline()))
    except (ValueError, json.JSONDecodeError) as e:
        return fail(f"Rejected malformed request: {e}", 4)

    from ..core.platform import UnsupportedPlatform, get_backend
    from ..core.runner import CommandRunner

    try:
        osb = get_backend()  # chosen from the host OS, never from the request
    except UnsupportedPlatform as e:
        return fail(str(e), 5)

    cancel = threading.Event()

    def watch_stdin() -> None:
        for line in sys.stdin:
            if line.strip() == "cancel":
                break
        cancel.set()  # explicit cancel, or EOF because the UI went away

    threading.Thread(target=watch_stdin, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: cancel.set())
    res = osb.run_operation(req, emit, cancel, runner=CommandRunner())
    return 0 if res.ok else 1


# --- UI side ---------------------------------------------------------------------------

def helper_argv(euid: int | None = None, sudo: str | None = None) -> list[str] | None:
    """Command line that starts the helper with root rights, or None if impossible."""
    base = [sys.executable, "-I", "-m", "fourmatter.privilege.helper"]
    euid = os.geteuid() if euid is None else euid
    if euid == 0:
        return base
    sudo = sudo or shutil.which("sudo")
    return [sudo, "-n", "--", *base] if sudo else None


def have_privileges() -> bool:
    """True if the helper can start without prompting (root, or sudo credentials cached)."""
    if os.geteuid() == 0:
        return True
    sudo = shutil.which("sudo")
    return bool(sudo and subprocess.run([sudo, "-n", "true"], capture_output=True).returncode == 0)


def acquire_privileges() -> bool:
    """Prompt for the sudo password on the real terminal. Call inside App.suspend()."""
    sudo = shutil.which("sudo")
    return bool(sudo and subprocess.run([sudo, "-v"]).returncode == 0)


def run_via_helper(req: OperationRequest, emit: Emit, cancel: threading.Event) -> OperationResult:
    argv = helper_argv()
    if argv is None:
        return OperationResult(False, "Insufficient privileges: sudo is not installed. "
                               "Run 4matter as root or install sudo.")
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert p.stdin and p.stdout
    p.stdin.write(json.dumps(req.to_dict()) + "\n")
    p.stdin.flush()
    done = threading.Event()

    def forward_cancel() -> None:
        while not done.is_set():
            if cancel.wait(0.2):
                try:
                    p.stdin.write("cancel\n")
                    p.stdin.flush()
                except OSError:
                    pass
                return

    threading.Thread(target=forward_cancel, daemon=True).start()
    result: OperationResult | None = None
    other: list[str] = []
    for line in p.stdout:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            other.append(line.rstrip())
            emit({"event": "log", "line": line.rstrip()})
            continue
        if ev.get("event") == "result":
            result = OperationResult(**{k: v for k, v in ev.items() if k != "event"})
        emit(ev)
    code = p.wait()
    done.set()
    if result is None:
        text = "\n".join(other)
        if "password is required" in text or "a terminal is required" in text:
            msg = "Insufficient privileges: sudo needs your password. Retry and authenticate."
        else:
            msg = f"The privileged helper exited unexpectedly (status {code})."
        result = OperationResult(False, msg, text, "Unknown: check the drive before trusting it.")
        emit({"event": "result", **result.__dict__})
    return result


if __name__ == "__main__":
    raise SystemExit(main())
