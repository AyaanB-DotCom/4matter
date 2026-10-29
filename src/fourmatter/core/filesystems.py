"""Filesystem catalogue, label rules, tool detection and mkfs argument construction."""

from __future__ import annotations

from dataclasses import dataclass

from .runner import CommandRunner, find_tool

PLACEHOLDER = "<new-partition>"


@dataclass(frozen=True)
class FsInfo:
    key: str
    name: str
    tool: str
    package: str  # human install hint
    summary: str
    max_file: str
    label_max: int
    lsblk_type: str  # what blkid/lsblk reports once formatted


FS: dict[str, FsInfo] = {f.key: f for f in (
    FsInfo("fat32", "FAT32", "mkfs.vfat", "dosfstools",
           "Broad legacy compatibility (consoles, cameras, car stereos, firmware tools). "
           "Maximum single file size is 4 GiB minus one byte.",
           "4 GiB - 1 byte", 11, "vfat"),
    FsInfo("exfat", "exFAT", "mkfs.exfat", "exfatprogs",
           "Modern cross-platform storage with large files. Windows, macOS and Linux read and "
           "write it. No journaling and no Unix permissions.", "16 EiB (effectively unlimited)", 11,
           "exfat"),
    FsInfo("ntfs", "NTFS", "mkfs.ntfs", "ntfs-3g (Fedora: ntfsprogs)",
           "Windows-oriented storage with journaling and permissions. Linux needs the kernel "
           "ntfs3 driver or ntfs-3g to write; macOS can only read it.",
           "16 EiB (effectively unlimited)", 128, "ntfs"),
    FsInfo("ext4", "ext4", "mkfs.ext4", "e2fsprogs",
           "Native Linux filesystem with journaling and Unix permissions. Not readable by "
           "Windows or macOS without extra drivers.", "16 TiB", 16, "ext4"),
    FsInfo("btrfs", "Btrfs", "mkfs.btrfs", "btrfs-progs",
           "Optional Linux filesystem with checksums and snapshots. Not readable by Windows or "
           "macOS without extra drivers.", "16 EiB", 255, "btrfs"),
    FsInfo("apfs", "APFS", "diskutil", "built into macOS",
           "Apple's modern filesystem. Best for drives used only with Macs. Windows and Linux "
           "cannot read it without third-party drivers.", "8 EiB", 255, "apfs"),
    FsInfo("hfsplus", "Mac OS Extended", "diskutil", "built into macOS",
           "Journaled HFS+, the pre-APFS Mac format. Needed for old Macs and some Time Machine "
           "setups. Linux can read it with the hfsplus driver; Windows cannot.", "8 EiB", 255,
           "hfsplus"),
)}

LINUX_FS = ("fat32", "exfat", "ntfs", "ext4", "btrfs")
MAC_FS = ("fat32", "exfat", "hfsplus", "apfs")

CORE_TOOLS = ("lsblk", "sfdisk", "wipefs", "udevadm", "umount", "sync")
INSTALL_LINES = (
    "Debian/Ubuntu: sudo apt install {pkg}",
    "Fedora:        sudo dnf install {pkg}",
    "Arch:          sudo pacman -S {pkg}",
)
DISTRO_PKG = {  # per-distro package names where they differ from FsInfo.package
    "ntfs": ("ntfs-3g", "ntfsprogs", "ntfs-3g"),
}


@dataclass(frozen=True)
class Availability:
    ok: bool
    path: str | None
    hint: str


def availability(fs: str) -> Availability:
    """Linux availability: is the mkfs tool installed? (macOS uses MacOSBackend.fs_support.)"""
    info = FS[fs]
    if fs not in LINUX_FS:
        return Availability(False, None, f"{info.name} cannot be created on Linux (no native tools).")
    path = find_tool(info.tool)
    pk = DISTRO_PKG.get(fs, (info.package,) * 3)
    hint = "\n".join(line.format(pkg=p) for line, p in zip(INSTALL_LINES, pk))
    return Availability(path is not None, path, f"{info.tool} not found. Install {info.package}:\n{hint}")


