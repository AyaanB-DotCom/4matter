"""The interface the UI and the privileged helper use; one implementation per host OS."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable

from ..core.filesystems import Availability
from ..core.models import Drive, OperationRequest, OperationResult, Plan
from ..core.runner import CommandRunner
from ..core.safety import Safety

Emit = Callable[[dict], None]


class OSBackend(ABC):
    name: str = ""          # "linux" | "macos"
    display: str = ""       # "Linux" | "macOS"
    unmount_tool: str = ""  # shown in docs/errors

    @abstractmethod
    def scan(self, runner: CommandRunner) -> list[Drive]:
        """Discover disks, their partitions and mount state."""

    @abstractmethod
    def listed(self, drive: Drive) -> bool:
        """Show this drive in the default registry view (USB / external)?"""

    @abstractmethod
    def assess(self, drive: Drive) -> Safety:
        """Is this a positively identified, eligible target? Fails closed."""

    @abstractmethod
    def core_tools(self) -> tuple[str, ...]:
        """Executables every operation needs."""

    @abstractmethod
    def missing_core_tools(self) -> list[str]: ...

    @abstractmethod
    def fs_order(self) -> tuple[str, ...]:
        """Filesystems offered (in order) on this OS, including ones shown as unavailable."""

    @abstractmethod
    def fs_support(self) -> dict[str, Availability]:
        """Availability (and the reason/hint when not available) of every known filesystem."""

    @abstractmethod
    def validate_config(self, size: int, fs: str | None, table: str, label: str) -> list[str]:
        """Reject impossible filesystem / partition-scheme / label combinations."""

    @abstractmethod
    def build_plan(self, drive: Drive, req: OperationRequest, safety: Safety, *,
                   fs_ok: dict[str, bool] | None = None,
                   core_missing: list[str] | None = None) -> Plan:
        """The ordered steps (unmount, partition, format, ...) for a request."""

    @abstractmethod
    def run_operation(self, req: OperationRequest, emit: Emit, cancel: threading.Event, *,
                      runner: CommandRunner, scan: Callable[[], list[Drive]] | None = None,
                      check_node: bool = True) -> OperationResult:
        """Re-validate, unmount, zero-fill and/or format, verify. Emits structured events."""

    @abstractmethod
    def prerequisite_report(self) -> tuple[list[str], bool]: ...
