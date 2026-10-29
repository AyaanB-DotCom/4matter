"""macOS backend: diskutil (structured plist output), tmutil, ioreg and raw-device zero-fill.

Nothing here parses decorative `diskutil` text: every query uses `-plist` and `plistlib`.
Safety rule of thumb: an external USB disk is eligible only when every check passes;
anything unknown is a blocker.
"""

from __future__ import annotations

import dataclasses
import fcntl
import os
import plistlib
import re
import stat
import struct
import threading
import time
from collections.abc import Callable

from ..core.devices import ScanError
from ..core.filesystems import FS, MAC_FS, Availability, normalize_label, validate_label
from ..core.formatting import STATE_AFTER, explain_failure
from ..core.models import (Drive, Mount, OperationError, OperationRequest, OperationResult,
                           Partition, Plan, SafetyError, Step, human_size)
from ..core.runner import CommandRunner, ToolMissing, find_tool
from ..core import guard
from ..core.guard import UnauthorizedOperation, ValidatedTarget
from ..core.safety import Safety, assess
from ..core.zero_fill import DryRunViolation, ZeroFillError, zero_fill
from .base import Emit, OSBackend

CORE_TOOLS = ("diskutil",)
DEFAULT_LABEL = "UNTITLED"

# diskutil "format" argument (verified: FAT32 yields "MS-DOS FAT32", see verify_result).
ERASE_FORMAT = {"fat32": "FAT32", "exfat": "ExFAT", "hfsplus": "JHFS+", "apfs": "APFS"}
PERSONALITY = {"fat32": "MS-DOS FAT32", "exfat": "ExFAT", "hfsplus": "Journaled HFS+", "apfs": "APFS"}
SCHEME = {"gpt": "GPT", "mbr": "MBR"}
TABLE_NAMES = {"GUID_partition_scheme": "gpt", "FDisk_partition_scheme": "dos",
               "Apple_partition_scheme": "apm"}
EXPECTED_TABLE = {"gpt": "gpt", "mbr": "dos"}
MIN_BYTES = {"fat32": 35 * 10**6, "exfat": 1 << 20, "hfsplus": 4 << 20, "apfs": 9 << 20}
MBR_MAX_BYTES = 2 * 1024**4

SYSTEM_ROLES = {"System", "Data", "Preboot", "Recovery", "VM", "Update", "xART", "Hardware",
                "ISC", "Backup"}
COMPLEX_CONTENT = {"Apple_CoreStorage", "Apple_RAID", "Apple_RAID_Offline", "Apple_Boot",
                   "Apple_APFS_ISC", "Apple_APFS_Recovery"}
UNSUPPORTED_REASON = {
    "ntfs": "macOS cannot create NTFS natively (it can only read it). Use exFAT for a drive shared "
            "with Windows, or format NTFS from Windows or Linux.",
    "ext4": "macOS has no native ext4 support. Use exFAT for a drive shared with Linux, or "
            "format ext4 from Linux.",
    "btrfs": "macOS has no Btrfs support. Format Btrfs from Linux.",
}

DKIOCGETBLOCKSIZE = 0x40046418   # _IOR('d', 24, uint32_t)
DKIOCGETBLOCKCOUNT = 0x40086419  # _IOR('d', 25, uint64_t)
_DISK_NODE = re.compile(r"^/dev/disk\d+$")


# ---- plist helpers (pure) ---------------------------------------------------------------

def whole_of(ident: str) -> str:
    m = re.match(r"(disk\d+)", ident or "")
    return m.group(1) if m else ident


def fstype_from_info(info: dict) -> str | None:
    """Canonical fs name from `diskutil info`. Distinguishes FAT32 from FAT16/FAT12."""
    ft = info.get("FilesystemType")
    if not ft:
        return None
    if ft == "msdos":
        name = str(info.get("FilesystemName", "")).upper()
        return "vfat" if "FAT32" in name else "fat16" if "FAT16" in name else \
            "fat12" if "FAT12" in name else "fat"
    return {"hfs": "hfsplus", "ufsd_NTFS": "ntfs", "cd9660": "iso9660"}.get(ft, ft)


