"""Host OS detection and backend selection."""

from __future__ import annotations

import platform


class UnsupportedPlatform(Exception):
    pass


def host_os(system: str | None = None) -> str:
    """'linux', 'macos', or the raw lower-cased system name for anything else."""
    s = (system or platform.system()).lower()
    return {"linux": "linux", "darwin": "macos"}.get(s, s)


def get_backend(name: str | None = None):
    """The OSBackend for `name` (default: this host). Raises UnsupportedPlatform otherwise."""
    name = name or host_os()
    if name == "linux":
        from ..backends.linux import LinuxBackend
        return LinuxBackend()
    if name == "macos":
        from ..backends.macos import MacOSBackend
        return MacOSBackend()
    raise UnsupportedPlatform(
        f"4matter supports Linux and macOS; this system reports '{platform.system() or 'unknown'}'. "
        "Use `4matter --demo` to try the interface.")
