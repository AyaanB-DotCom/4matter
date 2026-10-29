import errno
import os
import threading

import pytest

from fourmatter.core import zero_fill as Z


def make_img(tmp_path, size):
    p = tmp_path / "disk.img"
    p.write_bytes(os.urandom(size))
    return str(p)


def run(path, size, **kw):
    """Engine-level run over a temp file (no device, so no token is involved)."""
    events, cancel = [], kw.pop("cancel", threading.Event())
    fd = os.open(path, os.O_WRONLY)
    try:
        total = os.lseek(fd, 0, os.SEEK_END)
        if total != size:
            raise Z.ZeroFillError(f"capacity mismatch: device reports {total} bytes, expected {size}", 0)
        os.lseek(fd, 0, os.SEEK_SET)
        res = Z.write_zeros(fd, total, events.append, cancel, **kw)
    finally:
        os.close(fd)
    return res, events


def test_full_overwrite_with_partial_final_chunk(tmp_path):
    size = 5 * 1024 * 1024 + 1234 * 512  # not a chunk multiple
    path = make_img(tmp_path, size)
    res, ev = run(path, size, chunk=1024 * 1024, sync_every=2 * 1024 * 1024)
    data = open(path, "rb").read()
    assert len(data) == size and data == bytes(size)
    assert res.written == res.total == size and not res.cancelled
    written = [e["written"] for e in ev]
    assert written == sorted(written) and written[-1] == size  # monotonic, ends exactly at total
    assert all(e["total"] == size for e in ev)


def test_cancel_stops_early_and_reports_only_synced_bytes(tmp_path):
    size = 8 * 1024 * 1024
    path = make_img(tmp_path, size)
    cancel = threading.Event()
    events = []

    def emit(e):
        events.append(e)
        cancel.set()  # cancel right after the first progress report

    fd = os.open(path, os.O_WRONLY)
    res = Z.write_zeros(fd, size, emit, cancel, chunk=1024 * 1024, sync_every=2 * 1024 * 1024)
    os.close(fd)
    assert res.cancelled and 0 < res.written < size
    assert res.written == events[0]["written"]
    assert open(path, "rb").read() != bytes(size)  # tail untouched


def test_write_error_reports_confirmed_bytes(tmp_path, monkeypatch):
    size = 4 * 1024 * 1024
    path = make_img(tmp_path, size)
    real, n = os.write, {"n": 0}

    def flaky(fd, data):
        n["n"] += 1
        if n["n"] == 3:
            raise OSError(errno.EIO, "Input/output error")
        return real(fd, data)

    monkeypatch.setattr(Z.os, "write", flaky)
    with pytest.raises(Z.ZeroFillError, match="Input/output error") as ei:
        run(path, size, chunk=512 * 1024, sync_every=1024 * 1024)
    assert ei.value.written <= 1024 * 1024  # only fsync'd data is claimed


def test_short_writes_are_completed(tmp_path, monkeypatch):
    size = 1024 * 1024
    path = make_img(tmp_path, size)
    real = os.write
    monkeypatch.setattr(Z.os, "write", lambda fd, d: real(fd, bytes(d[:4096])))
    res, _ = run(path, size, chunk=256 * 1024, sync_every=512 * 1024)
    assert res.written == size and open(path, "rb").read() == bytes(size)


def test_capacity_mismatch_refused_before_writing(tmp_path):
    path = make_img(tmp_path, 4096)
    before = open(path, "rb").read()
    with pytest.raises(Z.ZeroFillError, match="mismatch"):
        run(path, 8192)
    assert open(path, "rb").read() == before


def test_dry_run_never_opens_for_writing(tmp_path, monkeypatch, usb):
    from conftest import make_token
    tok = make_token(usb)
    monkeypatch.setattr(Z.os, "open", lambda *a, **k: pytest.fail("os.open called"))
    with pytest.raises(Z.DryRunViolation):
        Z.zero_fill(usb.path, usb.size, lambda e: None, threading.Event(), token=tok, dry_run=True)


def test_opener_uses_exclusive_write(monkeypatch):
    seen = {}
    monkeypatch.setattr(Z.os, "open", lambda p, flags: seen.update(flags=flags) or 3)
    Z.open_block_device("/dev/sdb")
    assert seen["flags"] & os.O_EXCL and seen["flags"] & os.O_WRONLY


