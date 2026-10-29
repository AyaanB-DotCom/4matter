import dataclasses
import threading

import pytest
from conftest import parse_ext, FakeRunner, lsblk, usb_node

from fourmatter.core import executor as E
from fourmatter.core.models import FormatOptions, Identity, OperationRequest, Partition
from fourmatter.core.runner import DryRunRunner
from fourmatter.core.safety import assess


@pytest.fixture(autouse=True)
def tools(monkeypatch):
    import fourmatter.core.formatting as Fm
    monkeypatch.setattr(Fm, "missing_core_tools", lambda: [])
    monkeypatch.setattr(Fm, "availability", lambda fs: type("A", (), {"ok": True})())


def make_world(runner, mounted=None, fs_after="exfat", label_after="TRAVEL"):
    """A scan() whose answer follows what the (fake) runner has been asked to do."""
    base = parse_ext(lsblk(usb_node()))[0]
    if mounted:
        base = dataclasses.replace(base, partitions=(dataclasses.replace(base.partitions[0], mountpoints=(mounted,)),))

    def scan():
        cmds = [c[0][0] for c in runner.calls]
        d = base
        if "umount" in cmds:
            d = dataclasses.replace(d, partitions=(dataclasses.replace(d.partitions[0], mountpoints=()),))
        if "sfdisk" in cmds:
            p = Partition("sdb1", "/dev/sdb1", 63 * 10**9, None, None, None, (), "8:17")
            if any(c.startswith("mkfs") for c in cmds):
                p = dataclasses.replace(p, fstype=fs_after, label=label_after)
            d = dataclasses.replace(d, partitions=(p,))
        return d and [d]
    return base, scan


def request(base, mode="format", **o):
    return OperationRequest(mode, Identity.of(base), FormatOptions(**{"fs": "exfat", "label": "TRAVEL", **o}), (1000, 1000))


def go(req, runner, scan, cancel=None, root_dev=None, **kw):
    events = []
    res = E.run_operation(req, events.append, cancel or threading.Event(), runner=runner, scan=scan,
                          assess_fn=lambda d: assess(d, root_dev=root_dev), check_blockdev=False, **kw)
    return res, events


def test_full_format_sequence_and_verification():
    r = FakeRunner()
    base, scan = make_world(r)
    res, ev = go(request(base), r, scan)
    assert res.ok, res.message
    names = [c[0][0] for c in r.calls if c[1]]
    assert names == ["wipefs", "wipefs", "sfdisk", "mkfs.exfat", "sync"]
    sf = next(c for c in r.calls if c[0][0] == "sfdisk")
    assert sf[2].startswith("label: gpt") and "--wipe-partitions" in sf[0]
    mk = next(c for c in r.calls if c[0][0] == "mkfs.exfat" and c[1])
    assert mk[0][-1] == "/dev/sdb1"  # partition path from structured discovery
    assert res.verified["filesystem"] == "exfat"
    assert [e["key"] for e in ev if e["event"] == "stage"][-1] == "verify"


def test_unmount_then_format_and_busy_unmount_aborts():
    r = FakeRunner()
    base, scan = make_world(r, mounted="/run/media/u/TRAVEL")
    res, _ = go(request(base), r, scan)
    assert res.ok and r.calls[[c[0][0] for c in r.calls].index("umount")][0] == ["umount", "/run/media/u/TRAVEL"]

    r = FakeRunner(fail={"umount": (32, "target is busy")})
    base, scan = make_world(r, mounted="/run/media/u/TRAVEL")
    res, _ = go(request(base), r, scan)
    assert not res.ok and "still in use" in res.message
    assert not [c for c in r.calls if c[0][0] in ("wipefs", "sfdisk")]  # nothing destructive ran
    assert "-f" not in r.calls[[c[0][0] for c in r.calls].index("umount")][0]


def test_identity_change_aborts_before_any_write():
    r = FakeRunner()
    base, scan = make_world(r)
    swapped = dataclasses.replace(base, serial="OTHER")
    res, _ = go(request(base), r, lambda: [swapped])
    assert not res.ok and "identity changed" in res.message and not r.mutating


def test_disconnect_midway_is_reported_with_state():
    r = FakeRunner()
    base, scan = make_world(r)
    calls = {"n": 0}

    def flaky_scan():
        calls["n"] += 1
        return scan() if calls["n"] < 3 else []
    res, _ = go(request(base), r, flaky_scan)
    assert not res.ok and "no longer present" in res.message and res.state


def test_partition_failure_message_and_state():
    r = FakeRunner(fail={"sfdisk": (1, "sfdisk: /dev/sdb: device or resource busy")})
    base, scan = make_world(r)
    res, _ = go(request(base), r, scan)
    assert not res.ok and res.message.startswith("Creating the partition table failed")
    assert "partition table" in res.state and "sfdisk" in res.details
    assert "mkfs.exfat" not in r.mutating