def physical_wholes(info: dict) -> set[str] | None:
    """Physical whole disk(s) behind a volume. None when it cannot be determined."""
    stores = info.get("APFSPhysicalStores")
    if stores:
        out = {whole_of(s.get("APFSPhysicalStore") or s.get("DeviceIdentifier") or "") for s in stores}
        return out - {""} or None
    if info.get("APFSContainerReference"):
        return None
    pw = info.get("ParentWholeDisk")
    return {pw} if pw else None


def parse_usb(ioreg: list | None) -> dict[str, dict]:
    """`ioreg -a -r -c IOUSBHostDevice` -> {bsd_name: {vendor, product, serial, ids}}.

    Walks each USB device's subtree; every node carrying a "BSD Name" is attributed to the
    nearest enclosing USB device. Best effort: missing data simply stays missing.
    """
    out: dict[str, dict] = {}

    def walk(node, dev):
        if not isinstance(node, dict):
            return
        if "idVendor" in node:
            dev = {"vendor": node.get("USB Vendor Name") or node.get("kUSBVendorString"),
                   "product": node.get("USB Product Name") or node.get("kUSBProductString"),
                   "serial": node.get("USB Serial Number") or node.get("kUSBSerialNumberString"),
                   "ids": (node.get("idVendor"), node.get("idProduct"))}
        bsd = node.get("BSD Name")
        if bsd and dev:
            out[bsd] = dev
        for ch in node.get("IORegistryEntryChildren") or []:
            walk(ch, dev)

    for root in ioreg or []:
        walk(root, None)
    return out


def parse_media_ids(ioreg: list | None) -> dict[str, str]:
    """`ioreg -a -r -c IOMedia` -> {whole-disk BSD name: IORegistryEntryID}. The id belongs to the
    IOMedia object, so a disk that is unplugged and plugged in again gets a new one."""
    out: dict[str, str] = {}

    def walk(n):
        if isinstance(n, dict):
            if n.get("Whole") and n.get("BSD Name") and n.get("IORegistryEntryID") is not None:
                out[n["BSD Name"]] = str(n["IORegistryEntryID"])
            for c in n.get("IORegistryEntryChildren") or []:
                walk(c)

    for r in ioreg or []:
        walk(r)
    return out


def usb_label(u: dict, bus: str) -> str | None:
    ids = u.get("ids")
    if ids and None not in ids:
        return f"USB {ids[0]:04x}:{ids[1]:04x}"
    return "USB" if (u or bus == "USB") else None


def _devno(path: str) -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"


def _has_tm_backups(mountpoint: str) -> bool:
    return os.path.isdir(os.path.join(mountpoint, "Backups.backupdb"))


