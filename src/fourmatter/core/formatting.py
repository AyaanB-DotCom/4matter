"""Operation planning (pure) plus helpers used by the executor: wipe/partition argv,
partition discovery, verification and plain-English failure messages."""

from __future__ import annotations

from .filesystems import (FS, LINUX_FS, PLACEHOLDER, availability, missing_core_tools, mkfs_argv,
                          validate_label)
from .models import Drive, FormatOptions, OperationRequest, Plan, Step
from .partitioning import expected_pttype, sfdisk_script, validate_config
from .safety import Safety


def wipefs_argv(device: str) -> list[str]:
    return ["wipefs", "--all", device]


def sfdisk_argv(disk: str) -> list[str]:
    return ["sfdisk", "--wipe", "always", "--wipe-partitions", "always", disk]


def build_plan(drive: Drive, req: OperationRequest, safety: Safety, *,
               tools_ok: dict[str, bool] | None = None, core_missing: list[str] | None = None,
               exfat_flag: str = "-L") -> Plan:
    """Describe everything that would happen. Shared by dry-run, confirm screen and executor."""
    plan = Plan()
    o: FormatOptions = req.options
    core_missing = missing_core_tools() if core_missing is None else core_missing
    if not safety.eligible:
        plan.errors += [f"Target not eligible: {b}" for b in safety.blockers]
    if core_missing:
        plan.errors.append("Missing system tools: " + ", ".join(core_missing))
    if req.does_format:
        plan.errors += validate_config(drive.size, o.fs, o.table, o.label)
        if o.fs in FS and o.fs not in LINUX_FS:
            plan.errors.append(f"{FS[o.fs].name} cannot be created on Linux")
        elif o.fs in FS:
            ok = availability(o.fs).ok if tools_ok is None else tools_ok.get(o.fs, False)
            if not ok:
                plan.errors.append(f"Missing {FS[o.fs].tool}; install {FS[o.fs].package}")
    plan.warnings += list(safety.warnings)
    dev = drive.path
    plan.steps.append(Step("revalidate", f"Verify target identity and safety ({dev})"))
    for m in safety.to_unmount:
        plan.steps.append(Step("unmount", f"Unmount {m.mountpoint}", ("umount", m.mountpoint)))
    do_zero = req.does_zero or (req.does_format and o.zero_fill)
    if do_zero:
        plan.steps.append(Step("zero", f"Write zeros across all {drive.size:,} bytes of {dev}",
                               None, True))
        plan.warnings.append("Zero-fill is not guaranteed secure erasure on flash storage.")
    if req.does_format:
        for p in drive.partitions:
            if not do_zero:
                plan.steps.append(Step("wipe", f"Remove signatures from {p.path}",
                                       tuple(wipefs_argv(p.path)), True))
        plan.steps.append(Step("wipe", f"Remove partition-table signatures from {dev}",
                               tuple(wipefs_argv(dev)), True))
        plan.steps.append(Step(
            "partition", f"Create {o.table.upper()} table with one partition", tuple(sfdisk_argv(dev)),
            True))
        plan.steps.append(Step("settle", "Wait for the kernel and udev to see the new partition",
                               ("udevadm", "settle")))
        if o.fs in FS:
            plan.steps.append(Step(
                "mkfs", f"Create {FS[o.fs].name} filesystem", tuple(mkfs_argv(
                    o.fs, PLACEHOLDER, o.label, owner=req.owner, exfat_flag=exfat_flag)), True))
        plan.steps.append(Step("sync", "Flush writes", ("sync",)))
        plan.steps.append(Step("verify", "Read back and verify the result"))
    else:
        plan.steps.append(Step("sync", "Flush writes", ("sync",)))
    return plan


def required_tools(req: OperationRequest) -> list[str]:
    tools = ["lsblk", "sync"]
    if req.does_format:
        tools += ["sfdisk", "wipefs", "udevadm"]
        if req.options.fs in FS:
            tools.append(FS[req.options.fs].tool)
    return tools


def find_new_partition(drive: Drive) -> str | None:
    """Return the path of the single partition on a freshly scanned drive, else None."""
    return drive.partitions[0].path if len(drive.partitions) == 1 else None


def verify_result(drive: Drive, o: FormatOptions) -> tuple[bool, dict[str, str], str]:
    """Check that the read-back layout is what we asked for. Returns (ok, facts, problem)."""
    facts: dict[str, str] = {"device": drive.path, "partition table": (drive.table or "none")}
    if len(drive.partitions) != 1:
        return False, facts, f"expected 1 partition, found {len(drive.partitions)}"
    p = drive.partitions[0]
    facts |= {"partition": p.path, "filesystem": p.fstype or "none", "label": p.label or "(none)",
              "size": str(p.size)}
    if drive.table != expected_pttype(o.table):
        return False, facts, f"partition table is {drive.table!r}, expected {expected_pttype(o.table)!r}"
    want = FS[o.fs].lsblk_type
    if p.fstype != want:
        return False, facts, f"filesystem is {p.fstype!r}, expected {want!r}"
    if o.label and (p.label or "").lower() != o.label.lower():
        return False, facts, f"label is {p.label!r}, expected {o.label!r}"
    return True, facts, ""


def explain_failure(stage: str, returncode: int, stderr: str) -> str:
    """Translate a failed tool run into a sentence a non-expert can act on."""
    s = stderr.lower()
    if "ownership of the affected disk" in s or "requires root" in s or "not privileged" in s:
        why = "permission was denied (administrator rights are required)"
    elif "busy" in s or "in use" in s or "dissented" in s or "couldn't unmount" in s \
            or "failed to unmount" in s or "could not unmount" in s:
        why = "the selected device is still in use"
    elif "permission denied" in s or "operation not permitted" in s:
        why = "permission was denied (administrator rights are required)"
    elif "read-only" in s or "write protect" in s:
        why = "the device is read-only or write-protected"
    elif "no such file" in s or "no such device" in s or "not a block device" in s:
        why = "the device disappeared (was it unplugged?)"
    elif "input/output error" in s or "i/o error" in s:
        why = "the drive reported an I/O error; it may be failing or disconnected"
    elif "timed out" in s:
        why = "the tool timed out"
    else:
        why = f"the tool exited with status {returncode}"
    verb = {"unmount": "Unmounting", "erase": "Erasing and formatting the disk", "wipe": "Clearing old signatures", "partition":
            "Creating the partition table", "mkfs": "Formatting", "zero": "Zero-filling",
            "settle": "Waiting for the new partition", "sync": "Flushing writes"}.get(stage, stage)
    return f"{verb} failed: {why}."


STATE_AFTER = {  # what is true of the drive if the run stops during this stage
    "revalidate": "Nothing was changed. The drive is untouched.",
    "unmount": "Nothing was erased. Some partitions may now be unmounted.",
    "zero": "The drive is only partly overwritten. Its old data and partition table are "
            "partly destroyed and it may be unusable until formatted.",
    "wipe": "Old filesystem or partition signatures may have been removed. "
            "The drive probably has no usable partition table.",
    "partition": "The drive may have no valid partition table or an incomplete one.",
    "settle": "A new partition table was written but no filesystem was created.",
    "erase": "The disk may be partly erased, with no valid partition map or filesystem.",
    "mkfs": "A new partition table exists but the filesystem may be incomplete or missing.",
    "sync": "Changes were written; flushing was interrupted.",
    "verify": "Formatting finished but the result could not be verified.",
}
