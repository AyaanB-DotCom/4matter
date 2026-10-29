"""The external-drive-only policy, tested at every layer: eligibility, the central guard, the runner,
zero-fill, both executors, the privileged helper and the UI. Everything uses fixtures and fakes; no
real disk is ever opened for writing."""

import asyncio
import dataclasses
import io
import json
import os
import threading
import types

import pytest
from conftest import FakeRunner, lsblk, make_token, parse_ext, usb_node
from macos_helpers import (apfs_container, apfs_list, devno, external_list, fat_part, system_container,
                           system_entry, system_infos, usb_info)

from fourmatter.backends import macos as M
from fourmatter.backends.linux import LinuxBackend
from fourmatter.core import devices, executor as E, guard
from fourmatter.core.models import FormatOptions, Identity, OperationRequest, SafetyError
from fourmatter.core.runner import CommandRunner, DryRunRunner
from fourmatter.core.safety import assess

GB = 10**9


@pytest.fixture(autouse=True)
def tools_present(monkeypatch):
    import fourmatter.core.formatting as Fm
    monkeypatch.setattr(Fm, "missing_core_tools", lambda: [])
    monkeypatch.setattr(Fm, "availability", lambda fs: type("A", (), {"ok": True})())


def screen_text(app):
    return "\n".join(s.text for s in app.screen._compositor.render_strips())


def disk(name="sda", tran="sata", rm=False, **kw):
    n = usb_node(name=name, tran=tran, rm=rm, **{"maj:min": "8:0", **kw})
    return parse_ext(lsblk(n))[0]


def internal(name="sda", tran="sata"):
    return disk(name, tran)


def req(d, mode="format", **o):
    return OperationRequest(mode, Identity.of(d), FormatOptions(**{"fs": "exfat", "label": "X", **o}))


def run_linux(d, request, *, drives=None, runner=None, protected=None):
    runner = runner or FakeRunner()
    ev = []
    res = E.run_operation(request, ev.append, threading.Event(), runner=runner, scan=lambda: drives or [d],
                          assess_fn=lambda x: assess(x, protected=protected), check_blockdev=False)
    return res, runner


# ============================ eligibility: what is and is not external ===================================
@pytest.mark.parametrize("name,tran", [("sda", "sata"), ("nvme0n1", "nvme"), ("sdz", "ata"), ("sdq", None),
                                       ("sdw", ""), ("sdy", "unknown"), ("mmcblk0", "mmc")])
def test_internal_and_unverified_transports_are_never_eligible(name, tran):
    s = assess(disk(name, tran))
    assert not s.eligible and "external" in s.reason.lower() or "USB" in s.reason


def test_usb_flash_drive_is_eligible(usb):
    assert assess(usb).eligible


def test_external_usb_ssd_is_eligible_even_when_not_flagged_removable():
    d = disk("sdb", "usb", rm=False)  # many USB SSDs/HDDs report RM=0
    assert not d.removable and d.external and assess(d).eligible


def test_usb_claim_without_sysfs_evidence_is_rejected(tmp_path):
    """lsblk says usb but sysfs shows no USB device above the disk => ambiguous => refused."""
    (tmp_path / "sys/block/sdb/device").mkdir(parents=True)
    runner = type("R", (), {"run": lambda self, argv, **k: type("X", (), {"returncode": 0, "stderr": "",
                            "stdout": json.dumps(lsblk(usb_node()))})()})()
    d = devices.scan_devices(runner, sysroot=str(tmp_path))[0]
    assert d.transport == "usb" and not d.external and not assess(d).eligible


def test_unverified_drive_object_is_ineligible_by_default():
    d = devices.parse_lsblk(lsblk(usb_node()))[0]  # nothing has verified it
    assert d.external is False and not assess(d).eligible


def test_virtual_loop_optical_zram_rejected(system_tree):
    for d in system_tree[1:3]:
        assert not assess(d).eligible
    assert not assess(disk("zram0", "usb", **{"maj:min": "253:0"})).eligible


