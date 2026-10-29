"""The centralized destructive-operation guard.

Nothing in 4matter writes to a disk except through this module's tokens:

* `authorize()` is the ONLY place a `ValidatedTarget` is minted. It re-scans the system,
  refuses partitions and anything not positively verified as external physical storage,
  re-applies every system-storage protection and compares the drive with the identity the
  user selected.
* `CommandRunner.run(..., mutating=True)` and the zero-fill openers refuse to act without an
  authentic, fresh token, and refuse any device node that is not part of that token's target.

The checks are re-run (a new token is minted) before every destructive stage, and tokens expire
quickly so a stale authorization cannot be reused. There are no override switches.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .filesystems import PLACEHOLDER
from .models import Drive, Identity, SafetyError
from .safety import Safety, check_identity

TOKEN_TTL = 120.0  # seconds a token stays valid; each destructive stage mints a new one
_SEAL = object()   # only authorize() knows how to attach this

# The only programs that may run with mutating=True, and (for diskutil) the only verbs.
# Deliberately absent: dd, shred, parted, `diskutil zeroDisk/partitionDisk/apfs ...`.
DESTRUCTIVE_TOOLS = {"wipefs", "sfdisk", "umount", "blockdev", "mkfs.vfat", "mkfs.exfat", "mkfs.ntfs",
                     "mkfs.ext4", "mkfs.btrfs", "diskutil", "sync"}
DISKUTIL_VERBS = {"unmount", "unmountDisk", "eraseDisk"}
WHOLE_DISK_ONLY = {"sfdisk", "unmountDisk", "eraseDisk"}


class UnauthorizedOperation(SafetyError):
    """A destructive primitive was used without a valid, matching authorization."""


@dataclass(frozen=True)
class ValidatedTarget:
    identity: Identity
    drive: Drive
    stage: str
    nodes: frozenset[str]        # /dev nodes of the target: the disk, its partitions, its volumes
    mountpoints: frozenset[str]
    issued: float = 0.0
    seal: object = field(default=None, repr=False, compare=False)

    @property
    def path(self) -> str:
        return self.drive.path

    @property
    def partition_nodes(self) -> frozenset[str]:
        return frozenset(p.path for p in self.drive.partitions)


def _check_authentic(token: object, *, now: float | None = None) -> ValidatedTarget:
    if not isinstance(token, ValidatedTarget) or token.seal is not _SEAL:
        raise UnauthorizedOperation("destructive operation attempted without a validated target")
    age = (time.monotonic() if now is None else now) - token.issued
    if age > TOKEN_TTL or age < -1:
        raise UnauthorizedOperation("authorization expired; the target must be re-validated")
    return token


def check_argv(token: object, argv: list[str], *, now: float | None = None) -> ValidatedTarget:
    """Validate a mutating command against the token. Raises UnauthorizedOperation."""
    tok = _check_authentic(token, now=now)
    tool = os.path.basename(argv[0]) if argv else ""
    if tool not in DESTRUCTIVE_TOOLS:
        raise UnauthorizedOperation(f"{tool or '(empty)'} is not an allowed disk-modifying tool")
    args = argv[1:]
    verb = None
    if tool == "diskutil":
        verb = args[0] if args else ""
        if verb not in DISKUTIL_VERBS:
            raise UnauthorizedOperation(f"diskutil {verb or '(no verb)'} is not allowed")
    devs = [a for a in args if a.startswith("/dev/") or a == PLACEHOLDER]  # placeholder: dry-run plans
    for d in devs:
        if d != os.path.normpath(d) or d not in tok.nodes:
            raise UnauthorizedOperation(f"{d} is not part of the validated target {tok.path}")
    if tool == "umount":
        for a in args:
            if not a.startswith("-") and a not in tok.nodes and a not in tok.mountpoints:
                raise UnauthorizedOperation(f"{a} is not a mount of the validated target")
    if (tool in WHOLE_DISK_ONLY or verb in WHOLE_DISK_ONLY) and devs != [tok.path]:
        raise UnauthorizedOperation(f"{tool} may only address the whole target disk {tok.path}")
    if tool.startswith("mkfs.") and (len(devs) != 1 or devs[0] not in tok.partition_nodes):
        raise UnauthorizedOperation("mkfs may only address a partition of the validated target")
    if tool in ("wipefs", "blockdev") and not devs:
        raise UnauthorizedOperation(f"{tool} needs an explicit validated device")
    return tok


def require_target(token: object, path: str, size: int, *, now: float | None = None) -> ValidatedTarget:
    """For zero-fill: the token must be authentic, fresh, and name exactly this disk and size."""
    tok = _check_authentic(token, now=now)
    if path != tok.path or path != os.path.normpath(path):
        raise UnauthorizedOperation(f"{path} is not the validated target {tok.path}")
    if size != tok.drive.size:
        raise UnauthorizedOperation("capacity differs from the validated target")
    return tok


def authorize(identity: Identity, scan: Callable[[], list[Drive]],
              assess_fn: Callable[[Drive], Safety], *, stage: str,
              is_partition: Callable[[str], bool] | None = None,
              check_node: Callable[[Drive], None] | None = None) -> tuple[ValidatedTarget, Safety]:
    """Re-discover the target and mint a token, or raise SafetyError. Called before EVERY
    destructive stage (unmount, zero-fill, wipe, partition, format, ...)."""
    try:
        drives = scan()
    except Exception as e:  # cannot re-scan => cannot confirm => abort
        raise SafetyError(f"Could not re-scan devices: {e}") from e
    path = identity.path
    if not path.startswith("/dev/") or path != os.path.normpath(path):
        raise SafetyError(f"Refusing malformed device path {path!r}")
    for d in drives:
        if any(p.path == path for p in d.partitions) or any(m.source == path for m in d.mounts):
            raise SafetyError(f"{path} is a partition or volume, not a whole disk; only whole external "
                              "disks can be modified")
    if is_partition and is_partition(path):
        raise SafetyError(f"{path} is a partition, not a whole disk")
    cur = next((d for d in drives if d.path == path), None)
    if cur is None:
        raise SafetyError(f"Device {path} is no longer present (disconnected?)")
    diffs = check_identity(identity, cur)
    if diffs:
        raise SafetyError("Device identity changed since selection: " + "; ".join(diffs))
    safety = assess_fn(cur)
    if not safety.eligible:
        raise SafetyError("Device is not eligible: " + safety.reason)
    if check_node:
        check_node(cur)
    nodes = {cur.path, *(p.path for p in cur.partitions), *(m.source for m in cur.mounts)}
    token = ValidatedTarget(identity, cur, stage, frozenset(nodes),
                            frozenset(m.mountpoint for m in cur.mounts), time.monotonic(), _SEAL)
    return token, safety
