"""Runs an OperationRequest step by step and emits structured events.

The same `run_operation` is used by the privileged helper (real runner) and by dry-run
(DryRunRunner, which records mutating commands instead of executing them).

Events (dicts): stage, progress, log, result.
"""

from __future__ import annotations

import dataclasses
import os
import stat
import threading
from collections.abc import Callable

from . import guard
from .devices import ScanError, is_partition_node, scan_devices
from .guard import UnauthorizedOperation
from .filesystems import PLACEHOLDER, detect_exfat_flag, mkfs_argv
from .formatting import (STATE_AFTER, build_plan, explain_failure, sfdisk_argv, verify_result,
                         wipefs_argv)
from .models import (Drive, OperationError, OperationRequest, OperationResult, Partition,
                     SafetyError, human_size)
from .partitioning import sfdisk_script
from .runner import CommandRunner, ToolMissing
from .safety import Safety
from .zero_fill import ZeroFillError, zero_fill

Emit = Callable[[dict], None]


def run_operation(req: OperationRequest, emit: Emit, cancel: threading.Event, *,
                  runner: CommandRunner, scan: Callable[[], list[Drive]] | None = None,
                  assess_fn: Callable[[Drive], Safety] | None = None,
                  check_blockdev: bool = True) -> OperationResult:
    """The Linux operation sequence. Every destructive stage first mints a fresh token with
    guard.authorize() (re-scan, external-only and system-storage checks, identity comparison);
    the runner and the zero-fill opener then refuse anything that token does not cover."""
    scan = scan or (lambda: scan_devices(runner))
    if assess_fn is None:  # direct callers still get the full Linux policy, never a weaker one
        from ..backends.linux import LinuxBackend
        assess_fn = LinuxBackend().assess
    dry = runner.dry_run
    diag: list[str] = []
    stage = "revalidate"

    def log(line: str) -> None:
        diag.append(line)
        emit({"event": "log", "line": line})

    def node_check(drive: Drive) -> None:
        try:
            st = os.stat(drive.path)
        except OSError as e:
            raise SafetyError(f"Cannot stat {drive.path}: {e}") from e
        if not stat.S_ISBLK(st.st_mode):
            raise SafetyError(f"{drive.path} is not a block device")
        if f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}" != drive.maj_min:
            raise SafetyError("Device number does not match the scan")

    def authorize(st: str):
        return guard.authorize(req.identity, scan, assess_fn, stage=st,
                               is_partition=is_partition_node if check_blockdev else None,
                               check_node=node_check if (check_blockdev and not dry) else None)

    def run(tok, argv: list[str], *, input: str | None = None, timeout: float = 600) -> str:
        log("$ " + " ".join(argv) + ("   [dry-run: not executed]" if dry else ""))
        r = runner.run(argv, mutating=True, input=input, timeout=timeout, token=tok)
        if r.stdout.strip():
            log(r.stdout.strip())
        if r.stderr.strip():
            log(r.stderr.strip())
        if r.returncode != 0:
            raise OperationError(explain_failure(stage, r.returncode, r.stderr),
                                 f"{' '.join(argv)} -> exit {r.returncode}\n{r.stderr.strip()}")
        return r.stdout

    def authorized(st: str):
        """A fresh token for a destructive stage, with removable mounts unmounted first
        (plain umount, never forced; a busy mount aborts)."""
        tok, safety = authorize(st)
        if safety.to_unmount:
            for m in safety.to_unmount:
                run(tok, ["umount", m.mountpoint])
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
        plan = build_plan(drive, req, first_safety, exfat_flag=detect_exfat_flag(runner)
                          if req.options.fs == "exfat" else "-L")
        if not plan.ok:
            raise OperationError("Cannot proceed: " + plan.errors[0], "\n".join(plan.errors))
        o = req.options
        part_path = PLACEHOLDER
        total = len(plan.steps)
        for i, step in enumerate(plan.steps):
            if cancel.is_set():
                return finish(OperationResult(False, "Cancelled before the next step started.",
                                              state=STATE_AFTER.get(stage, ""), cancelled=True))
            stage = step.key
            emit({"event": "stage", "index": i, "total": total, "key": step.key,
                  "title": step.title, "determinate": step.key == "zero"})
            if step.key == "revalidate":
                continue  # done above; repeated (as a fresh token) before every destructive step
            if step.key == "unmount":
                tok, safety = authorize("unmount")
                if any(m.mountpoint == step.argv[1] for m in safety.to_unmount):
                    run(tok, list(step.argv))
            elif step.key == "zero":
                tok, _s = authorized("zero")
                drive = tok.drive
                if dry:
                    log(f"DRY-RUN: would zero-fill {drive.path} ({drive.size:,} bytes)")
                    continue
                zres = zero_fill(drive.path, drive.size, emit, cancel, token=tok)
                log(f"zero-fill wrote {zres.written:,} of {zres.total:,} bytes")
                if zres.cancelled:
                    return finish(OperationResult(
                        False, f"Zero-fill cancelled after {human_size(zres.written)}.",
                        state=STATE_AFTER["zero"], cancelled=True))
                tok, _s = authorize("reread")
                try:
                    run(tok, ["blockdev", "--rereadpt", tok.path])
                except OperationError:
                    log("blockdev --rereadpt failed (non-fatal)")
            elif step.key == "wipe":
                tok, _s = authorized("wipe")
                run(tok, list(step.argv))
            elif step.key == "partition":
                tok, _s = authorized("partition")
                run(tok, sfdisk_argv(tok.path), input=sfdisk_script(o.table, o.fs), timeout=120)
            elif step.key == "settle":
                r = runner.run(["udevadm", "settle", "--timeout=15"], mutating=False)
                log("$ udevadm settle --timeout=15")
                if r.returncode != 0:
                    raise OperationError(explain_failure(stage, r.returncode, r.stderr), r.stderr)
                if dry:
                    continue
                part_path = _wait_for_partition(scan, req, cancel, log)
            elif step.key == "mkfs":
                tok, _s = authorized("mkfs")
                if not dry and (len(tok.drive.partitions) != 1 or tok.drive.partitions[0].path != part_path):
                    raise SafetyError("The new partition changed before formatting")
                if dry:  # the plan names a placeholder partition that does not exist yet
                    tok = dataclasses.replace(tok, nodes=tok.nodes | {PLACEHOLDER},
                                              drive=dataclasses.replace(tok.drive, partitions=(
                                                  Partition("new", PLACEHOLDER, 0),)))
                argv = mkfs_argv(o.fs, part_path, o.label, owner=req.owner,
                                 exfat_flag=detect_exfat_flag(runner) if o.fs == "exfat" else "-L")
                run(tok, argv, timeout=3600)
            elif step.key == "sync":
                tok, _s = authorize("sync")
                run(tok, ["sync"])
                runner.run(["udevadm", "settle", "--timeout=15"], mutating=False)
            elif step.key == "verify":
                if dry:
                    log("DRY-RUN: verification skipped (nothing was written)")
                    continue
                tok, _s = authorize("verify")
                ok, facts, problem = verify_result(tok.drive, o)
                if not ok:
                    raise OperationError(
                        "Formatting completed but verification failed: " + problem,
                        "\n".join(f"{k}: {v}" for k, v in facts.items()))
                return finish(OperationResult(True, "Formatted and verified.", verified=facts))
        if dry:
            return finish(OperationResult(
                True, "Dry run complete: no writes were performed.",
                verified={"mode": "dry-run", "device": drive.path}))
        what = "Zero-fill complete: every byte of the device was written with zeros."
        return finish(OperationResult(True, what, verified={"device": drive.path,
                                                           "bytes written": f"{drive.size:,}"}))
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
        return finish(OperationResult(False, f"Missing utility: {e.tool}. Install it and retry.",
                                      str(e), STATE_AFTER.get(stage, "")))
    except (ScanError, OSError) as e:
        return finish(OperationResult(False, f"System error: {e}", repr(e), STATE_AFTER.get(stage, "")))
    except Exception as e:  # last resort: report, never crash silently mid-operation
        return finish(OperationResult(False, f"Unexpected error: {e}", repr(e),
                                      STATE_AFTER.get(stage, "")))


