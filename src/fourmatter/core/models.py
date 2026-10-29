"""Plain data models shared by the TUI, the backends and the privileged helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FILESYSTEMS = ("fat32", "exfat", "ntfs", "ext4", "btrfs", "apfs", "hfsplus")
MODES = ("format", "zero", "zero_format")
TABLES = ("gpt", "mbr")


@dataclass(frozen=True)
class Mount:
    source: str
    mountpoint: str


@dataclass(frozen=True)
class Partition:
    name: str
    path: str
    size: int
    fstype: str | None = None
    label: str | None = None
    uuid: str | None = None
    mountpoints: tuple[str, ...] = ()
    maj_min: str | None = None
    role: str | None = None  # e.g. "efi" for a firmware partition the OS created
    # Volumes that must be unmounted individually (macOS APFS: the container's volumes,
    # not the partition that stores it). When empty, `mountpoints` are mounts of `path`.
    volumes: tuple[Mount, ...] = ()


@dataclass(frozen=True)
class StackNode:
    """A device stacked on top of the disk (LUKS, LVM, RAID, ...)."""

    type: str
    name: str
    mountpoints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Drive:
    name: str
    path: str
    maj_min: str | None
    dtype: str
    size: int
    vendor: str | None = None
    model: str | None = None
    serial: str | None = None
    wwn: str | None = None
    transport: str | None = None
    removable: bool = False
    hotplug: bool = False
    read_only: bool = False
    table: str | None = None
    fstype: str | None = None
    label: str | None = None
    sector_size: int = 512
    mountpoints: tuple[str, ...] = ()
    partitions: tuple[Partition, ...] = ()
    stack: tuple[StackNode, ...] = ()
    usb: str | None = None
    by_id: tuple[str, ...] = ()
    simulated: bool = False
    # Positive identification as external physical storage, set ONLY by a backend scanner that
    # verified it (Linux: lsblk says usb AND sysfs shows a USB device ancestor; macOS: diskutil says
    # external, USB, physical). Defaults to False: unverified means ineligible.
    external: bool = False
    link: str | None = None      # physical topology evidence (Linux: resolved sysfs path)
    instance: str | None = None  # per-connection id (Linux: diskseq; macOS: IOMedia registry id)
    # Reasons a platform backend has already found that make this disk off-limits
    # (running system, Time Machine destination, internal, ...). Merged by safety.assess.
    blockers: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.vendor, self.model) if p]
        return " ".join(parts) if parts else "Unknown device"

    @property
    def mounts(self) -> tuple[Mount, ...]:
        out = [Mount(self.path, mp) for mp in self.mountpoints]
        for p in self.partitions:
            out += list(p.volumes) if p.volumes else [Mount(p.path, mp) for mp in p.mountpoints]
        for s in self.stack:
            out += [Mount(s.name, mp) for mp in s.mountpoints]
        return tuple(out)


@dataclass(frozen=True)
class Identity:
    """What we remember about a drive at selection time. Paths alone are not trusted."""

    path: str
    maj_min: str | None
    size: int
    vendor: str | None
    model: str | None
    serial: str | None
    wwn: str | None
    transport: str | None
    link: str | None = None
    instance: str | None = None

    @classmethod
    def of(cls, d: Drive) -> Identity:
        return cls(d.path, d.maj_min, d.size, d.vendor, d.model, d.serial, d.wwn, d.transport,
                   d.link, d.instance)

    def short(self) -> str:
        return self.path.removeprefix("/dev/")


@dataclass
class FormatOptions:
    fs: str | None = "exfat"
    label: str = ""
    table: str = "gpt"
    zero_fill: bool = False


@dataclass
class OperationRequest:
    mode: str
    identity: Identity
    options: FormatOptions
    owner: tuple[int, int] | None = None  # uid, gid that should own an ext4/btrfs root

    @property
    def does_format(self) -> bool:
        return self.mode in ("format", "zero_format")

    @property
    def does_zero(self) -> bool:
        return self.mode in ("zero", "zero_format")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "identity": self.identity.__dict__,
            "options": self.options.__dict__,
            "owner": list(self.owner) if self.owner else None,
        }

    @classmethod
    def from_dict(cls, d: Any) -> OperationRequest:
        """Strictly parse an untrusted request (the helper reads it from stdin)."""
        try:
            mode = d["mode"]
            i, o = d["identity"], d["options"]
            ident = Identity(
                path=_s(i["path"]), maj_min=_opt_s(i.get("maj_min")), size=_i(i["size"]),
                vendor=_opt_s(i.get("vendor")), model=_opt_s(i.get("model")),
                serial=_opt_s(i.get("serial")), wwn=_opt_s(i.get("wwn")),
                transport=_opt_s(i.get("transport")), link=_opt_s(i.get("link")),
                instance=_opt_s(i.get("instance")),
            )
            opts = FormatOptions(
                fs=_opt_s(o.get("fs")), label=_s(o.get("label", "")),
                table=_s(o.get("table", "gpt")), zero_fill=_b(o.get("zero_fill", False)),
            )
            owner = d.get("owner")
            if owner is not None:
                if len(owner) != 2:
                    raise ValueError("owner")
                owner = (_i(owner[0]), _i(owner[1]))
                if not all(0 <= x < 2**31 for x in owner):
                    raise ValueError("owner range")
        except (KeyError, TypeError, AttributeError) as e:
            raise ValueError(f"malformed request: {e!r}") from e
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if opts.table not in TABLES:
            raise ValueError(f"unknown partition table {opts.table!r}")
        if opts.fs is not None and opts.fs not in FILESYSTEMS:
            raise ValueError(f"unknown filesystem {opts.fs!r}")
        return cls(mode, ident, opts, owner)


def _s(v: Any) -> str:
    if not isinstance(v, str):
        raise TypeError(f"expected str, got {type(v).__name__}")
    return v


def _opt_s(v: Any) -> str | None:
    return None if v is None else _s(v)


def _i(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"expected int, got {type(v).__name__}")
    return v


def _b(v: Any) -> bool:
    if not isinstance(v, bool):
        raise TypeError("expected bool")
    return v


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    argv: tuple[str, ...] | None = None  # display argv; may hold PARTITION_PLACEHOLDER
    destructive: bool = False


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class OperationResult:
    ok: bool
    message: str
    details: str = ""
    state: str = ""  # plain-English description of what state the drive is in
    verified: dict[str, str] = field(default_factory=dict)
    cancelled: bool = False
    simulated: bool = False


class SafetyError(Exception):
    """The target failed (re)validation. Fails closed."""


class OperationError(Exception):
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.message, self.details = message, details


# --- formatting helpers ----------------------------------------------------------------

def human_size(n: int | float, binary: bool = False) -> str:
    base, units = (1024, ("B", "KiB", "MiB", "GiB", "TiB", "PiB")) if binary else (
        1000, ("B", "KB", "MB", "GB", "TB", "PB"))
    x = float(n)
    for u in units:
        if x < base or u == units[-1]:
            return f"{int(x)} B" if u == "B" else f"{x:.1f} {u}"
        x /= base
    raise AssertionError


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--:--"
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_rate(bps: float | None) -> str:
    return "-- MB/s" if bps is None else f"{human_size(bps)}/s"