# ============================ system storage: root, swap, device mapper, external boot drive =============
def test_linux_root_disk_rejected_even_if_it_is_external_usb():
    n = usb_node()
    n["children"][0]["mountpoints"] = ["/"]
    d = parse_ext(lsblk(n))[0]
    assert d.external and not assess(d).eligible and assess(d).protected


def test_external_usb_ssd_hosting_the_running_system_is_protected_via_mountinfo(tmp_path):
    proc, sysroot = tmp_path / "proc", tmp_path / "sys"
    (proc / "self").mkdir(parents=True)
    # root is dm-0 (253:0) -> sdb1 (8:17) on disk sdb (8:16)
    (proc / "self/mountinfo").write_text("36 1 253:0 / / rw - ext4 /dev/mapper/vg-root rw\n")
    (proc / "swaps").write_text("Filename Type Size Used Priority\n")
    dm = sysroot / "devices/virtual/block/dm-0"
    (dm / "slaves/sdb1").mkdir(parents=True)
    (dm / "slaves/sdb1/dev").write_text("8:17\n")
    part = sysroot / "devices/pci0/usb1/block/sdb/sdb1"
    part.mkdir(parents=True)
    (part / "partition").write_text("1")
    (part / "dev").write_text("8:17\n")
    (part.parent / "dev").write_text("8:16\n")
    (sysroot / "dev/block").mkdir(parents=True)
    (sysroot / "dev/block/253:0").symlink_to(dm)
    (sysroot / "dev/block/8:17").symlink_to(part)
    prot = devices.system_protected(sysroot=str(tmp_path), proc=str(proc))
    assert {"253:0", "8:17", "8:16"} <= set(prot)
    d = disk("sdb", "usb", **{"maj:min": "8:16"})  # no lsblk-visible stack at all
    s = assess(d, protected=prot)
    assert not s.eligible and "backs the running system" in s.reason and s.protected


def test_swap_device_protects_its_disk(tmp_path):
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "self/mountinfo").write_text("")
    (proc / "swaps").write_text("Filename Type Size Used Priority\n/dev/sdb2 partition 100 0 -2\n")
    prot = devices.system_protected(sysroot=str(tmp_path), proc=str(proc))
    assert prot[os.path.realpath("/dev/sdb2")] == "is in use as swap"
    n = usb_node()
    n["children"][0].update(path="/dev/sdb2", name="sdb2")
    assert not assess(parse_ext(lsblk(n))[0], protected=prot).eligible


def test_swap_mountpoint_in_lsblk_rejected():
    n = usb_node()
    n["children"][0]["mountpoints"] = ["[SWAP]"]
    assert "swap" in assess(parse_ext(lsblk(n))[0]).reason.lower()


def test_unreadable_mount_table_fails_closed(tmp_path, usb):
    prot = devices.system_protected(sysroot=str(tmp_path), proc=str(tmp_path / "nope"))
    s = assess(usb, protected=prot)
    assert not s.eligible and "mount table" in s.reason


def test_lvm_raid_dm_children_block_the_whole_disk():
    n = usb_node()
    n["children"][0]["children"] = [{"name": "md0", "type": "raid1", "mountpoints": [None]}]
    assert "stacked" in assess(parse_ext(lsblk(n))[0]).reason


def test_linux_backend_assess_reads_the_system_tables_every_time(monkeypatch, usb):
    calls = []
    monkeypatch.setattr(devices, "system_protected", lambda: calls.append(1) or {})
    b = LinuxBackend()
    b.assess(usb)
    b.assess(usb)
    assert len(calls) == 2


# ============================ macOS: protected storage ======================================================
def mac_parse(lst, infos=None, apfs=None, startup=frozenset({"disk0"}), **kw):
    return M.parse_macos(lst, {**system_infos(), **(infos or {})}, apfs, startup=set(startup) if startup is not None else None,
                         tm=set(), devno=devno, tm_marker=lambda mp: False, **kw)


def mac(drives, name):
    return next(d for d in drives if d.name == name)