def _wait_for_partition(scan, req: OperationRequest, cancel: threading.Event, log) -> str:
    """Poll the structured device list until exactly one partition appears on the target."""
    for _ in range(40):
        drive = next((d for d in scan() if d.path == req.identity.path), None)
        if drive is None:
            raise SafetyError(f"Device {req.identity.path} is no longer present (disconnected?)")
        if len(drive.partitions) == 1:
            log(f"new partition: {drive.partitions[0].path}")
            return drive.partitions[0].path
        if cancel.wait(0.5):
            break
    raise OperationError("The new partition did not appear in time.",
                         "The partition table was written but the kernel did not report the partition.")


# --- app-level backends (what the UI talks to) ---------------------------------------------------

class Backend:
    """Live mode: real scan, privileged helper for writes. Delegates OS details to an OSBackend."""

    mode = "live"
    label = "LIVE"
    writes = True

    def __init__(self, os_backend=None) -> None:
        if os_backend is None:
            from .platform import get_backend
            os_backend = get_backend()
        self.os = os_backend

    @property
    def host(self) -> str:
        return self.os.name

    def scan(self) -> list[Drive]:
        return self.os.scan(CommandRunner())

    def listed(self, drive: Drive) -> bool:
        return self.os.listed(drive)

    def assess(self, drive: Drive):
        return self.os.assess(drive)

    def fs_support(self):
        return self.os.fs_support()

    def fs_order(self) -> tuple[str, ...]:
        return self.os.fs_order()

    def fs_available(self) -> dict[str, bool]:
        return {k: a.ok for k, a in self.fs_support().items()}

    def core_missing(self) -> list[str]:
        return self.os.missing_core_tools()

    def validate_config(self, size, fs, table, label) -> list[str]:
        return self.os.validate_config(size, fs, table, label)

    def build_plan(self, drive: Drive, req: OperationRequest, safety):
        return self.os.build_plan(drive, req, safety, fs_ok=self.fs_available(),
                                  core_missing=self.core_missing())

    def execute(self, req: OperationRequest, emit: Emit, cancel: threading.Event) -> OperationResult:
        from ..privilege.helper import run_via_helper
        return run_via_helper(req, emit, cancel)


class DryRunBackend(Backend):
    """Real scan and validation; the executor runs with a runner that cannot write."""

    mode = "dry-run"
    label = "DRY-RUN"
    writes = False

    def execute(self, req, emit, cancel):
        from .runner import DryRunRunner
        return self.os.run_operation(req, emit, cancel, runner=DryRunRunner())
