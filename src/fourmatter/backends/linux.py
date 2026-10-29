"""Linux backend: lsblk / sfdisk / wipefs / mkfs.* (the original 4matter engine)."""

from __future__ import annotations

from ..core import devices
from ..core.filesystems import (FS, LINUX_FS, Availability, availability, missing_core_tools,
                                prerequisite_report, CORE_TOOLS)
from ..core.formatting import build_plan
from ..core.partitioning import validate_config
from ..core.safety import assess
from .base import OSBackend


class LinuxBackend(OSBackend):
    name = "linux"
    display = "Linux"
    unmount_tool = "umount"

    def __init__(self) -> None:
        self._root: str | None | bool = False  # False = not computed yet

    def scan(self, runner):
        return devices.scan_devices(runner)

    def listed(self, drive):
        return drive.external  # verified external USB only; internal disks are never listed

    def root_dev(self):
        if self._root is False:
            self._root = devices.root_devno()
        return self._root

    def assess(self, drive):
        # Re-read the system's own mount table and swap list every time: nothing is cached.
        return assess(drive, root_dev=self.root_dev(), protected=devices.system_protected())

    def core_tools(self):
        return CORE_TOOLS

    def missing_core_tools(self):
        return missing_core_tools()

    def fs_order(self):
        return ("exfat", "fat32", "ntfs", "ext4", "btrfs")

    def fs_support(self) -> dict[str, Availability]:
        return {k: availability(k) for k in FS}

    def validate_config(self, size, fs, table, label):
        errs = validate_config(size, fs, table, label)
        if fs in FS and fs not in LINUX_FS:
            errs.append(f"{FS[fs].name} cannot be created on Linux")
        return errs

    def build_plan(self, drive, req, safety, *, fs_ok=None, core_missing=None):
        return build_plan(drive, req, safety, tools_ok=fs_ok, core_missing=core_missing)

    def run_operation(self, req, emit, cancel, *, runner, scan=None, check_node=True):
        from ..core.executor import run_operation
        scan = scan or (lambda: self.scan(runner))
        return run_operation(req, emit, cancel, runner=runner, scan=scan, assess_fn=self.assess,
                             check_blockdev=check_node)

    def prerequisite_report(self):
        return prerequisite_report()