def test_internal_apple_ssd_rejected_and_not_external():
    d = mac(mac_parse(external_list()), "disk0")
    assert not d.external and not M.MacOSBackend().assess(d).eligible and not M.MacOSBackend().listed(d)


def test_external_usb_ssd_with_removable_flag_false_is_still_eligible_on_mac():
    info = usb_info(Removable=False, RemovableMedia=False)  # RemovableMediaOrExternalDevice stays True
    d = mac(mac_parse(external_list(), {"disk4": info, "disk4s2": {"FilesystemType": "exfat"}}), "disk4")
    assert d.external and M.MacOSBackend().assess(d).eligible


@pytest.mark.parametrize("kw", [{"Internal": True}, {"BusProtocol": "PCI-Express"}, {"BusProtocol": "Apple Fabric"},
                                {"BusProtocol": "Secure Digital"}, {"VirtualOrPhysical": "Virtual"},
                                {"RemovableMediaOrExternalDevice": False}, {"OSInternalMedia": True},
                                {"BusProtocol": ""}])
def test_mac_disks_not_positively_external_are_rejected(kw):
    d = mac(mac_parse(external_list(), {"disk4": usb_info(**kw)}), "disk4")
    assert not d.external and not M.MacOSBackend().assess(d).eligible


def test_mac_external_boot_drive_rejected_by_actual_relationships_not_by_number():
    # the running system's APFS container is backed by external disk4 (booted from a USB SSD)
    d = mac(mac_parse(external_list(), {"disk4": usb_info()}, startup={"disk4"}), "disk4")
    assert d.external and not M.MacOSBackend().assess(d).eligible and M.MacOSBackend().assess(d).protected
    # ...and a low-numbered disk is NOT assumed internal or safe: only evidence counts
    lst = external_list(did="disk0")
    lst["AllDisksAndPartitions"] = [lst["AllDisksAndPartitions"][-1]]
    lst["AllDisksAndPartitions"][0]["Partitions"] = [fat_part("disk0s2")]
    low = M.parse_macos(lst, {"disk0": usb_info("disk0"), "disk0s2": {"FilesystemType": "exfat"}}, None,
                        startup={"disk9"}, tm=set(), devno=devno, tm_marker=lambda mp: False)
    assert M.MacOSBackend().assess(mac(low, "disk0")).eligible


def test_mac_protected_apfs_physical_store_rejected():
    cont = {"DeviceIdentifier": "disk5", "Content": "Apple_APFS_Container", "Size": 63 * GB,
            "APFSPhysicalStores": [{"DeviceIdentifier": "disk4s2"}], "APFSVolumes": [{"DeviceIdentifier": "disk5s1", "VolumeName": "Mac"}]}
    part = {"DeviceIdentifier": "disk4s2", "Content": "Apple_APFS", "Size": 63 * GB}
    apfs = apfs_list(apfs_container("disk5", ["disk4s2"], [("Mac", ["System"]), ("Preboot", ["Preboot"])]))
    d = mac(mac_parse(external_list([part], extra=[cont]), {"disk4": usb_info()}, apfs), "disk4")
    s = M.MacOSBackend().assess(d)
    assert not s.eligible and s.protected and "system or backup" in s.reason


def test_mac_unknown_startup_disk_rejects_even_external_drives():
    d = mac(mac_parse(external_list(), {"disk4": usb_info()}, startup=None), "disk4")
    assert not M.MacOSBackend().assess(d).eligible


# ============================ the guard: validation cannot be bypassed ======================================
def test_runner_refuses_mutating_commands_without_a_token(monkeypatch):
    import fourmatter.core.runner as R
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("process started"))
    for r in (CommandRunner(), DryRunRunner()):
        for argv in (["wipefs", "--all", "/dev/sda"], ["mkfs.ext4", "/dev/sda1"], ["diskutil", "eraseDisk", "APFS", "X", "GPT", "/dev/disk0"],
                     ["dd", "if=/dev/zero", "of=/dev/sda"], ["sfdisk", "/dev/sda"]):
            with pytest.raises(guard.UnauthorizedOperation):
                r.run(argv, mutating=True)
            with pytest.raises(guard.UnauthorizedOperation):
                r.run(argv, mutating=True, token="yes please")