def parse_macos(list_data: dict, infos: dict[str, dict], apfs: dict | None, *,
                startup: set[str] | None, tm: set[str] | None, usb: dict[str, dict] | None = None,
                devno: Callable[[str], str | None] | None = None,
                tm_marker: Callable[[str], bool] | None = None,
                allow_virtual: bool = False,
                media_ids: dict[str, str] | None = None) -> list[Drive]:
    """Build Drive models from `diskutil list -plist` plus per-disk `info` plists."""
    usb = usb or {}
    devno = devno or _devno
    tm_marker = tm_marker or _has_tm_backups
    entries = list_data.get("AllDisksAndPartitions", [])
    containers = {e["DeviceIdentifier"]: e for e in entries if e.get("APFSPhysicalStores")}
    roles: dict[str, dict[str, set[str]]] = {}
    for c in (apfs or {}).get("Containers", []):
        roles[c.get("ContainerReference", "")] = {v.get("DeviceIdentifier", ""): set(v.get("Roles") or [])
                                                  for v in c.get("Volumes", [])}
    drives: list[Drive] = []
    for e in entries:
        if e.get("APFSPhysicalStores"):
            continue  # synthesized APFS container; reached through its physical store
        did = e["DeviceIdentifier"]
        inf = infos.get(did)
        blockers: list[str] = []
        if not inf:
            blockers.append("Cannot read disk information (fails closed)")
            inf = {}
        internal = inf.get("Internal")
        if internal is None:
            blockers.append("Cannot tell whether the disk is internal")
        elif internal or inf.get("OSInternalMedia"):
            blockers.append("Internal disk")
        bus = inf.get("BusProtocol") or ""
        if (bus == "Disk Image" or inf.get("VirtualOrPhysical") == "Virtual") and not allow_virtual:
            blockers.append("Virtual disk (disk image), not physical storage")
        if startup is None:
            blockers.append("Cannot determine the startup disk")
        elif did in startup:
            blockers.append("Holds the running macOS system (startup disk)")
        if tm is not None and did in tm:
            blockers.append("Time Machine destination")
        writable = inf.get("WritableMedia")
        if writable is None:
            blockers.append("Cannot tell whether the disk is writable")
        # Positive identification as external physical storage: every condition must hold.
        external = (internal is False and not inf.get("OSInternalMedia")
                    and bool(inf.get("RemovableMediaOrExternalDevice"))
                    and (bus == "USB" and inf.get("VirtualOrPhysical") != "Virtual"
                         or (allow_virtual and bus == "Disk Image")))
        parts: list[Partition] = []
        for p in e.get("Partitions") or []:
            pid, content = p["DeviceIdentifier"], p.get("Content", "")
            path = f"/dev/{pid}"
            if content in COMPLEX_CONTENT:
                blockers.append(f"Contains {content} (macOS recovery, RAID or CoreStorage volume)")
            if content.startswith("Apple_APFS"):
                cont = next((c for c in containers.values()
                             if any(s["DeviceIdentifier"] == pid for s in c["APFSPhysicalStores"])), None)
                vols = (cont or {}).get("APFSVolumes", [])
                if cont and len(cont["APFSPhysicalStores"]) > 1:
                    blockers.append("APFS container spans several physical disks (Fusion/RAID)")
                role_union = set().union(*(roles.get(cont["DeviceIdentifier"], {}).values())) if cont else set()
                bad = sorted(role_union & SYSTEM_ROLES)
                if bad:
                    blockers.append("Contains macOS system or backup volumes (" + ", ".join(bad) + ")")
                mps = tuple(v["MountPoint"] for v in vols if v.get("MountPoint"))
                names = [v.get("VolumeName") for v in vols if v.get("VolumeName")]
                parts.append(Partition(pid, path, int(p.get("Size", 0)), "apfs", names[0] if names else None,
                                       p.get("DiskUUID"), mps, devno(path),
                                       volumes=tuple(Mount(f"/dev/{v['DeviceIdentifier']}", v["MountPoint"])
                                                     for v in vols if v.get("MountPoint"))))
                if cont is None:
                    blockers.append("APFS partition without a readable container")
                continue
            pinf = infos.get(pid, {})
            mp = (p.get("MountPoint"),) if p.get("MountPoint") else ()
            parts.append(Partition(pid, path, int(p.get("Size", 0)), fstype_from_info(pinf),
                                   p.get("VolumeName") or None, p.get("VolumeUUID"), mp, devno(path),
                                   role="efi" if content == "EFI" else None))
        for part in parts:
            for mp in part.mountpoints:
                if tm_marker(mp):
                    blockers.append(f"Contains Time Machine backups ({mp})")
        path = inf.get("DeviceNode") or f"/dev/{did}"
        u = usb.get(did) or {}
        data_fs = {p.fstype for p in parts if p.fstype and p.role != "efi"}
        mounts = (inf.get("MountPoint"),) if inf.get("MountPoint") and not parts else ()
        drives.append(Drive(
            name=did, path=path, maj_min=devno(path), dtype="disk",
            size=int(inf.get("TotalSize") or inf.get("Size") or e.get("Size") or 0),
            vendor=u.get("vendor") or None,
            model=(u.get("product") or inf.get("MediaName") or inf.get("IORegistryEntryName") or None),
            serial=u.get("serial") or None, wwn=None, transport=bus.lower() or None,
            removable=bool(inf.get("RemovableMediaOrExternalDevice") or inf.get("Removable")),
            hotplug=bool(inf.get("Ejectable")), read_only=writable is False,
            table=TABLE_NAMES.get(e.get("Content") or inf.get("Content") or ""),
            fstype=(fstype_from_info(inf) if not parts else (data_fs.pop() if len(data_fs) == 1 else None)),
            label=(inf.get("VolumeName") or None) if not parts else None,
            sector_size=int(inf.get("DeviceBlockSize") or 512), mountpoints=mounts,
            partitions=tuple(parts),
            usb=usb_label(u, bus), external=external,
            link=f"{bus}:{inf.get('IORegistryEntryName') or ''}" if inf else None,
            instance=(media_ids or {}).get(did),
            blockers=tuple(dict.fromkeys(blockers))))
    return drives


