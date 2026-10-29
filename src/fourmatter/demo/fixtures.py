"""Simulated drives and a simulated executor for `4matter --demo`.

Nothing here opens a device or starts a process. Every event it emits is marked simulated.
"""

from __future__ import annotations

import dataclasses
import threading
import time

from ..core.executor import Backend, Emit
from ..core.filesystems import FS, PLACEHOLDER
from ..core.models import (Drive, OperationRequest, OperationResult, Partition, StackNode,
                           human_size)
from ..core import guard
from ..core.safety import assess

GB = 1000**3


def _drive(name, vendor, model, size_gb, *, serial=None, fs=None, label=None, table="gpt",
           mounts=(), usb="USB 3.0 5000 Mbit/s", parts=None, devno=None, **kw) -> Drive:
    maj, minor = devno or ({"sda": (8, 0), "sdb": (8, 16), "sdc": (8, 32), "sdd": (8, 48),
                            "sde": (8, 64)}[name])
    size = int(size_gb * GB)
    if parts is None:
        parts = [Partition(f"{name}1", f"/dev/{name}1", size - 1024**2, fs, label,
                           f"DEMO-{name}-0001", tuple(mounts), f"{maj}:{minor + 1}")] if fs else []
    return Drive(name=name, path=f"/dev/{name}", maj_min=f"{maj}:{minor}", dtype="disk", size=size,
                 vendor=vendor, model=model, serial=serial, transport=kw.pop("transport", "usb"),
                 removable=True, hotplug=True, table=table if parts else None,
                 fstype=fs, label=label, partitions=tuple(parts), usb=usb,
                 by_id=(f"usb-{(vendor or 'X')}_{model.replace(' ', '_')}_{serial}-0:0",) if serial else (),
                 simulated=True, external=kw.pop("external", True), **kw)


def demo_drives(os_name: str = "linux") -> list[Drive]:
    if os_name == "macos":
        return _mac_drives()
    return [
        _drive("sdb", "SanDisk", "Ultra", 64, serial="4C530001230912", fs="exfat", label="TRAVEL"),
        _drive("sdc", "Kingston", "DataTraveler 3.0", 32, serial="0019E06B4F12", fs="vfat",
               label="KINGSTON", table="dos", usb="USB 3.0 5000 Mbit/s",
               mounts=("/run/media/demo/KINGSTON",)),
        _drive("sdd", "Lexar", "JumpDrive S75", 128, serial="AA00000000000489", usb="USB 3.1 10000 Mbit/s",
               parts=[Partition("sdd1", "/dev/sdd1", 100 * GB, "ext4", "backup", "DEMO-sdd1", (), "8:49"),
                      Partition("sdd2", "/dev/sdd2", 27 * GB, "ntfs", "scratch", "DEMO-sdd2", (), "8:50")],
               fs=None, label=None),
        _drive("sde", "Generic", "Flash Disk (write-protected)", 16, serial="0000000000A1",
               fs="vfat", label="LOCKED", table="dos", usb="USB 2.0 480 Mbit/s", read_only=True),
        _drive("sdf", "Samsung", "Portable SSD T7 (booted system)", 500, serial="S6XXNS0T4412", fs=None,
               label=None, devno=(8, 80), mounts=(),
               parts=[Partition("sdf1", "/dev/sdf1", 499 * GB, "ext4", "root", "DEMO-sdf1", ("/",), "8:81")]),
        dataclasses.replace(
            _drive("sda", "Samsung", "SSD 980 (simulated system disk)", 500, serial="S64DNX0T123456",
                   transport="nvme", usb=None, external=False,
                   parts=[Partition("sda1", "/dev/sda1", 512 * 1000**2, "vfat", "EFI", "DEMO", ("/boot/efi",), "8:1"),
                          Partition("sda2", "/dev/sda2", 499 * GB, "ext4", "root", "DEMO2", ("/",), "8:2")],
                   fs=None, label=None),
            transport="sata"),
    ]


def _mac_drives() -> list[Drive]:
    def d(n, *a, **kw):
        return _drive(f"disk{n}", *a, devno=(1, 4 * n), **kw)

    def mpart(n, i, size, fs, label, mount=None, role=None):
        return Partition(f"disk{n}s{i}", f"/dev/disk{n}s{i}", size, fs, label, f"DEMO-{n}-{i}",
                         (mount,) if mount else (), f"1:{4 * n + i}", role=role)

    lexar = [mpart(6, 1, 100 * GB, "apfs", "Projects"), mpart(6, 2, 27 * GB, "exfat", "Shared")]
    return [
        d(4, "SanDisk", "Ultra", 64, serial="4C530001230912", fs="exfat", label="TRAVEL"),
        d(5, "Kingston", "DataTraveler 3.0", 32, serial="0019E06B4F12", fs="vfat", label="KINGSTON",
          table="dos", mounts=("/Volumes/KINGSTON",)),
        d(6, "Lexar", "JumpDrive S75", 128, serial="AA00000000000489", usb="USB 3.1 10000 Mbit/s",
          parts=lexar, fs=None, label=None),
        d(7, "Generic", "Flash Disk (write-protected)", 16, serial="0000000000A1", fs="vfat",
          label="LOCKED", table="dos", usb="USB 2.0 480 Mbit/s", read_only=True),
        d(8, "Samsung", "Portable SSD T7 (bootable macOS)", 500, serial="S6XXNS0T4412",
          parts=[mpart(8, 1, 499 * GB, "apfs", "Macintosh HD - Ext")], fs=None, label=None,
          blockers=("Contains macOS system or backup volumes (Data, Preboot, Recovery, System)",)),
        d(9, "WD", "Elements 2TB (Time Machine)", 2000, serial="57584A3141", fs="hfsplus",
          label="Backups", blockers=("Time Machine destination",)),
        dataclasses.replace(
            d(0, "Apple", "APPLE SSD (simulated startup disk)", 500, serial="0ba0153e", usb=None,
              parts=[mpart(0, 1, 500 * GB, "apfs", "Macintosh HD", "/")], fs=None, label=None,
              external=False, blockers=("Internal disk", "Holds the running macOS system (startup disk)")),
            transport="apple fabric"),
    ]