def test_token_covers_only_its_own_target(usb):
    tok = make_token(usb)
    guard.check_argv(tok, ["wipefs", "--all", "/dev/sdb"])
    guard.check_argv(tok, ["wipefs", "--all", "/dev/sdb1"])
    for argv in (["wipefs", "--all", "/dev/sda"], ["wipefs", "--all", "/dev/sdb2"], ["wipefs", "--all", "/dev/sdb", "/dev/sda"],
                 ["wipefs", "--all", "/dev/../dev/sda"], ["wipefs", "--all"], ["umount", "/home"], ["umount", "/"],
                 ["sfdisk", "/dev/sdb1"], ["mkfs.ext4", "/dev/sdb"], ["mkfs.ext4", "/dev/sda1"],
                 ["dd", "of=/dev/sdb"], ["shred", "/dev/sdb"], ["diskutil", "zeroDisk", "/dev/sdb"],
                 ["diskutil", "eraseDisk", "APFS", "X", "GPT", "/dev/sdb1"], ["rm", "-rf", "/dev/sdb"]):
        with pytest.raises(guard.UnauthorizedOperation):
            guard.check_argv(tok, argv)


def test_expired_and_forged_tokens_are_refused(usb, monkeypatch):
    tok = make_token(usb)
    with pytest.raises(guard.UnauthorizedOperation, match="expired"):
        guard.check_argv(tok, ["wipefs", "--all", "/dev/sdb"], now=tok.issued + guard.TOKEN_TTL + 1)
    with pytest.raises(guard.UnauthorizedOperation):
        guard.check_argv(dataclasses.replace(tok, seal=None), ["wipefs", "--all", "/dev/sdb"])
    with pytest.raises(guard.UnauthorizedOperation):
        guard.check_argv(None, ["wipefs", "--all", "/dev/sdb"])


def test_authorize_refuses_internal_partition_and_missing_targets(system_tree, usb):
    nvme = system_tree[0]
    scan = lambda: system_tree  # noqa: E731
    with pytest.raises(SafetyError, match="not eligible"):
        guard.authorize(Identity.of(nvme), scan, assess, stage="t")
    for path in ("/dev/nvme0n1p1", "/dev/sdb1"):  # an internal partition, an external partition
        ident = dataclasses.replace(Identity.of(usb), path=path)
        with pytest.raises(SafetyError, match="partition"):
            guard.authorize(ident, scan, assess, stage="t")
    with pytest.raises(SafetyError, match="malformed"):
        guard.authorize(dataclasses.replace(Identity.of(usb), path="/dev/../etc/passwd"), scan, assess, stage="t")
    with pytest.raises(SafetyError, match="no longer present"):
        guard.authorize(dataclasses.replace(Identity.of(usb), path="/dev/sdz"), scan, assess, stage="t")


def test_sysfs_partition_check_is_used(usb, tmp_path):
    (tmp_path / "sys/class/block/sdb").mkdir(parents=True)
    (tmp_path / "sys/class/block/sdb/partition").write_text("1")
    with pytest.raises(SafetyError, match="partition"):
        guard.authorize(Identity.of(usb), lambda: [usb], assess, stage="t",
                        is_partition=lambda p: devices.is_partition_node(p, str(tmp_path)))


# ---- replacement / reconnection --------------------------------------------------------------------------
@pytest.mark.parametrize("change", [
    {"serial": "OTHER"}, {"model": "Cruzer"}, {"size": 1}, {"maj_min": "8:32"}, {"wwn": "0x1"},
    {"link": "/sys/devices/pci0/usb1/1-3/sdb"}, {"instance": "43"}, {"vendor": "Evil"}])
def test_any_identity_change_stops_execution_before_any_write(usb, change):
    swapped = dataclasses.replace(usb, **change)
    res, runner = run_linux(usb, req(usb), drives=[swapped])
    assert not res.ok and "identity changed" in res.message and not runner.mutating