# ---- raw-device zero-fill primitives ----------------------------------------------------------

def open_raw_disk(path: str, *, dry_run: bool = False) -> int:
    """Open /dev/rdiskN (the unbuffered raw node) for exclusive writing."""
    if dry_run:
        raise DryRunViolation(f"refusing to open {path} for writing in dry-run mode")
    if not _DISK_NODE.match(path):
        raise ZeroFillError(f"refusing unexpected device path {path!r}", 0)
    flags = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_EXLOCK", 0)
    return os.open("/dev/r" + path[5:], flags)


def check_mac_fd(fd: int, token: ValidatedTarget) -> None:
    """The opened raw node must be a character device whose unit number is the validated disk's."""
    st = os.fstat(fd)
    if not stat.S_ISCHR(st.st_mode):
        raise UnauthorizedOperation("the opened path is not a raw disk device")
    want = (token.drive.maj_min or ":").split(":")[-1]
    if str(os.minor(st.st_rdev)) != want:
        raise UnauthorizedOperation("the opened device is not the validated disk")


def raw_disk_size(fd: int) -> int:
    """Capacity from the block-device ioctls (lseek(SEEK_END) is not reliable on macOS)."""
    bs = struct.unpack("I", fcntl.ioctl(fd, DKIOCGETBLOCKSIZE, b"\0" * 4))[0]
    bc = struct.unpack("Q", fcntl.ioctl(fd, DKIOCGETBLOCKCOUNT, b"\0" * 8))[0]
    return bs * bc


def full_sync(fd: int) -> None:
    """F_FULLFSYNC asks the drive to flush its own cache; plain fsync does not on macOS."""
    try:
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    except (OSError, AttributeError):
        os.fsync(fd)


# ---- verification -----------------------------------------------------------------------------

def effective_label(fs: str, label: str) -> str:
    return normalize_label(fs, label) if label else DEFAULT_LABEL


def verify_result(drive: Drive, fs: str, table: str, label: str) -> tuple[bool, dict[str, str], str]:
    facts = {"device": drive.path, "partition table": drive.table or "none"}
    data = [p for p in drive.partitions if p.role != "efi"]
    if len(data) != 1:
        return False, facts, f"expected 1 data partition, found {len(data)}"
    p = data[0]
    facts |= {"partition": p.path, "filesystem": p.fstype or "none", "label": p.label or "(none)",
              "size": str(p.size)}
    if drive.table != EXPECTED_TABLE[table]:
        return False, facts, f"partition map is {drive.table!r}, expected {EXPECTED_TABLE[table]!r}"
    want = FS[fs].lsblk_type
    if p.fstype != want:
        return False, facts, f"filesystem is {p.fstype!r}, expected {want!r}"
    want_label = effective_label(fs, label)
    if (p.label or "").lower() != want_label.lower():
        return False, facts, f"volume name is {p.label!r}, expected {want_label!r}"
    return True, facts, ""