class DemoBackend(Backend):
    mode = "demo"
    label = "DEMO"
    writes = False

    def __init__(self, sim_seconds: float = 6.0, os_name: str | None = None) -> None:
        from ..backends.base import OSBackend  # noqa: F401
        from ..core.platform import get_backend, host_os
        name = os_name or (host_os() if host_os() in ("linux", "macos") else "linux")
        super().__init__(get_backend(name))
        self.drives = demo_drives(name)
        self.sim_seconds = sim_seconds

    def scan(self) -> list[Drive]:
        return list(self.drives)

    def assess(self, drive: Drive):
        return assess(drive)  # pure: no root-device lookup, no subprocess

    def fs_support(self):
        from ..backends.macos import UNSUPPORTED_REASON
        from ..core.filesystems import LINUX_FS, MAC_FS, Availability, availability
        supported = MAC_FS if self.os.name == "macos" else LINUX_FS
        out = {}
        for k in FS:
            if k in supported:
                out[k] = Availability(True, None, "")
            elif self.os.name == "macos":
                out[k] = Availability(False, None, UNSUPPORTED_REASON.get(k, "Not supported on macOS"))
            else:
                out[k] = availability(k)  # unsupported kinds return before any lookup
        return out

    def core_missing(self) -> list[str]:
        return []

    def build_plan(self, drive, req, safety):
        return self.os.build_plan(drive, req, safety, fs_ok={k: a.ok for k, a in self.fs_support().items()},
                                  core_missing=[])

    def execute(self, req: OperationRequest, emit: Emit, cancel: threading.Event) -> OperationResult:
        return simulate(self, req, emit, cancel, seconds=self.sim_seconds)


def simulate(backend: DemoBackend, req: OperationRequest, emit: Emit, cancel: threading.Event,
             *, seconds: float = 6.0) -> OperationResult:
    def finish(res: OperationResult) -> OperationResult:
        res.simulated = True
        emit({"event": "result", **res.__dict__})
        return res

    try:
        tok, safety = guard.authorize(req.identity, backend.scan, backend.assess, stage="demo")
        drive = tok.drive
    except Exception as e:
        return finish(OperationResult(False, f"Aborted: {e}"))
    plan = backend.build_plan(drive, req, safety)
    if not plan.ok:
        return finish(OperationResult(False, "Cannot proceed: " + plan.errors[0]))
    for i, step in enumerate(plan.steps):
        if cancel.is_set():
            return finish(OperationResult(False, "Simulation cancelled.", cancelled=True))
        emit({"event": "stage", "index": i, "total": len(plan.steps), "key": step.key,
              "title": "[SIMULATED] " + step.title, "determinate": step.key == "zero"})
        if step.argv:
            emit({"event": "log", "line": "SIMULATED $ " + " ".join(step.argv)})
        if step.key == "zero":
            t0 = time.monotonic()
            total = drive.size
            while True:
                if cancel.is_set():
                    return finish(OperationResult(False, "Simulation cancelled.", cancelled=True))
                el = time.monotonic() - t0
                frac = min(1.0, el / seconds)
                written = int(total * frac)
                rate = total / seconds
                emit({"event": "progress", "written": written, "total": total, "elapsed": el,
                      "rate": rate, "eta": (total - written) / rate if el >= 1 else None})
                if frac >= 1.0:
                    break
                time.sleep(0.1)
        else:
            time.sleep(0.4)
    o = req.options
    if req.does_format:
        pt = Partition(f"{drive.name}1", f"{drive.path}1", drive.size - 1024**2,
                       FS[o.fs].lsblk_type, o.label or None, "DEMO-NEW", (), None)
        new = dataclasses.replace(drive, partitions=(pt,), table="gpt" if o.table == "gpt" else "dos",
                                  fstype=pt.fstype, label=pt.label)
        facts = {"device": drive.path, "partition": pt.path, "partition table": new.table,
                 "filesystem": pt.fstype, "label": pt.label or "(none)", "size": str(pt.size)}
        msg = "SIMULATED: formatted and verified. No real disk was touched."
    else:
        new = dataclasses.replace(drive, partitions=(), table=None, fstype=None, label=None)
        facts = {"device": drive.path, "bytes written": f"{drive.size:,}"}
        msg = "SIMULATED: zero-fill complete. No real disk was touched."
    backend.drives = [new if d.path == drive.path else d for d in backend.drives]
    return finish(OperationResult(True, msg, verified=facts))