# ---- the guard: zero-fill cannot be called around validation --------------------------------------
def test_zero_fill_without_a_token_is_refused_and_opens_nothing(monkeypatch):
    monkeypatch.setattr(Z.os, "open", lambda *a, **k: pytest.fail("device opened without authorization"))
    for bad in (None, "token", object()):
        with pytest.raises(Z.UnauthorizedOperation):
            Z.zero_fill("/dev/sda", 10**9, lambda e: None, threading.Event(), token=bad)


def test_zero_fill_refuses_another_device_or_capacity(usb, monkeypatch):
    from conftest import make_token
    tok = make_token(usb)  # authorizes /dev/sdb only
    monkeypatch.setattr(Z.os, "open", lambda *a, **k: pytest.fail("device opened"))
    with pytest.raises(Z.UnauthorizedOperation, match="not the validated target"):
        Z.zero_fill("/dev/sda", usb.size, lambda e: None, threading.Event(), token=tok)
    with pytest.raises(Z.UnauthorizedOperation, match="not the validated target"):
        Z.zero_fill("/dev/sdb1", usb.size, lambda e: None, threading.Event(), token=tok)
    with pytest.raises(Z.UnauthorizedOperation, match="capacity"):
        Z.zero_fill("/dev/sdb", usb.size + 512, lambda e: None, threading.Event(), token=tok)


def test_zero_fill_refuses_a_stale_token(usb, monkeypatch):
    from conftest import make_token
    from fourmatter.core import guard
    tok = make_token(usb)
    monkeypatch.setattr(guard.time, "monotonic", lambda: tok.issued + guard.TOKEN_TTL + 1)
    with pytest.raises(Z.UnauthorizedOperation, match="expired"):
        Z.zero_fill("/dev/sdb", usb.size, lambda e: None, threading.Event(), token=tok)


def test_forged_token_is_refused(usb):
    import dataclasses
    from conftest import make_token
    tok = make_token(usb)
    forged = dataclasses.replace(tok, seal=object())
    with pytest.raises(Z.UnauthorizedOperation):
        Z.zero_fill("/dev/sdb", usb.size, lambda e: None, threading.Event(), token=forged)


def test_fd_must_be_the_validated_device(usb, tmp_path):
    """Even with a valid token, an fd that is not the validated block device is refused."""
    from conftest import make_token
    tok = make_token(usb)
    f = tmp_path / "decoy.img"
    f.write_bytes(bytes(usb.size if usb.size < 10**6 else 4096))
    opened = []
    with pytest.raises(Z.UnauthorizedOperation, match="not a block device"):
        Z.zero_fill("/dev/sdb", usb.size, lambda e: None, threading.Event(), token=tok,
                    opener=lambda p, dry_run=False: opened.append(os.open(str(f), os.O_WRONLY)) or opened[-1])
    assert f.read_bytes() == bytes(len(f.read_bytes()))  # nothing was written


def test_zero_fill_happy_path_with_injected_binding(usb, tmp_path):
    from conftest import make_token
    tok = make_token(usb)
    f = tmp_path / "img"
    f.write_bytes(os.urandom(8192))
    small = Z.dataclasses.replace(tok, drive=Z.dataclasses.replace(tok.drive, size=8192)) if hasattr(Z, "dataclasses") else None
    import dataclasses
    small = dataclasses.replace(tok, drive=dataclasses.replace(tok.drive, size=8192))
    res = Z.zero_fill("/dev/sdb", 8192, lambda e: None, threading.Event(), token=small,
                      opener=lambda p, dry_run=False: os.open(str(f), os.O_WRONLY),
                      fd_check=lambda fd, t: None, chunk=4096, sync_every=4096)
    assert res.written == 8192 and f.read_bytes() == bytes(8192)


def test_progress_stats():
    s = Z.progress_stats(50, 100, 10)
    assert s == {"fraction": 0.5, "rate": 5.0, "eta": 10.0}
    assert Z.progress_stats(0, 100, 10)["rate"] is None
    assert Z.progress_stats(50, 100, 0.5)["eta"] is None  # too early to estimate
    assert Z.progress_stats(100, 100, 5)["eta"] is None and Z.progress_stats(5, 0, 1)["fraction"] == 0