# ---- the backend ---------------------------------------------------------------------------------

class MacOSBackend(OSBackend):
    name = "macos"
    display = "macOS"
    unmount_tool = "diskutil unmount"

    def __init__(self, *, allow_disk_images: bool = False) -> None:
        # allow_disk_images exists ONLY so tests can exercise the real code on a temporary
        # disk image they created. The CLI never sets it and the privileged helper never
        # receives it.
        self.allow_images = allow_disk_images
        self._fs: dict[str, Availability] | None = None

    # -- discovery ---------------------------------------------------------------------------
    @staticmethod
    def _plist(runner: CommandRunner, argv: list[str], *, optional: bool = False):
        try:
            r = runner.run(argv, timeout=60)
        except ToolMissing as e:
            if optional:
                return None
            raise ScanError(f"{e.tool} not found") from e
        if r.returncode != 0 or not r.stdout.strip():
            if optional:
                return None
            raise ScanError((r.stderr or r.stdout).strip() or f"{argv[0]} failed")
        try:
            return plistlib.loads(r.stdout.encode("utf-8"))
        except Exception as e:  # plistlib raises several different types
            if optional:
                return None
            raise ScanError(f"unparseable {argv[0]} output: {e}") from e

    def _physical(self, runner, target: str) -> set[str] | None:
        info = self._plist(runner, ["diskutil", "info", "-plist", target], optional=True)
        return physical_wholes(info) if info else None

    def scan(self, runner: CommandRunner) -> list[Drive]:
        lst = self._plist(runner, ["diskutil", "list", "-plist"])
        entries = lst.get("AllDisksAndPartitions", [])
        infos: dict[str, dict] = {}
        for e in entries:
            if e.get("APFSPhysicalStores"):
                continue
            did = e["DeviceIdentifier"]
            info = self._plist(runner, ["diskutil", "info", "-plist", did], optional=True)
            if info:
                infos[did] = info
                if not info.get("Internal"):  # only candidates need per-partition detail
                    for p in e.get("Partitions") or []:
                        pi = self._plist(runner, ["diskutil", "info", "-plist", p["DeviceIdentifier"]],
                                         optional=True)
                        if pi:
                            infos[p["DeviceIdentifier"]] = pi
        apfs = self._plist(runner, ["diskutil", "apfs", "list", "-plist"], optional=True)
        # Which physical disk(s) back the running system? Unknown => every disk is blocked.
        startup: set[str] | None = set()
        for vol in ("/", "/System/Volumes/Data"):
            s = self._physical(runner, vol)
            if s is None and vol == "/":
                startup = None
                break
            startup |= s or set()
        tm: set[str] | None = set()
        tmi = self._plist(runner, ["tmutil", "destinationinfo", "-X"], optional=True)
        if tmi is None:
            tm = None  # could not check: backups are still caught by volume roles / backupdb marker
        else:
            for d in tmi.get("Destinations", []):
                if d.get("MountPoint"):
                    tm |= self._physical(runner, d["MountPoint"]) or set()
        io = self._plist(runner, ["ioreg", "-a", "-r", "-c", "IOUSBHostDevice"], optional=True)
        media = self._plist(runner, ["ioreg", "-a", "-r", "-c", "IOMedia"], optional=True)
        return parse_macos(lst, infos, apfs, startup=startup, tm=tm, usb=parse_usb(io),
                           allow_virtual=self.allow_images, media_ids=parse_media_ids(media))

    def listed(self, drive: Drive) -> bool:
        return drive.external  # verified external USB only; internal disks are never listed

    def _transports(self) -> tuple[str, ...]:
        return ("usb", "disk image") if self.allow_images else ("usb",)

    def assess(self, drive: Drive) -> Safety:
        s = assess(drive, allowed_transports=self._transports())
        if drive.size and drive.size % max(drive.sector_size, 1):
            return dataclasses.replace(s, eligible=False, blockers=s.blockers + (
                "Capacity is not a multiple of the sector size",))
        return s

    # -- capabilities ----------------------------------------------------------------------------
    def core_tools(self):
        return CORE_TOOLS

    def missing_core_tools(self):
        return [t for t in CORE_TOOLS if find_tool(t) is None]

    def fs_order(self):
        return ("exfat", "fat32", "apfs", "hfsplus", "ntfs", "ext4")

    def fs_support(self) -> dict[str, Availability]:
        if self._fs is None:
            names: set[str] | None = None
            try:
                data = self._plist(CommandRunner(), ["diskutil", "listFilesystems", "-plist"])
                names = {f.get("Personality") for f in data}
            except ScanError:
                pass
            out: dict[str, Availability] = {}
            for k, pers in PERSONALITY.items():
                ok = names is not None and pers in names
                out[k] = Availability(ok, "/usr/sbin/diskutil", "" if ok else (
                    f"diskutil on this Mac does not list a '{pers}' format." if names is not None
                    else "Could not query diskutil for supported formats."))
            for k, why in UNSUPPORTED_REASON.items():
                out[k] = Availability(False, None, why)
            self._fs = out
        return self._fs

    def validate_config(self, size, fs, table, label):
        errs: list[str] = []
        if table not in SCHEME:
            errs.append(f"Unknown partition scheme '{table}'")
        if fs not in FS:
            return errs + [f"Unknown filesystem '{fs}'"]
        if fs not in MAC_FS:
            return errs + [UNSUPPORTED_REASON.get(fs, f"{FS[fs].name} is not supported on macOS")]
        if fs in ("apfs", "hfsplus") and table != "gpt":
            errs.append(f"{FS[fs].name} requires the GPT partition scheme on macOS")
        if table == "mbr" and size > MBR_MAX_BYTES:
            errs.append("MBR cannot address more than 2 TiB; use GPT for this drive")
        if fs == "fat32" and size > MBR_MAX_BYTES:
            errs.append("FAT32 is not practical above 2 TiB; choose exFAT")
        if size and size < MIN_BYTES[fs]:
            errs.append(f"{FS[fs].name} needs at least {human_size(MIN_BYTES[fs])}")
        if (e := validate_label(fs, label)):
            errs.append(e)
        return errs

    # -- planning -------------------------------------------------------------------------------
    def build_plan(self, drive, req, safety, *, fs_ok=None, core_missing=None) -> Plan:
        plan = Plan()
        o = req.options
        core_missing = self.missing_core_tools() if core_missing is None else core_missing
        if not safety.eligible:
            plan.errors += [f"Target not eligible: {b}" for b in safety.blockers]
        if core_missing:
            plan.errors.append("Missing system tools: " + ", ".join(core_missing))
        if req.does_format:
            plan.errors += self.validate_config(drive.size, o.fs, o.table, o.label)
            if o.fs in MAC_FS:
                ok = self.fs_support()[o.fs].ok if fs_ok is None else fs_ok.get(o.fs, False)
                if not ok:
                    plan.errors.append(f"{FS[o.fs].name} is not available on this Mac")
        plan.warnings += list(safety.warnings)
        plan.steps.append(Step("revalidate", f"Verify target identity and safety ({drive.path})"))
        for m in safety.to_unmount:
            plan.steps.append(Step("unmount", f"Unmount {m.mountpoint}", ("diskutil", "unmount", m.source)))
        do_zero = req.does_zero or (req.does_format and o.zero_fill)
        if do_zero:
            plan.steps.append(Step("zero", f"Write zeros across all {drive.size:,} bytes of {drive.path}",
                                   None, True))
            plan.warnings.append("Zero-fill is not guaranteed secure erasure on flash storage.")
        if req.does_format and o.fs in MAC_FS:
            label = effective_label(o.fs, o.label)
            plan.steps.append(Step(
                "erase", f"Erase the disk: new {SCHEME[o.table]} map with one {FS[o.fs].name} volume",
                ("diskutil", "eraseDisk", ERASE_FORMAT[o.fs], label, SCHEME[o.table], drive.path), True))
            plan.steps.append(Step("verify", "Read back and verify the result"))
            if not o.label:
                plan.warnings.append(f"macOS requires a volume name; '{DEFAULT_LABEL}' will be used.")
        return plan

    # -- execution ------------------------------------------------------------------------------
    def run_operation(self, req: OperationRequest, emit: Emit, cancel: threading.Event, *,
                      runner: CommandRunner, scan=None, check_node: bool = True) -> OperationResult:
        scan = scan or (lambda: self.scan(runner))
        dry = runner.dry_run
        diag: list[str] = []
        stage = "revalidate"

        def log(line: str) -> None:
            diag.append(line)
            emit({"event": "log", "line": line})

        def node_check(drive: Drive) -> None:
            if not _DISK_NODE.match(drive.path):
                raise SafetyError(f"Unexpected device path {drive.path}")
            try:
                st = os.stat(drive.path)
            except OSError as e:
                raise SafetyError(f"Cannot stat {drive.path}: {e}") from e
            if not stat.S_ISBLK(st.st_mode):
                raise SafetyError(f"{drive.path} is not a block device")
            if f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}" != drive.maj_min:
                raise SafetyError("Device number does not match the scan")

        def authorize(st: str):
            return guard.authorize(req.identity, scan, self.assess, stage=st,
                                   is_partition=lambda p: not _DISK_NODE.match(p),
                                   check_node=node_check if (check_node and not dry) else None)

        def run(tok, argv: list[str], *, timeout: float = 900) -> str:
            log("$ " + " ".join(argv) + ("   [dry-run: not executed]" if dry else ""))
            r = runner.run(argv, mutating=True, timeout=timeout, token=tok)
            for text in (r.stdout, r.stderr):
                if text.strip():
                    log(text.strip())
            if r.returncode != 0:
                raise OperationError(explain_failure(stage, r.returncode, r.stderr + r.stdout),
                                     f"{' '.join(argv)} -> exit {r.returncode}\n{r.stderr.strip()}")
            return r.stdout

        def authorized(st: str):
            """A fresh token with removable volumes unmounted first (never forced)."""
            tok, safety = authorize(st)
            if safety.to_unmount:
                for m in safety.to_unmount:
                    run(tok, ["diskutil", "unmount", m.source])
                tok, safety = authorize(st)
                if safety.to_unmount and not dry:
                    raise OperationError("Unmounting failed: the selected device is still in use.")
            return tok, safety

        def finish(res: OperationResult) -> OperationResult:
            res.simulated = dry
            res.details = res.details or "\n".join(diag)
            emit({"event": "result", **res.__dict__})
            return res

        try:
            first, first_safety = authorize("revalidate")
            drive = first.drive
            plan = self.build_plan(drive, req, first_safety)
            if not plan.ok:
                raise OperationError("Cannot proceed: " + plan.errors[0], "\n".join(plan.errors))
            o = req.options
            total = len(plan.steps)
            for i, step in enumerate(plan.steps):
                if cancel.is_set():
                    return finish(OperationResult(False, "Cancelled before the next step started.",
                                                  state=STATE_AFTER.get(stage, ""), cancelled=True))
                stage = step.key
                emit({"event": "stage", "index": i, "total": total, "key": step.key,
                      "title": step.title, "determinate": step.key == "zero"})
                if step.key == "revalidate":
                    continue
                if step.key == "unmount":
                    tok, safety = authorize("unmount")
                    if any(m.source == step.argv[2] for m in safety.to_unmount):
                        run(tok, list(step.argv))
                elif step.key == "zero":
                    tok, _s = authorized("zero")
                    run(tok, ["diskutil", "unmountDisk", tok.path])
                    tok, _s = authorize("zero")  # fresh token for the raw open, after the unmount
                    drive = tok.drive
                    if dry:
                        log(f"DRY-RUN: would zero-fill {drive.path} ({drive.size:,} bytes)")
                        continue
                    z = zero_fill(drive.path, drive.size, emit, cancel, token=tok, opener=open_raw_disk,
                                  size_fn=raw_disk_size, sync_fn=full_sync, fd_check=check_mac_fd)
                    log(f"zero-fill wrote {z.written:,} of {z.total:,} bytes")
                    if z.cancelled:
                        return finish(OperationResult(
                            False, f"Zero-fill cancelled after {human_size(z.written)}.",
                            state=STATE_AFTER["zero"], cancelled=True))
                elif step.key == "erase":
                    tok, _s = authorized("erase")
                    run(tok, list(step.argv[:-1]) + [tok.path], timeout=1800)
                elif step.key == "verify":
                    if dry:
                        log("DRY-RUN: verification skipped (nothing was written)")
                        continue
                    cur = None
                    for _ in range(20):  # the new volume appears asynchronously
                        tok, _s = authorize("verify")
                        cur = tok.drive
                        if any(p.role != "efi" for p in cur.partitions):
                            break
                        if cancel.wait(0.5):
                            break
                    ok, facts, problem = verify_result(cur, o.fs, o.table, o.label)
                    if not ok:
                        raise OperationError("Formatting completed but verification failed: " + problem,
                                             "\n".join(f"{k}: {v}" for k, v in facts.items()))
                    return finish(OperationResult(True, "Formatted and verified.", verified=facts))
            if dry:
                return finish(OperationResult(True, "Dry run complete: no writes were performed.",
                                              verified={"mode": "dry-run", "device": drive.path}))
            return finish(OperationResult(
                True, "Zero-fill complete: every byte of the device was written with zeros.",
                verified={"device": drive.path, "bytes written": f"{drive.size:,}"}))
        except UnauthorizedOperation as e:
            return finish(OperationResult(False, f"Refused: {e}", str(e), STATE_AFTER.get(stage, "")))
        except SafetyError as e:
            return finish(OperationResult(False, f"Aborted: {e}", str(e), STATE_AFTER.get(stage, "")))
        except OperationError as e:
            return finish(OperationResult(False, e.message, e.details, STATE_AFTER.get(stage, "")))
        except ZeroFillError as e:
            return finish(OperationResult(
                False, f"Zero-fill interrupted: {e} ({human_size(e.written)} confirmed written).",
                str(e), STATE_AFTER["zero"]))
        except ToolMissing as e:
            return finish(OperationResult(False, f"Missing utility: {e.tool}.", str(e),
                                          STATE_AFTER.get(stage, "")))
        except PermissionError as e:
            return finish(OperationResult(
                False, "Insufficient privileges: opening the disk was denied (administrator "
                "rights are required).", repr(e), STATE_AFTER.get(stage, "")))
        except (ScanError, OSError) as e:
            return finish(OperationResult(False, f"System error: {e}", repr(e), STATE_AFTER.get(stage, "")))
        except Exception as e:  # last resort: report, never crash silently mid-operation
            return finish(OperationResult(False, f"Unexpected error: {e}", repr(e),
                                          STATE_AFTER.get(stage, "")))

    def prerequisite_report(self) -> tuple[list[str], bool]:
        missing = self.missing_core_tools()
        lines = ["Core tools (built into macOS):"]
        lines += [f"  [{'ok' if t not in missing else '--'}] {t}" for t in CORE_TOOLS]
        if not missing:
            lines.append("Formats supported by this macOS version (diskutil listFilesystems):")
            for k, a in self.fs_support().items():
                if k in MAC_FS:
                    lines.append(f"  [{'ok' if a.ok else '--'}] {FS[k].name}")
            lines.append("Not available on macOS:")
            lines += [f"  [--] {FS[k].name}: {why}" for k, why in UNSUPPORTED_REASON.items()]
        return lines, not missing
