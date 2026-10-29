"""Safety engine. Fails closed: anything not positively understood is a blocker."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Drive, Identity, Mount, SafetyError

# Only mounts under these are treated as removable-media mounts that may be unmounted.
REMOVABLE_MOUNT_PREFIXES = ("/media/", "/run/media/", "/mnt/", "/Volumes/")
REMOVABLE_MOUNT_EXACT = ("/media", "/mnt")
SYSTEM_MOUNTS = ("/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/home", "/etc", "/opt", "/nix")


# Blockers containing these phrases mean "this external disk is in use by the system or holds
# protected data" (shown as PROTECTED); anything else is shown as UNAVAILABLE.
PROTECTED_PHRASES = ("running", "system filesystem", "root filesystem", "swap", "Time Machine",
                     "system or backup", "startup disk", "stacked", "backs the", "Contains Apple_",
                     "mount table")


@dataclass(frozen=True)
class Safety:
    eligible: bool
    blockers: tuple[str, ...] = ()
    to_unmount: tuple[Mount, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def protected(self) -> bool:
        return not self.eligible and any(p in b for b in self.blockers for p in PROTECTED_PHRASES)

    @property
    def reason(self) -> str:
        return "; ".join(self.blockers)


def is_removable_mount(mp: str) -> bool:
    return mp in REMOVABLE_MOUNT_EXACT or mp.startswith(REMOVABLE_MOUNT_PREFIXES)


def assess(drive: Drive, *, root_dev: str | None = None,
           allowed_transports: tuple[str, ...] = ("usb",),
           protected: dict[str, str] | None = None) -> Safety:
    """The single eligibility decision. A disk is eligible only if it is positively verified as
    external physical storage AND nothing the system uses lives on it. Fails closed.

    `protected` maps "maj:min" / "/dev/..." keys to a reason the running system uses them
    (Linux: from /proc/self/mountinfo and /proc/swaps, already expanded to physical disks).
    """
    blockers: list[str] = []
    warnings: list[str] = []
    if not drive.external:
        blockers.append("Not verified as external physical storage (internal, virtual or "
                        "unverifiable disks are never modified)")
    if drive.dtype != "disk":
        blockers.append(f"Unsupported device type '{drive.dtype}' (only whole disks are supported)")
    if drive.name.startswith(("loop", "zram", "sr", "ram", "nbd", "md", "dm-", "mmcblk0boot")):
        blockers.append("Virtual, optical or RAM-backed device")
    if not drive.maj_min or not drive.path.startswith("/dev/"):
        blockers.append("Device identity cannot be determined")
    if drive.transport not in allowed_transports:
        blockers.append(f"Not USB-attached (transport: {drive.transport or 'Unknown'})")
    blockers.extend(drive.blockers)  # platform backend findings (running system, Time Machine, ...)
    if drive.read_only:
        blockers.append("Device is read-only (check the drive's write-protect switch)")
    if drive.size <= 0:
        blockers.append("No media present or capacity unknown")
    if drive.stack:
        kinds = ", ".join(sorted({s.type for s in drive.stack}))
        blockers.append(f"Contains active stacked storage ({kinds}); close or remove it first")

    to_unmount: list[Mount] = []
    for m in drive.mounts:
        if m.mountpoint == "[SWAP]" or m.mountpoint.startswith("[SWAP"):
            blockers.append("Contains active swap")
        elif is_removable_mount(m.mountpoint):
            to_unmount.append(m)
        elif m.mountpoint in SYSTEM_MOUNTS or m.mountpoint.startswith(("/boot/", "/var/", "/usr/")):
            blockers.append(f"Contains a system filesystem mounted at {m.mountpoint}")
        else:
            blockers.append(f"Mounted at {m.mountpoint} (not a removable-media mount point); "
                            "unmount it yourself if you are sure")
    if root_dev and root_dev in {drive.maj_min, *(p.maj_min for p in drive.partitions)}:
        blockers.append("Holds the running root filesystem")
    if protected is not None:
        if "*" in protected:
            blockers.append("Cannot verify system storage: " + protected["*"])
        keys = [drive.maj_min, drive.path, *(p.maj_min for p in drive.partitions),
                *(p.path for p in drive.partitions)]
        for k in keys:
            if k and k in protected:
                blockers.append(f"This disk backs the running system ({protected[k]})")
                break
    if drive.fstype == "swap" or any(p.fstype == "swap" for p in drive.partitions):
        warnings.append("Contains a swap signature (not active)")
    if not drive.serial and not drive.wwn:
        warnings.append("No serial or WWN reported; identity relies on model, size and device number")
    return Safety(not blockers, tuple(dict.fromkeys(blockers)), tuple(to_unmount), tuple(warnings))


def check_identity(expected: Identity, current: Drive) -> list[str]:
    """Return a list of mismatches between what the user selected and what is there now.

    Every recorded field must match exactly, including the connection `link` and `instance`:
    a drive that was unplugged and plugged in again is a new connection and needs a fresh
    selection, even if it looks identical.
    """
    now = Identity.of(current)
    return [f"{f}: expected {getattr(expected, f)!r}, found {getattr(now, f)!r}"
            for f in Identity.__dataclass_fields__ if getattr(expected, f) != getattr(now, f)]


def revalidate(expected: Identity, scan, *, root_dev: str | None = None,
               assess_fn=None) -> tuple[Drive, Safety]:
    """Re-scan and confirm the same, still-eligible drive is at the selected path.

    Raises SafetyError if the device vanished, changed identity, or is not safe.
    """
    try:
        drives = scan()
    except Exception as e:  # scanning failure => cannot confirm => abort
        raise SafetyError(f"Could not re-scan devices: {e}") from e
    cur = next((d for d in drives if d.path == expected.path), None)
    if cur is None:
        raise SafetyError(f"Device {expected.path} is no longer present (disconnected?)")
    diffs = check_identity(expected, cur)
    if diffs:
        raise SafetyError("Device identity changed since selection: " + "; ".join(diffs))
    s = assess_fn(cur) if assess_fn else assess(cur, root_dev=root_dev)
    if not s.eligible:
        raise SafetyError("Device is not eligible: " + s.reason)
    return cur, s


def confirmation_token(identity: Identity) -> str:
    """What the user must type to confirm a zero-fill (e.g. 'sdb')."""
    return identity.short()


def confirmation_ok(identity: Identity, typed: str) -> bool:
    return typed.strip() == confirmation_token(identity)