def test_replugged_same_stick_needs_a_fresh_selection(usb):
    """Same path, serial, size - but a new connection (new diskseq / host number)."""
    replug = dataclasses.replace(usb, instance="99", link="/sys/devices/pci0/usb1/1-2/host9/sdb")
    res, runner = run_linux(usb, req(usb), drives=[replug])
    assert not res.ok and "instance" in res.details and not runner.mutating


def test_disconnect_then_a_different_disk_at_the_same_path_is_never_adopted(usb):
    other = dataclasses.replace(usb, serial="NEW", model="Other", instance="7")
    res, runner = run_linux(usb, req(usb), drives=[other])
    assert not res.ok and not runner.mutating


def test_authorization_is_rechecked_before_every_destructive_stage(usb):
    """The target disappears between two stages: the later stage must refuse."""
    seen = {"n": 0}

    def scan():
        seen["n"] += 1
        return [usb] if seen["n"] < 4 else []
    runner = FakeRunner()
    res = E.run_operation(req(usb), lambda e: None, threading.Event(), runner=runner, scan=scan,
                          assess_fn=assess, check_blockdev=False)
    assert not res.ok and "no longer present" in res.message
    assert len(runner.mutating) < 4 and seen["n"] >= 4


# ============================ the executors cannot be pointed at internal storage ===========================
def test_linux_executor_refuses_an_internal_disk_even_when_called_directly(system_tree):
    nvme = system_tree[0]
    res, runner = run_linux(nvme, req(nvme), drives=system_tree)
    assert not res.ok and "not eligible" in res.message and not runner.calls  # nothing ran, not even a probe write
    res, runner = run_linux(nvme, OperationRequest("zero", Identity.of(nvme), FormatOptions(fs=None)), drives=system_tree)
    assert not res.ok and not runner.mutating


def test_linux_executor_refuses_partition_paths_manually_supplied(system_tree, usb):
    for path in ("/dev/nvme0n1p1", "/dev/sdb1"):
        r = OperationRequest("format", dataclasses.replace(Identity.of(usb), path=path), FormatOptions("exfat"))
        res, runner = run_linux(usb, r, drives=system_tree)
        assert not res.ok and "partition" in res.message and not runner.mutating


def test_linux_executor_default_policy_is_the_full_linux_policy(monkeypatch, system_tree):
    """No assess_fn given: the executor falls back to LinuxBackend.assess, never to 'allow'."""
    monkeypatch.setattr(devices, "system_protected", lambda: {})
    nvme = system_tree[0]
    runner = FakeRunner()
    res = E.run_operation(req(nvme), lambda e: None, threading.Event(), runner=runner, scan=lambda: system_tree,
                          check_blockdev=False)
    assert not res.ok and not runner.mutating


def test_linux_executor_busy_mount_aborts_before_any_wipe(usb):
    n = usb_node()
    n["children"][0]["mountpoints"] = ["/run/media/u/T"]
    d = parse_ext(lsblk(n))[0]
    runner = FakeRunner(fail={"umount": (32, "target is busy")})
    res, _ = run_linux(d, req(d), runner=runner)
    assert not res.ok and "still in use" in res.message
    assert not [c for c in runner.mutating if c[0] in ("wipefs", "sfdisk") or c[0].startswith("mkfs")]


def test_linux_executor_mount_in_a_non_removable_place_is_refused():
    n = usb_node()
    n["children"][0]["mountpoints"] = ["/home/me/stick"]
    d = parse_ext(lsblk(n))[0]
    res, runner = run_linux(d, req(d))
    assert not res.ok and not runner.mutating


