"""Bounded, sequential zero-fill of an entire block device.

Progress counts only bytes that have been fsync'd, so it reflects completed writes
rather than data parked in the page cache.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .guard import UnauthorizedOperation, ValidatedTarget, require_target

CHUNK = 4 * 1024 * 1024
SYNC_EVERY = 32 * 1024 * 1024


class ZeroFillError(Exception):
    def __init__(self, message: str, written: int):
        super().__init__(message)
        self.written = written


class DryRunViolation(Exception):
    """A write was attempted in dry-run mode. This must never happen."""


@dataclass
class ZeroFillResult:
    written: int
    total: int
    elapsed: float
    cancelled: bool = False


def progress_stats(written: int, total: int, elapsed: float) -> dict:
    """Fraction, average rate and ETA (None until enough data to estimate)."""
    frac = 0.0 if total <= 0 else min(1.0, written / total)
    rate = written / elapsed if elapsed > 0 and written > 0 else None
    eta = (total - written) / rate if rate and written < total and elapsed >= 2 else None
    return {"fraction": frac, "rate": rate, "eta": eta}


def open_block_device(path: str, *, dry_run: bool = False) -> int:
    """Open for exclusive writing. O_EXCL on a block device makes the kernel refuse
    (EBUSY) if it, or a partition of it, is mounted or otherwise in use."""
    if dry_run:
        raise DryRunViolation(f"refusing to open {path} for writing in dry-run mode")
    return os.open(path, os.O_WRONLY | os.O_EXCL | getattr(os, "O_CLOEXEC", 0))


def device_size(fd: int) -> int:
    return os.lseek(fd, 0, os.SEEK_END)


def check_linux_fd(fd: int, token: ValidatedTarget) -> None:
    """The opened fd must be the block device whose number was validated (path swaps and
    re-plugs between validation and open are caught here)."""
    st = os.fstat(fd)
    if not stat.S_ISBLK(st.st_mode):
        raise UnauthorizedOperation("the opened path is not a block device")
    if f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}" != token.drive.maj_min:
        raise UnauthorizedOperation("the opened device is not the validated device")


def zero_fill(path: str, expected_size: int, emit: Callable[[dict], None],
              cancel: threading.Event, *, token: ValidatedTarget | None, dry_run: bool = False,
              opener: Callable[..., int] = open_block_device, chunk: int = CHUNK,
              sync_every: int = SYNC_EVERY, clock: Callable[[], float] = time.monotonic,
              size_fn: Callable[[int], int] = device_size,
              sync_fn: Callable[[int], None] = os.fsync,
              fd_check: Callable[[int, ValidatedTarget], None] = check_linux_fd) -> ZeroFillResult:
    """Write zeros over [0, size) of the validated target. Refuses without a token for exactly
    this path and capacity, and verifies the opened fd IS the validated device."""
    tok = require_target(token, path, expected_size)
    fd = opener(path, dry_run=dry_run)
    try:
        fd_check(fd, tok)
        total = size_fn(fd)
        if total != expected_size:
            raise ZeroFillError(f"capacity mismatch: device reports {total} bytes, "
                                f"expected {expected_size}", 0)
        os.lseek(fd, 0, os.SEEK_SET)
        return write_zeros(fd, total, emit, cancel, chunk=chunk, sync_every=sync_every,
                           clock=clock, sync_fn=sync_fn)
    finally:
        os.close(fd)


def write_zeros(fd: int, total: int, emit: Callable[[dict], None], cancel: threading.Event, *,
                chunk: int = CHUNK, sync_every: int = SYNC_EVERY,
                clock: Callable[[], float] = time.monotonic,
                sync_fn: Callable[[int], None] = os.fsync) -> ZeroFillResult:
    """The write loop over an already-validated, already-open descriptor."""
    zeros = memoryview(bytes(chunk))
    written = synced = 0
    start = last = clock()
    last_synced = 0
    while written < total:
        if cancel.is_set():
            sync_fn(fd)
            return ZeroFillResult(synced if synced else 0, total, clock() - start, True)
        want = min(chunk, total - written)
        view, off = zeros[:want], 0
        while off < want:  # handle short writes
            try:
                n = os.write(fd, view[off:])
            except OSError as e:
                raise ZeroFillError(f"write failed at byte {written + off}: {e.strerror or e}",
                                    synced) from e
            if n <= 0:
                raise ZeroFillError(f"write returned {n} at byte {written + off}", synced)
            off += n
        written += want
        if written - synced >= sync_every or written == total:
            try:
                sync_fn(fd)
            except OSError as e:
                raise ZeroFillError(f"flush failed near byte {written}: {e.strerror or e}",
                                    synced) from e
            synced = written
            now = clock()
            window = now - last
            stats = progress_stats(synced, total, now - start)
            emit({"event": "progress", "written": synced, "total": total,
                  "elapsed": now - start, "rate": (synced - last_synced) / window
                  if window > 0 else stats["rate"], "eta": stats["eta"]})
            last, last_synced = now, synced
    return ZeroFillResult(written, total, clock() - start)
