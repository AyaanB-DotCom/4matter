"""Linux block-device discovery. Parses structured `lsblk --json` output."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

from .models import Drive, Partition, StackNode
from .runner import CommandRunner

COLUMNS = ("NAME,PATH,KNAME,PKNAME,MAJ:MIN,TYPE,SIZE,MODEL,VENDOR,SERIAL,WWN,TRAN,RM,HOTPLUG,"
           "RO,FSTYPE,LABEL,UUID,PTTYPE,LOG-SEC")


class ScanError(Exception):
    pass


def _bool(v: Any) -> bool:
    return v in (True, 1, "1", "true")


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _str(v: Any) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _mounts(node: dict) -> tuple[str, ...]:
    mps = node.get("mountpoints")
    if mps is None:  # util-linux < 2.37 only has a single "mountpoint"
        mps = [node.get("mountpoint")]
    return tuple(m for m in mps if m)


def _collect_stack(node: dict, out: list[StackNode]) -> None:
    for child in node.get("children") or []:
        out.append(StackNode(str(child.get("type")), str(child.get("name")), _mounts(child)))
        _collect_stack(child, out)


def parse_lsblk(data: dict) -> list[Drive]:
    """Turn lsblk JSON into Drive models. Recurses through partitions and stacked devices."""
    drives = []
    for n in data.get("blockdevices", []):
        parts: list[Partition] = []
        stack: list[StackNode] = []
        for c in n.get("children") or []:
            if c.get("type") == "part":
                parts.append(Partition(
                    name=str(c["name"]), path=str(c.get("path") or f"/dev/{c['name']}"),
                    size=_int(c.get("size")), fstype=_str(c.get("fstype")),
                    label=_str(c.get("label")), uuid=_str(c.get("uuid")),
                    mountpoints=_mounts(c), maj_min=_str(c.get("maj:min"))))
                _collect_stack(c, stack)
            else:
                stack.append(StackNode(str(c.get("type")), str(c.get("name")), _mounts(c)))
                _collect_stack(c, stack)
        name = str(n["name"])
        fstypes = {p.fstype for p in parts if p.fstype}
        drives.append(Drive(
            name=name, path=str(n.get("path") or f"/dev/{name}"), maj_min=_str(n.get("maj:min")),
            dtype=str(n.get("type")), size=_int(n.get("size")),
            vendor=_str(n.get("vendor")), model=_str(n.get("model")),
            serial=_str(n.get("serial")), wwn=_str(n.get("wwn")), transport=_str(n.get("tran")),
            removable=_bool(n.get("rm")), hotplug=_bool(n.get("hotplug")),
            read_only=_bool(n.get("ro")), table=_str(n.get("pttype")),
            fstype=_str(n.get("fstype")) or (fstypes.pop() if len(fstypes) == 1 else None),
            label=_str(n.get("label")), sector_size=_int(n.get("log-sec"), 512) or 512,
            mountpoints=_mounts(n), partitions=tuple(parts), stack=tuple(stack)))
    return drives


def usb_info(name: str, sysroot: str = "/") -> str | None:
    """Describe the USB link of /sys/block/<name> by walking up to its usb_device node."""
    try:
        p = Path(sysroot, "sys/block", name, "device").resolve()
    except OSError:
        return None
    for d in (p, *p.parents):
        if (d / "idVendor").exists():
            def rd(f: str) -> str | None:
                try:
                    return (d / f).read_text().strip() or None
                except OSError:
                    return None
            speed = rd("speed")
            gen = {"480": "2.0", "5000": "3.0", "10000": "3.1", "20000": "3.2"}.get(speed or "")
            bits = [f"USB {gen}" if gen else "USB"]
            if speed:
                bits.append(f"{speed} Mbit/s")
            if rd("idVendor") and rd("idProduct"):
                bits.append(f"{rd('idVendor')}:{rd('idProduct')}")
            return " ".join(bits)
        if str(d) == sysroot:
            break
    return None


def by_id_links(name: str, root: str = "/dev/disk/by-id") -> tuple[str, ...]:
    """Stable /dev/disk/by-id names pointing at this whole disk (not its partitions)."""
    found = []
    try:
        for e in sorted(os.scandir(root), key=lambda e: e.name):
            if e.is_symlink() and os.path.basename(os.readlink(e.path)) == name:
                found.append(e.name)
    except OSError:
        pass
    return tuple(found)


def scan_devices(runner: CommandRunner | None = None, *, sysroot: str = "/") -> list[Drive]:
    runner = runner or CommandRunner()
    for cols in (COLUMNS + ",MOUNTPOINTS", COLUMNS + ",MOUNTPOINT"):
        r = runner.run(["lsblk", "--json", "--bytes", "--output", cols])
        if r.returncode == 0:
            break
    else:
        raise ScanError(r.stderr.strip() or "lsblk failed")
    try:
        drives = parse_lsblk(json.loads(r.stdout))
    except (json.JSONDecodeError, KeyError) as e:
        raise ScanError(f"unparseable lsblk output: {e}") from e
    out = []
    for d in drives:
        usb = usb_info(d.name, sysroot) if d.dtype == "disk" else None
        link, instance = sysfs_identity(d.name, sysroot)
        # External = the kernel says USB *and* sysfs shows a real USB device above the disk.
        # RM (the "removable" flag) is deliberately not used: many USB SSDs/HDDs report 0.
        external = d.dtype == "disk" and d.transport == "usb" and usb is not None
        out.append(dataclasses.replace(d, usb=usb, by_id=by_id_links(d.name), external=external,
                                       link=link, instance=instance))
    return out


def sysfs_identity(name: str, sysroot: str = "/") -> tuple[str | None, str | None]:
    """(resolved sysfs device path, diskseq) for a block device. The path embeds the SCSI host
    number, which changes on every re-plug; diskseq is unique per disk instance (kernel 5.15+)."""
    base = Path(sysroot, "sys/class/block", name)
    try:
        link = str(base.resolve())
    except OSError:
        link = None
    try:
        seq = (base / "diskseq").read_text().strip() or None
    except OSError:
        seq = None
    return (link if link and Path(link).exists() else None), seq


def is_partition_node(path: str, sysroot: str = "/") -> bool:
    """True if /dev/<name> is a partition (sysfs marks partitions with a `partition` file)."""
    return Path(sysroot, "sys/class/block", os.path.basename(path), "partition").exists()


CRITICAL_MOUNTS = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/home", "/etc", "/opt", "/nix",
                   "/srv", "/root", "/boot/firmware")


def _sys_devno(devno: str, sysroot: str) -> Path | None:
    p = Path(sysroot, "sys/dev/block", devno)
    try:
        return p.resolve() if p.exists() else None
    except OSError:
        return None


def _expand_devno(devno: str, sysroot: str, seen: set[str]) -> None:
    """Add devno plus its parent disk and every underlying (slave) device, recursively, so a
    device-mapper / md / partition in use protects the whole physical disk beneath it."""
    if devno in seen:
        return
    seen.add(devno)
    base = _sys_devno(devno, sysroot)
    if base is None:
        return
    if (base / "partition").exists():
        try:
            _expand_devno((base.parent / "dev").read_text().strip(), sysroot, seen)
        except OSError:
            pass
    slaves = base / "slaves"
    if slaves.is_dir():
        for s in slaves.iterdir():
            try:
                _expand_devno((s / "dev").read_text().strip(), sysroot, seen)
            except OSError:
                pass


def system_protected(sysroot: str = "/", proc: str = "/proc") -> dict[str, str]:
    """Devices the running system uses, from /proc/self/mountinfo and /proc/swaps.

    Keys are "maj:min" strings and /dev paths; values say why. Partitions and stacked devices are
    expanded to the physical disks underneath them. An unreadable /proc yields a marker entry that
    makes the caller fail closed (see LinuxBackend.assess).
    """
    out: dict[str, str] = {}
    seeds: dict[str, str] = {}
    try:
        for line in Path(proc, "self/mountinfo").read_text().splitlines():
            left, _, right = line.partition(" - ")
            f, r = left.split(), right.split()
            devno, mp = f[2], f[4].replace("\\040", " ")
            if mp in CRITICAL_MOUNTS or mp.startswith(("/boot/", "/usr/", "/var/")):
                seeds[devno] = f"holds the system filesystem mounted at {mp}"
                if len(r) >= 2 and r[1].startswith("/dev/"):
                    out[os.path.realpath(r[1])] = seeds[devno]
    except (OSError, IndexError):
        return {"*": "the system's mount table could not be read"}
    try:
        for line in Path(proc, "swaps").read_text().splitlines()[1:]:
            dev = line.split()[0]
            if dev.startswith("/dev/"):
                out[os.path.realpath(dev)] = "is in use as swap"
                try:
                    st = os.stat(dev)
                    seeds[f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"] = "is in use as swap"
                except OSError:
                    pass
    except OSError:
        pass
    for devno, why in seeds.items():
        seen: set[str] = set()
        _expand_devno(devno, sysroot, seen)
        for d in seen:
            out.setdefault(d, why)
    return out


def root_devno() -> str | None:
    """major:minor of the filesystem backing `/` (None for anonymous devices like btrfs)."""
    st = os.stat("/").st_dev
    return f"{os.major(st)}:{os.minor(st)}" if os.major(st) else None