def test_mac_executor_refuses_internal_partition_and_protected_targets():
    drives = mac_parse(external_list(), {"disk4": usb_info(), "disk4s2": {"FilesystemType": "exfat"}})
    internal_disk, ext_disk = mac(drives, "disk0"), mac(drives, "disk4")
    backend = M.MacOSBackend()
    backend._fs = {k: M.Availability(True, None, "") for k in M.FS}
    for ident in (Identity.of(internal_disk), dataclasses.replace(Identity.of(ext_disk), path="/dev/disk4s2"),
                  dataclasses.replace(Identity.of(ext_disk), path="/dev/disk0s2")):
        runner = FakeRunner()
        for mode in ("format", "zero", "zero_format"):
            r = OperationRequest(mode, ident, FormatOptions(None if mode == "zero" else "exfat", "X", "gpt"))
            res = backend.run_operation(r, lambda e: None, threading.Event(), runner=runner, scan=lambda: drives,
                                        check_node=False)
            assert not res.ok and not runner.calls, (ident.path, mode, res.message)


def test_mac_raw_open_and_fd_binding(monkeypatch):
    from fourmatter.core.zero_fill import ZeroFillError
    for bad in ("/dev/disk0s1", "/dev/disk4s1", "/dev/rdisk4", "/dev/sda", "disk4"):
        monkeypatch.setattr(M.os, "open", lambda *a, **k: pytest.fail("opened"))
        with pytest.raises(ZeroFillError):
            M.open_raw_disk(bad)
    tok = types.SimpleNamespace(drive=types.SimpleNamespace(maj_min="1:20"))
    import stat as st
    real = os.fstat

    def fake(mode, rdev):
        return lambda fd: types.SimpleNamespace(st_mode=mode, st_rdev=rdev) if fd == 987654 else real(fd)
    monkeypatch.setattr(M.os, "fstat", fake(st.S_IFCHR, os.makedev(14, 20)))
    M.check_mac_fd(987654, tok)  # same unit number: fine
    monkeypatch.setattr(M.os, "fstat", fake(st.S_IFCHR, os.makedev(14, 0)))
    with pytest.raises(guard.UnauthorizedOperation):
        M.check_mac_fd(987654, tok)
    monkeypatch.setattr(M.os, "fstat", fake(st.S_IFREG, 0))
    with pytest.raises(guard.UnauthorizedOperation):
        M.check_mac_fd(987654, tok)


def test_linux_fd_binding(monkeypatch):
    import stat as st
    from fourmatter.core import zero_fill as Z
    tok = types.SimpleNamespace(drive=types.SimpleNamespace(maj_min="8:16"))
    real = os.fstat

    def fake(rdev):
        return lambda fd: types.SimpleNamespace(st_mode=st.S_IFBLK, st_rdev=rdev) if fd == 987654 else real(fd)
    monkeypatch.setattr(Z.os, "fstat", fake(os.makedev(8, 16)))
    Z.check_linux_fd(987654, tok)
    monkeypatch.setattr(Z.os, "fstat", fake(os.makedev(8, 0)))
    with pytest.raises(guard.UnauthorizedOperation):
        Z.check_linux_fd(987654, tok)


# ============================ the privileged helper verifies independently ===================================
def test_helper_rejects_an_internal_target_without_running_anything(monkeypatch, capsys, system_tree):
    from fourmatter.privilege import helper
    nvme = system_tree[0]
    request = json.dumps(req(nvme).to_dict()) + "\n"

    class FakeLinux(LinuxBackend):
        def scan(self, runner):
            return system_tree

        def assess(self, d):
            return assess(d)

    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.sys, "stdin", io.StringIO(request))
    monkeypatch.setattr("fourmatter.core.platform.get_backend", lambda name=None: FakeLinux())
    import fourmatter.core.runner as R
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("a command was executed"))
    assert helper.main() == 1
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result = next(e for e in out if e["event"] == "result")
    assert not result["ok"] and "not eligible" in result["message"]


def test_helper_ignores_a_ui_that_claims_the_drive_is_safe(monkeypatch, capsys, system_tree):
    """The request carries no eligibility field; extra keys claiming safety change nothing."""
    from fourmatter.privilege import helper
    nvme = system_tree[0]
    d = req(nvme).to_dict()
    d.update(eligible=True, force=True, allow_internal=True, safety={"eligible": True})

    class FakeLinux(LinuxBackend):
        def scan(self, runner):
            return system_tree

        def assess(self, x):
            return assess(x)

    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.sys, "stdin", io.StringIO(json.dumps(d) + "\n"))
    monkeypatch.setattr("fourmatter.core.platform.get_backend", lambda name=None: FakeLinux())
    import fourmatter.core.runner as R
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("a command was executed"))
    assert helper.main() == 1