def test_mkfs_failure_not_rolled_back_claim():
    r = FakeRunner(fail={"mkfs.exfat": (1, "busy")})
    base, scan = make_world(r)
    res, _ = go(request(base), r, scan)
    assert not res.ok and "rolled back" not in res.state and "filesystem" in res.state


def test_verification_failure_reported():
    r = FakeRunner()
    base, scan = make_world(r, fs_after="vfat")
    res, _ = go(request(base), r, scan)
    assert not res.ok and "verification failed" in res.message


def test_missing_tool_reported():
    class Missing(FakeRunner):
        def run(self, argv, **kw):
            from fourmatter.core.runner import ToolMissing
            if argv[0] == "mkfs.exfat" and kw.get("mutating"):
                raise ToolMissing("mkfs.exfat")
            return super().run(argv, **kw)
    r = Missing()
    base, scan = make_world(r)
    res, _ = go(request(base), r, scan)
    assert not res.ok and "mkfs.exfat" in res.message


def test_cancel_between_steps():
    r = FakeRunner()
    base, scan = make_world(r)
    cancel = threading.Event()
    seen = []

    def emit(ev):
        seen.append(ev)
        if ev["event"] == "stage" and ev["key"] == "partition":
            cancel.set()
    res = E.run_operation(request(base), emit, cancel, runner=r, scan=scan,
                          assess_fn=assess, check_blockdev=False)
    assert res.cancelled and not res.ok and "mkfs.exfat" not in r.mutating


def test_zero_only_uses_zero_fill_and_never_formats(monkeypatch):
    r = FakeRunner()
    base, scan = make_world(r)
    got = {}

    def fake_zero(path, size, emit, cancel, **kw):
        got.update(path=path, size=size)
        emit({"event": "progress", "written": size, "total": size, "elapsed": 1, "rate": 1, "eta": 0})
        from fourmatter.core.zero_fill import ZeroFillResult
        return ZeroFillResult(size, size, 1.0)
    monkeypatch.setattr(E, "zero_fill", fake_zero)
    req = OperationRequest("zero", Identity.of(base), FormatOptions(fs=None))
    res, ev = go(req, r, scan)
    assert res.ok and got == {"path": "/dev/sdb", "size": base.size}
    assert not [c for c in r.calls if c[0][0] in ("sfdisk", "wipefs") or c[0][0].startswith("mkfs")]


def test_zero_interrupted_by_write_error(monkeypatch):
    from fourmatter.core.zero_fill import ZeroFillError
    r = FakeRunner()
    base, scan = make_world(r)

    def boom(*a, **k):
        raise ZeroFillError("write failed at byte 99: Input/output error", 64)
    monkeypatch.setattr(E, "zero_fill", boom)
    req = OperationRequest("zero_format", Identity.of(base), FormatOptions("exfat", "", "gpt", True))
    res, _ = go(req, r, scan)
    assert not res.ok and "Zero-fill interrupted" in res.message and "partly overwritten" in res.state
    assert "sfdisk" not in [c[0][0] for c in r.calls]


def test_unsafe_target_refused():
    r = FakeRunner()
    base, scan = make_world(r)
    res, _ = go(request(base), r, scan, root_dev="8:17")
    assert not res.ok and "root filesystem" in res.message and not r.mutating


# ---- dry run enforcement (backend level) -------------------------------------------------
def test_dry_run_runs_no_mutating_process_and_opens_no_device(monkeypatch):
    import subprocess

    import fourmatter.core.runner as R
    import fourmatter.core.zero_fill as Z
    ran = []
    monkeypatch.setattr(R.subprocess, "run", lambda argv, **k: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(Z.os, "open", lambda *a, **k: pytest.fail("device opened in dry-run"))
    monkeypatch.setattr(R, "find_tool", lambda n: "/bin/" + n)
    base = parse_ext(lsblk(usb_node()))[0]
    for mode, o in (("format", {}), ("zero_format", {"zero_fill": True}), ("zero", {"fs": None})):
        req = request(base, mode, **o)
        runner = DryRunRunner()
        res, ev = go(req, runner, lambda: [base])
        assert res.ok and res.simulated, res.message
        assert runner.planned  # recorded, not executed
        planned_names = {a[0] for a in runner.planned}
        assert planned_names & {"wipefs", "sfdisk", "sync"} or mode == "zero"
    executed = {a[0].rsplit("/", 1)[-1] for a in ran}
    assert not executed & {"wipefs", "sfdisk", "umount", "sync", "blockdev"}
    assert not any(x.startswith("mkfs") for x in executed)


def test_dry_run_runner_never_delegates_mutating(monkeypatch, usb):
    import fourmatter.core.runner as R
    from conftest import make_token
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("subprocess used"))
    r = DryRunRunner()
    assert r.run(["wipefs", "--all", "/dev/sdb"], mutating=True, token=make_token(usb)).returncode == 0
    assert r.planned == [["wipefs", "--all", "/dev/sdb"]]


def test_dry_run_backend_flags():
    b = E.DryRunBackend()
    assert b.writes is False and b.mode == "dry-run"
