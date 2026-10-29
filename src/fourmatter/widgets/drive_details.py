"""Shared rendering of drive facts, used by the dashboard panel and the inspect screen."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from ..core.models import Drive, human_size
from ..core.safety import Safety, assess
from ..theme import c

FS_NAMES = {"vfat": "FAT32", "exfat": "exFAT", "ntfs": "NTFS", "ext4": "ext4", "btrfs": "Btrfs",
            "crypto_LUKS": "LUKS", "swap": "swap", "ext3": "ext3", "ext2": "ext2", "xfs": "XFS",
            "iso9660": "ISO9660", "LVM2_member": "LVM", "apfs": "APFS", "hfsplus": "Mac OS Extended",
            "fat16": "FAT16", "fat12": "FAT12", "fat": "FAT"}


def fs_name(fs: str | None) -> str:
    return FS_NAMES.get(fs, fs) if fs else "None"


def unknown(v: str | None) -> str:
    return v if v else "Unknown"


def layout_summary(d: Drive) -> str:
    if not d.partitions:
        return "No partitions" if not d.fstype else f"{fs_name(d.fstype)} (no partition table)"
    return f"{len(d.partitions)} partition{'s' if len(d.partitions) != 1 else ''}"


def status_tag(d: Drive, s: Safety) -> Text:
    if not s.eligible:
        return Text("PROTECTED" if s.protected else "UNAVAILABLE", c("rust"))
    if s.to_unmount:
        return Text("MOUNTED", c("amber"))
    return Text("READY", c("moss"))


def layout_bar(d: Drive, width: int = 36) -> list[Text]:
    """Proportional ▕███▒▒░░▏ diagram of the partition layout, plus a legend."""
    glyphs = "█▓▒░"
    cells: list[tuple[str, str]] = []
    total = max(d.size, 1)
    used = 0
    for i, p in enumerate(d.partitions):
        n = max(1, round(width * p.size / total))
        cells.append((glyphs[i % 4] * n, c("teal" if i % 2 == 0 else "moss")))
        used += n
    if used < width:
        cells.append(("·" * (width - used), c("dim")))
    bar = Text("▕", c("slate"))
    for s, style in cells:
        bar.append(s[:width], style)
    bar.append("▏", c("slate"))
    if not d.partitions:
        bar = Text("▕" + "·" * width + "▏", c("dim"))
    lines = [bar]
    for i, p in enumerate(d.partitions):
        mp = f"  @ {', '.join(p.mountpoints)}" if p.mountpoints else ""
        lines.append(Text(f" {glyphs[i % 4]} {p.name}  {fs_name(p.fstype)}  {human_size(p.size)}"
                          + (f'  "{p.label}"' if p.label else "") + mp, c("bone")))
    return lines


def describe(d: Drive, s: Safety | None = None, *, full: bool = False) -> Table:
    s = s or assess(d)
    t = Table.grid(padding=(0, 1))
    t.add_column(style=c("slate"), no_wrap=True)
    t.add_column(overflow="fold")

    def row(k: str, v, style: str = "") -> None:
        t.add_row(k, v if isinstance(v, Text) else Text(str(v), style or c("bone")))

    row("Device", f"{d.path}  ({'simulated' if d.simulated else d.dtype})")
    row("Model", unknown(d.model))
    row("Vendor", unknown(d.vendor))
    row("Capacity", f"{human_size(d.size)}  ({d.size:,} bytes)" if d.size else "Unknown")
    row("Filesystem", fs_name(d.fstype) if (d.fstype or d.partitions) else "None")
    row("Table", {"gpt": "GPT", "dos": "MBR", None: "None"}.get(d.table, d.table or "Unknown"))
    row("Layout", layout_summary(d))
    mounts = [m.mountpoint for m in d.mounts]
    row("Mounted", ", ".join(mounts) if mounts else "No", c("amber") if mounts else c("bone"))
    ident = d.serial or d.wwn
    row("Serial/ID", unknown(ident))
    row("Transport", (d.transport or "Unknown").upper() + (f"  {d.usb}" if d.usb else ""))
    row("Read-only", "Yes" if d.read_only else "No", c("rust") if d.read_only else c("bone"))
    if full:
        row("WWN", unknown(d.wwn))
        row("Sector size", f"{d.sector_size} bytes")
        row("Removable", "Yes" if d.removable else "No")
        for i, link in enumerate(d.by_id):
            row("by-id" if i == 0 else "", link)
        if not d.by_id:
            row("by-id", "Unknown")
    if s.eligible:
        tag = "Yes, eligible" + (" (will unmount first)" if s.to_unmount else "")
        row("Safe to modify", Text(tag, c("moss")))
    else:
        row("Safe to modify", Text("NO - PROTECTED" if s.protected else "NO - UNAVAILABLE",
                                   c("rust", "bold")))
        for b in s.blockers:
            row("", Text("· " + b, c("rust")))
    return t


class DriveDetails(Static):
    def show(self, d: Drive | None, s: Safety | None = None) -> None:
        if d is None:
            self.update(Text("No device selected.", c("slate")))
        else:
            self.update(describe(d, s))