# ============================ no override switches exist =======================================================
def test_no_cli_flag_can_relax_the_policy(capsys):
    from fourmatter import cli
    help_text = cli.build_parser().format_help().lower()
    for word in ("force", "unsafe", "internal", "override", "allow-"):
        assert word not in help_text
    for flag in ("--force", "--unsafe", "--allow-internal", "--allow-system", "--no-safety"):
        with pytest.raises(SystemExit) as e:
            cli.main([flag])
        assert e.value.code == 2


def test_no_override_parameter_on_the_public_entry_points():
    import inspect
    for fn in (E.run_operation, LinuxBackend.run_operation, M.MacOSBackend.run_operation, assess):
        names = " ".join(inspect.signature(fn).parameters).lower()
        for word in ("force", "unsafe", "allow_internal", "bypass", "skip"):
            assert word not in names


def test_every_mutating_call_site_goes_through_a_token():
    """Static audit: every `mutating=True` in the source passes a token."""
    import pathlib
    import re
    root = pathlib.Path(devices.__file__).parent.parent
    for f in root.rglob("*.py"):
        text = f.read_text()
        if f.name in ("runner.py", "guard.py"):
            continue
        for m in re.finditer(r"mutating=True", text):
            line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end())]
            assert "token=" in line, f"{f.name}: {line.strip()}"


# ============================ dry-run and demo stay non-destructive =============================================
def test_dry_run_still_validates_and_refuses_internal_targets(monkeypatch, system_tree):
    import fourmatter.core.runner as R
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("process started"))
    nvme = system_tree[0]
    runner = DryRunRunner()
    res = E.run_operation(req(nvme), lambda e: None, threading.Event(), runner=runner, scan=lambda: system_tree,
                          assess_fn=assess, check_blockdev=False)
    assert not res.ok and not runner.planned


# ============================ the UI ========================================================================
def ui(coro, backend, size=(100, 34)):
    from fourmatter.app import FourMatterApp

    async def go():
        app = FourMatterApp(backend)
        async with app.run_test(size=size) as p:
            await p.pause(0.4)
            await coro(app, p)
    asyncio.run(go())


def test_ui_with_only_internal_disks_shows_the_empty_notice_and_nothing_actionable(system_tree):
    from fourmatter.demo.fixtures import DemoBackend
    from fourmatter.screens.dashboard import DashboardScreen
    from fourmatter.widgets.drive_list import DriveList
    b = DemoBackend(os_name="linux")
    b.drives = [dataclasses.replace(system_tree[0], simulated=True), dataclasses.replace(system_tree[0], name="sdx", path="/dev/sdx")]

    async def t(app, p):
        assert not app.screen.query_one(DriveList).drives
        text = screen_text(app)
        assert "NO ELIGIBLE EXTERNAL DRIVES DETECTED" in text and "internal and system storage" in text.lower()
        for key in ("f", "w", "enter"):
            await p.press(key)
            await p.pause(0.1)
            assert isinstance(app.screen, DashboardScreen)
    ui(t, b)


def test_ui_protected_external_drive_cannot_reach_confirm_even_if_pushed_directly():
    from fourmatter.demo.fixtures import DemoBackend
    from fourmatter.screens.confirm import ConfirmScreen
    from fourmatter.screens.dashboard import DashboardScreen
    b = DemoBackend(os_name="linux")

    async def t(app, p):
        boot = next(d for d in b.scan() if d.name == "sdf")
        app.wizard.reset(boot)
        app.wizard.options.fs = "exfat"
        app.push_screen(ConfirmScreen())
        await p.pause(0.3)
        assert not isinstance(app.screen, ConfirmScreen) and isinstance(app.screen, DashboardScreen)
    ui(t, b)