def missing_core_tools() -> list[str]:
    return [t for t in CORE_TOOLS if find_tool(t) is None]


def prerequisite_report() -> tuple[list[str], bool]:
    """Human-readable tool report; second value is True when the core tools are all present."""
    lines = ["Core tools (util-linux / systemd):"]
    core_missing = missing_core_tools()
    for t in CORE_TOOLS:
        lines.append(f"  [{'ok' if t not in core_missing else '--'}] {t}")
    lines.append("Filesystem formatters:")
    for k in LINUX_FS:
        f, a = FS[k], availability(k)
        lines.append(f"  [{'ok' if a.ok else '--'}] {f.name:<6} {f.tool}" +
                     ("" if a.ok else f"   (install: {f.package})"))
    return lines, not core_missing


# --- labels ---------------------------------------------------------------------------

_FAT_BAD = set('"*+,./:;<=>?[\\]|') | {chr(c) for c in range(32)} | {chr(127)}


def normalize_label(fs: str, label: str) -> str:
    return label.upper() if fs == "fat32" else label


def validate_label(fs: str, label: str) -> str | None:
    """Return an error message, or None when the label is valid for this filesystem."""
    if fs not in FS:
        return f"Unknown filesystem '{fs}'"
    if any(ord(c) < 32 or ord(c) == 127 for c in label):
        return "Label contains control characters"
    info = FS[fs]
    if fs in ("ext4", "btrfs", "apfs"):
        n = len(label.encode("utf-8"))
        unit = "bytes"
    else:
        n, unit = len(label), "characters"
    if n > info.label_max:
        return f"{info.name} labels are limited to {info.label_max} {unit} (got {n})"
    if fs == "fat32":
        bad = sorted({c for c in label if c in _FAT_BAD})
        if bad:
            return "FAT32 labels cannot contain: " + " ".join(bad)
        if label != label.strip():
            return "FAT32 labels cannot start or end with spaces"
    if fs in ("ntfs", "exfat") and label.startswith(("-",)):
        return "Label cannot start with '-'"
    if fs in ("apfs", "hfsplus") and any(ch in label for ch in ":/"):
        return "Volume names on macOS filesystems cannot contain ':' or '/'"
    return None


# --- mkfs argv -----------------------------------------------------------------------

def exfat_label_flag(version_output: str) -> str:
    return "-L" if "exfatprogs" in version_output.lower() else "-n"


def mkfs_argv(fs: str, device: str, label: str, *, owner: tuple[int, int] | None = None,
              exfat_flag: str = "-L") -> list[str]:
    label = normalize_label(fs, label)
    if fs == "fat32":
        argv = ["mkfs.vfat", "-F", "32"] + (["-n", label] if label else []) + [device]
    elif fs == "exfat":
        argv = ["mkfs.exfat"] + ([exfat_flag, label] if label else []) + [device]
    elif fs == "ntfs":
        argv = ["mkfs.ntfs", "-Q"] + (["-L", label] if label else []) + [device]
    elif fs == "ext4":
        argv = ["mkfs.ext4", "-F", "-q", "-m", "0"]
        if owner and owner[0] > 0:
            argv += ["-E", f"root_owner={owner[0]}:{owner[1]}"]
        argv += (["-L", label] if label else []) + [device]
    elif fs == "btrfs":
        argv = ["mkfs.btrfs", "-f"] + (["-L", label] if label else []) + [device]
    else:
        raise ValueError(f"unsupported filesystem {fs!r}")
    return argv


def detect_exfat_flag(runner: CommandRunner) -> str:
    if runner.dry_run:  # dry-run never executes a mkfs binary, not even for --version
        return "-L"
    try:
        r = runner.run(["mkfs.exfat", "-V"], timeout=10)
        return exfat_label_flag(r.stdout + r.stderr)
    except Exception:
        return "-L"
