import copy
import dataclasses
import fcntl
import threading

import pytest
from macos_helpers import (MacRunner, apfs_container, apfs_list, devno, external_list, fat_part, load,
                           system_container, system_entry, system_infos, usb_info)

from fourmatter.backends import macos as M
from fourmatter.core.devices import ScanError
from fourmatter.core.filesystems import FS
from fourmatter.core.models import FormatOptions, Identity, OperationRequest, Partition
from fourmatter.core.runner import DryRunRunner
from fourmatter.core.safety import assess

STARTUP = {"disk0"}


def parse(lst, infos=None, apfs=None, startup=STARTUP, tm=frozenset(), **kw):
    infos = {**system_infos(), **(infos or {})}
    kw.setdefault("devno", devno)
    kw.setdefault("tm_marker", lambda mp: False)
    return M.parse_macos(lst, infos, apfs, startup=startup, tm=set(tm) if tm is not None else None, **kw)


def ext(drives, name="disk4"):
    return next(d for d in drives if d.name == name)


def good_world(**kw):
    return parse(external_list(), {"disk4": usb_info(), "disk4s2": {"FilesystemType": "exfat"}}, **kw)


# ---- real plists captured from a temporary disk image on macOS 27 ------------------------------
def real_image_drive(tag="fat32_gpt", **kw):
    infos = {"disk4": load(f"info_{tag}_disk4.plist"), "disk4s1": load(f"info_{tag}_disk4s1.plist")}
    kw.setdefault("startup", STARTUP)
    return M.parse_macos(load(f"list_{tag}.plist"), infos, None, tm=set(), devno=devno,
                         tm_marker=lambda mp: False, **kw)[0]


def test_real_fat32_gpt_image_parses():
    d = real_image_drive("fat32_gpt", allow_virtual=True)
    assert (d.name, d.path, d.table, d.transport, d.size) == ("disk4", "/dev/disk4", "gpt", "disk image", 134217728)
    p = d.partitions[0]
    assert (p.fstype, p.label, p.mountpoints) == ("vfat", "PROBE32", ("/Volumes/PROBE32",))
    assert d.blockers == () and not d.read_only


def test_real_mbr_image_and_virtual_disks_are_blocked_by_default():
    assert real_image_drive("fat32_mbr", allow_virtual=True).table == "dos"
    assert any("Virtual disk" in b for b in real_image_drive("fat32_gpt").blockers)


# ---- eligibility and protection ----------------------------------------------------------------------
def test_external_usb_is_eligible_and_mount_is_unmountable():
    d = ext(good_world())
    s = M.MacOSBackend().assess(d)
    assert s.eligible and [m.source for m in s.to_unmount] == ["/dev/disk4s2"]
    assert d.vendor is None and d.model == "SanDisk Ultra Media" and d.fstype == "exfat"


def test_internal_startup_disk_blocked_and_not_listed():
    drives = good_world()
    sysd = ext(drives, "disk0")
    s = M.MacOSBackend().assess(sysd)
    assert not s.eligible and "Internal disk" in s.reason and "startup disk" in s.reason
    assert not M.MacOSBackend().listed(sysd) and M.MacOSBackend().listed(ext(drives))


def test_external_disk_the_system_booted_from_is_blocked():
    d = ext(good_world(startup={"disk4"}))
    assert not M.MacOSBackend().assess(d).eligible and "running macOS" in M.MacOSBackend().assess(d).reason


def test_unknown_startup_disk_fails_closed_for_everything():
    for d in good_world(startup=None):
        assert not M.MacOSBackend().assess(d).eligible
    assert "Cannot determine the startup disk" in ext(good_world(startup=None)).blockers


def test_time_machine_destination_and_backup_marker_block():
    assert "Time Machine destination" in ext(good_world(tm={"disk4"})).blockers
    d = ext(good_world(tm_marker=lambda mp: mp == "/Volumes/TRAVEL"))
    assert any("Time Machine backups" in b for b in d.blockers)


def test_tm_check_unavailable_does_not_hide_the_disk():
    assert ext(good_world(tm=None)).blockers == ()


APFS_PART = {"DeviceIdentifier": "disk4s2", "Content": "Apple_APFS", "Size": 63 * 10**9}


def apfs_world(containers, volumes, volroles):
    cont = {"DeviceIdentifier": "disk5", "Content": "Apple_APFS_Container", "Size": 63 * 10**9,
            "APFSPhysicalStores": containers, "APFSVolumes": volumes}
    return parse(external_list([APFS_PART], extra=[cont]), {"disk4": usb_info()}, volroles)


def vol(i, name, mp=None):
    return {"DeviceIdentifier": f"disk5s{i}", "VolumeName": name, **({"MountPoint": mp} if mp else {})}


def test_apfs_data_container_is_fine_and_unmounts_volumes_not_the_store():
    d = ext(apfs_world([{"DeviceIdentifier": "disk4s2"}], [vol(1, "Work", "/Volumes/Work")],
                       apfs_list(apfs_container("disk5", ["disk4s2"], [("Work", [])]))))
    p = d.partitions[0]
    assert (p.fstype, p.label) == ("apfs", "Work") and d.blockers == ()
    s = M.MacOSBackend().assess(d)
    assert s.eligible and [m.source for m in s.to_unmount] == ["/dev/disk5s1"]


def test_bootable_macos_install_on_external_apfs_is_blocked():
    apfs = apfs_list(apfs_container("disk5", ["disk4s2"], [("Mac", ["System"]), ("Data", ["Data"]),
                                                          ("Preboot", ["Preboot"]), ("Recovery", ["Recovery"])]))
    d = ext(apfs_world([{"DeviceIdentifier": "disk4s2"}], [vol(1, "Mac")], apfs))
    b = " ".join(d.blockers)
    assert not M.MacOSBackend().assess(d).eligible and "Preboot" in b and "Recovery" in b


def test_apfs_backup_role_and_fusion_container_blocked():
    apfs = apfs_list(apfs_container("disk5", ["disk4s2"], [("TM", ["Backup"])]))
    assert any("Backup" in b for b in ext(apfs_world([{"DeviceIdentifier": "disk4s2"}], [vol(1, "TM")], apfs)).blockers)
    two = [{"DeviceIdentifier": "disk4s2"}, {"DeviceIdentifier": "disk6s2"}]
    assert any("spans several" in b for b in ext(apfs_world(two, [vol(1, "X")], apfs_list())).blockers)


@pytest.mark.parametrize("content", ["Apple_CoreStorage", "Apple_RAID", "Apple_Boot", "Apple_APFS_Recovery"])
def test_complex_storage_blocked(content):
    d = ext(parse(external_list([{**fat_part(), "Content": content}]), {"disk4": usb_info()}))
    assert not M.MacOSBackend().assess(d).eligible and content in " ".join(d.blockers)


@pytest.mark.parametrize("kw,frag", [
    ({"WritableMedia": False}, "read-only"), ({"BusProtocol": "PCI-Express"}, "Not USB"),
    ({"BusProtocol": "Secure Digital"}, "Not USB"), ({"Internal": True}, "Internal"),
    ({"VirtualOrPhysical": "Virtual", "BusProtocol": "Disk Image"}, "Virtual"),
    ({"Internal": None}, "internal")])
def test_disk_blockers(kw, frag):
    info = {k: v for k, v in usb_info(**kw).items() if v is not None}
    d = ext(parse(external_list(), {"disk4": info}))
    assert not M.MacOSBackend().assess(d).eligible and frag.lower() in " ".join(d.blockers + M.MacOSBackend().assess(d).blockers).lower()


def test_missing_info_fails_closed():
    d = ext(parse(external_list(), {}))
    assert not M.MacOSBackend().assess(d).eligible


def test_efi_partition_is_marked_and_ignored_by_verification():
    parts = [{"DeviceIdentifier": "disk4s1", "Content": "EFI", "Size": 209715200, "VolumeName": "EFI"},
             fat_part()]
    d = ext(parse(external_list(parts), {"disk4": usb_info(), "disk4s2": {"FilesystemType": "exfat"}}))
    assert [p.role for p in d.partitions] == ["efi", None]
    assert M.verify_result(d, "exfat", "gpt", "TRAVEL")[0]


def test_identity_includes_device_number_and_serial_from_ioreg():
    io = [{"idVendor": 0x781, "idProduct": 0x5581, "USB Vendor Name": "SanDisk", "USB Product Name": "Ultra",
           "USB Serial Number": "4C53", "IORegistryEntryChildren": [
               {"IORegistryEntryChildren": [{"BSD Name": "disk4", "IORegistryEntryChildren": [{"BSD Name": "disk4s2"}]}]}]},
          {"idVendor": 1, "idProduct": 2, "USB Serial Number": "OTHER"}]
    usb = M.parse_usb(io)
    assert usb["disk4"]["serial"] == "4C53" and usb["disk4s2"]["vendor"] == "SanDisk"
    d = ext(parse(external_list(), {"disk4": usb_info(), "disk4s2": {"FilesystemType": "exfat"}}, usb=usb))
    assert (d.vendor, d.model, d.serial, d.usb) == ("SanDisk", "Ultra", "4C53", "USB 0781:5581")
    assert Identity.of(d).maj_min == "1:16"


# ---- helpers ---------------------------------------------------------------------------------------------
def test_physical_wholes():
    assert M.physical_wholes({"APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}]}) == {"disk0"}
    assert M.physical_wholes({"ParentWholeDisk": "disk2"}) == {"disk2"}
    assert M.physical_wholes({"APFSContainerReference": "disk3"}) is None and M.physical_wholes({}) is None


def test_fstype_distinguishes_fat32_from_fat16():
    assert M.fstype_from_info({"FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT32"}) == "vfat"
    assert M.fstype_from_info({"FilesystemType": "msdos", "FilesystemName": "MS-DOS FAT16"}) == "fat16"
    assert M.fstype_from_info({"FilesystemType": "hfs"}) == "hfsplus" and M.fstype_from_info({}) is None


# ---- scan orchestration (command construction + failure handling) ------------------------------------------
def scan_answers(over=None):
    root = {"DeviceIdentifier": "disk3s1s1", "ParentWholeDisk": "disk3",
            "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}]}
    a = {("diskutil", "list", "-plist"): external_list(),
         ("diskutil", "info", "-plist", "disk0"): usb_info("disk0", Internal=True, BusProtocol="Apple Fabric"),
         ("diskutil", "info", "-plist", "disk4"): usb_info(),
         ("diskutil", "info", "-plist", "disk4s2"): {"FilesystemType": "exfat"},
         ("diskutil", "info", "-plist", "/"): root,
         ("diskutil", "info", "-plist", "/System/Volumes/Data"): root,
         ("diskutil", "apfs", "list", "-plist"): apfs_list(),
         ("tmutil", "destinationinfo", "-X"): {},
         ("ioreg", "-a", "-r", "-c", "IOUSBHostDevice"): []}
    a.update(over or {})
    return a


def test_scan_end_to_end_with_structured_commands(monkeypatch):
    monkeypatch.setattr(M, "_devno", devno)
    monkeypatch.setattr(M, "_has_tm_backups", lambda mp: False)
    r = MacRunner(scan_answers())
    drives = M.MacOSBackend().scan(r)
    assert {d.name for d in drives} == {"disk0", "disk4"}
    assert "Holds the running macOS system" in " ".join(ext(drives, "disk0").blockers)
    assert M.MacOSBackend().assess(ext(drives)).eligible
    assert not r.mutating and all(c[0][0] in ("diskutil", "tmutil", "ioreg") for c in r.calls)
    assert all("-plist" in c[0] or c[0][0] != "diskutil" for c in r.calls)


def test_scan_fails_closed_when_root_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(M, "_devno", devno)
    r = MacRunner(scan_answers(), fail={("diskutil", "info", "-plist", "/"): (1, "boom")})
    drives = M.MacOSBackend().scan(r)
    assert drives and all(not M.MacOSBackend().assess(d).eligible for d in drives)


def test_scan_errors_are_reported():
    with pytest.raises(ScanError):
        M.MacOSBackend().scan(MacRunner({}, fail={("diskutil", "list"): (1, "no")}))
    with pytest.raises(ScanError):
        M.MacOSBackend().scan(MacRunner({("diskutil", "list", "-plist"): "not a plist"}))


def test_scan_handles_time_machine_destination(monkeypatch):
    monkeypatch.setattr(M, "_devno", devno)
    monkeypatch.setattr(M, "_has_tm_backups", lambda mp: False)
    a = scan_answers({("tmutil", "destinationinfo", "-X"): {"Destinations": [{"MountPoint": "/Volumes/TRAVEL"}]},
                      ("diskutil", "info", "-plist", "/Volumes/TRAVEL"): {"ParentWholeDisk": "disk4"}})
    assert "Time Machine destination" in ext(M.MacOSBackend().scan(MacRunner(a))).blockers


# ---- capabilities -----------------------------------------------------------------------------------------------
def test_fs_support_from_real_listfilesystems():
    r = MacRunner({("diskutil", "listFilesystems", "-plist"): load("listFilesystems.plist")})
    b = M.MacOSBackend()
    import fourmatter.backends.macos as mod
    mod_runner = mod.CommandRunner
    try:
        mod.CommandRunner = lambda: r
        sup = b.fs_support()
    finally:
        mod.CommandRunner = mod_runner
    assert all(sup[k].ok for k in ("fat32", "exfat", "apfs", "hfsplus"))
    assert not sup["ntfs"].ok and "read" in sup["ntfs"].hint and "exFAT" in sup["ntfs"].hint
    assert not sup["ext4"].ok and "exFAT" in sup["ext4"].hint and not sup["btrfs"].ok


def test_fs_support_unqueryable_means_unavailable(monkeypatch):
    monkeypatch.setattr(M, "CommandRunner", lambda: MacRunner({}, fail={("diskutil",): (1, "x")}))
    assert not any(M.MacOSBackend().fs_support()[k].ok for k in ("fat32", "exfat", "apfs", "hfsplus"))


@pytest.mark.parametrize("fs,table,label,size,frag", [
    ("apfs", "mbr", "x", 64 * 10**9, "GPT"), ("hfsplus", "mbr", "x", 64 * 10**9, "GPT"),
    ("ntfs", "gpt", "", 64 * 10**9, "NTFS"), ("ext4", "gpt", "", 64 * 10**9, "ext4"),
    ("fat32", "gpt", "A" * 12, 64 * 10**9, "limited"), ("exfat", "mbr", "", 3 * 1024**4, "2 TiB"),
    ("apfs", "gpt", "a:b", 64 * 10**9, "':'"), ("fat32", "gpt", "", 10**6, "at least"),
    ("zfs", "gpt", "", 10**9, "Unknown"), ("exfat", "apm", "", 10**9, "scheme")])
def test_validate_config_rejections(fs, table, label, size, frag):
    errs = M.MacOSBackend().validate_config(size, fs, table, label)
    assert errs and any(frag in e for e in errs), errs


@pytest.mark.parametrize("fs,table", [("fat32", "mbr"), ("fat32", "gpt"), ("exfat", "gpt"), ("exfat", "mbr"),
                                       ("apfs", "gpt"), ("hfsplus", "gpt")])
def test_valid_combinations(fs, table):
    assert M.MacOSBackend().validate_config(64 * 10**9, fs, table, "OK") == []


# ---- plan / command construction -----------------------------------------------------------------------------
def backend():
    """A MacOSBackend whose supported-format list is preloaded (no diskutil call)."""
    b = M.MacOSBackend()
    b._fs = {k: M.Availability(k in M.MAC_FS, None, "" if k in M.MAC_FS else M.UNSUPPORTED_REASON.get(k, "")) for k in FS}
    return b


def req(d, mode="format", **o):
    return OperationRequest(mode, Identity.of(d), FormatOptions(**{"fs": "fat32", "label": "usb", "table": "mbr", **o}))


def plan_for(d, request, **kw):
    b = M.MacOSBackend()
    return b.build_plan(d, request, b.assess(d), fs_ok={k: True for k in FS}, core_missing=[], **kw)


@pytest.mark.parametrize("fs,table,fmt,label", [("fat32", "mbr", "FAT32", "USB"), ("exfat", "gpt", "ExFAT", "usb"),
                                                 ("hfsplus", "gpt", "JHFS+", "usb"), ("apfs", "gpt", "APFS", "usb")])
def test_erase_disk_commands(fs, table, fmt, label):
    d = ext(good_world())
    p = plan_for(d, req(d, fs=fs, table=table))
    assert p.ok, p.errors
    erase = next(s for s in p.steps if s.key == "erase")
    assert erase.argv == ("diskutil", "eraseDisk", fmt, label, table.upper(), "/dev/disk4")
    assert [s.key for s in p.steps] == ["revalidate", "unmount", "erase", "verify"]
    assert p.steps[1].argv == ("diskutil", "unmount", "/dev/disk4s2")
    assert "force" not in " ".join(" ".join(s.argv or ()) for s in p.steps)


def test_default_name_and_zero_plans():
    d = ext(good_world())
    p = plan_for(d, req(d, label=""))
    assert next(s for s in p.steps if s.key == "erase").argv[3] == "UNTITLED" and any("UNTITLED" in w for w in p.warnings)
    z = plan_for(d, OperationRequest("zero", Identity.of(d), FormatOptions(fs=None)))
    assert [s.key for s in z.steps] == ["revalidate", "unmount", "zero"] and z.ok
    zf = plan_for(d, req(d, "zero_format", zero_fill=True))
    assert [s.key for s in zf.steps] == ["revalidate", "unmount", "zero", "erase", "verify"]


def test_plan_rejects_unsupported_and_ineligible():
    d = ext(good_world())
    p = plan_for(d, req(d, fs="ntfs", table="gpt"))
    assert not p.ok and "natively" in " ".join(p.errors)
    sysd = ext(good_world(), "disk0")
    assert not plan_for(sysd, req(sysd)).ok
    b = M.MacOSBackend()
    assert not b.build_plan(d, req(d), b.assess(d), fs_ok={"fat32": False}, core_missing=[]).ok
    assert not b.build_plan(d, req(d), b.assess(d), fs_ok={"fat32": True}, core_missing=["diskutil"]).ok


# ---- execution with a fake runner ------------------------------------------------------------------------------------
def world_after(runner, *, fs="exfat", label="TRAVEL", table="gpt", fstype_override=None, mounted=True):
    """scan() that follows what the fake runner has been asked to do."""
    def scan():
        cmds = [c[0] for c in runner.calls if c[1]]
        unmounted = any(c[:2] == ["diskutil", "unmount"] for c in cmds)
        erased = any(c[:2] == ["diskutil", "eraseDisk"] for c in cmds)
        if erased:
            parts = [{"DeviceIdentifier": "disk4s1", "Content": "EFI", "Size": 2 * 10**8, "VolumeName": "EFI"},
                     fat_part("disk4s2", f"/Volumes/{label}", name=label)]
            infos = {"disk4": usb_info(), "disk4s2": {"FilesystemType": fstype_override or fs}}
            tbl = "GUID_partition_scheme" if table == "gpt" else "FDisk_partition_scheme"
            lst = external_list(parts, content=tbl)
        else:
            lst = external_list([fat_part(mount=None if (unmounted or not mounted) else "/Volumes/TRAVEL")])
            infos = {"disk4": usb_info(), "disk4s2": {"FilesystemType": "exfat"}}
        return [d for d in parse(lst, infos) if d.name == "disk4"] or []
    return scan


def operate(request, runner, scan, cancel=None, **kw):
    events = []
    res = backend().run_operation(request, events.append, cancel or threading.Event(), runner=runner,
                                  scan=scan, check_node=False, **kw)
    return res, events


class Runner(MacRunner):
    def run(self, argv, *, mutating=False, input=None, timeout=None, token=None):
        if mutating:
            from fourmatter.core.guard import check_argv
            check_argv(token, argv)
        self.calls.append((list(argv), mutating))
        for k, (rc, err) in self.fail.items():
            if tuple(argv[: len(k)]) == k:
                from fourmatter.core.runner import CommandResult
                return CommandResult(rc, "", err)
        from fourmatter.core.runner import CommandResult
        return CommandResult(0, "", "")


@pytest.fixture
def patch_tools(monkeypatch):
    monkeypatch.setattr(M, "find_tool", lambda n: "/usr/sbin/" + n)


def start_world(**kw):
    r = Runner()
    scan = world_after(r, **kw)
    base = scan()[0]
    return r, scan, base


def test_format_flow_unmounts_erases_and_verifies(patch_tools):
    r, scan, base = start_world(fs="exfat")
    res, ev = operate(req(base, fs="exfat", table="gpt", label="TRAVEL"), r, scan)
    assert res.ok, res.message
    assert r.mutating == [["diskutil", "unmount", "/dev/disk4s2"],
                          ["diskutil", "eraseDisk", "ExFAT", "TRAVEL", "GPT", "/dev/disk4"]]
    assert res.verified["filesystem"] == "exfat" and res.verified["partition"] == "/dev/disk4s2"
    assert [e["key"] for e in ev if e["event"] == "stage"] == ["revalidate", "unmount", "erase", "verify"]


def test_fat32_alias_is_verified_not_assumed(patch_tools):
    r, scan, base = start_world(fs="fat32", fstype_override="fat16", label="USB", table="mbr")
    res, _ = operate(req(base, fs="fat32", table="mbr", label="usb"), r, scan)
    assert not res.ok and "verification failed" in res.message and "fat16" in res.message


def test_busy_volume_aborts_before_erasing_and_never_forces(patch_tools):
    r = Runner(fail={("diskutil", "unmount"): (1, "Volume TRAVEL failed to unmount: dissented by PID 412 (mds)")})
    scan = world_after(r)
    base = scan()[0]
    res, _ = operate(req(base, fs="exfat", table="gpt"), r, scan)
    assert not res.ok and "still in use" in res.message
    assert not any(c[:2] == ["diskutil", "eraseDisk"] for c in r.mutating)
    assert not any("force" in " ".join(c) for c in r.mutating)


def test_permission_failure_is_explained(patch_tools):
    r = Runner(fail={("diskutil", "eraseDisk"): (1, "Error: -69888: Couldn't open device. Ownership of the affected disk is required")})
    scan = world_after(r)
    base = scan()[0]
    res, _ = operate(req(base, fs="exfat", table="gpt"), r, scan)
    assert not res.ok and "permission" in res.message and "Erasing" in res.message and res.state


def test_identity_change_and_disconnect_abort_without_writes(patch_tools):
    r, scan, base = start_world()
    swapped = dataclasses.replace(base, serial="OTHER")
    res, _ = operate(req(base, fs="exfat", table="gpt"), r, lambda: [swapped])
    assert not res.ok and "identity changed" in res.message and not r.mutating
    res, _ = operate(req(base, fs="exfat", table="gpt"), Runner(), lambda: [])
    assert not res.ok and "no longer present" in res.message


def test_startup_disk_is_refused_by_the_executor(patch_tools):
    d = ext(good_world(startup={"disk4"}))
    r = Runner()
    res, _ = operate(req(d, fs="exfat", table="gpt"), r, lambda: [d])
    assert not res.ok and "not eligible" in res.message and not r.mutating


def test_cancel_before_erase(patch_tools):
    r, scan, base = start_world()
    cancel = threading.Event()
    events = []

    def emit(e):
        events.append(e)
        if e["event"] == "stage" and e["key"] == "unmount":
            cancel.set()  # a running step finishes; cancellation takes effect before the next one
    res = backend().run_operation(req(base, fs="exfat", table="gpt"), emit, cancel, runner=r, scan=scan,
                                  check_node=False)
    assert res.cancelled and not any(c[:2] == ["diskutil", "eraseDisk"] for c in r.mutating)


def test_zero_only_uses_raw_zero_fill_with_mac_primitives(monkeypatch, patch_tools):
    r, scan, base = start_world()
    got = {}

    def fake_zero(path, size, emit, cancel, **kw):
        got.update(path=path, size=size, opener=kw["opener"], size_fn=kw["size_fn"], sync_fn=kw["sync_fn"])
        from fourmatter.core.zero_fill import ZeroFillResult
        return ZeroFillResult(size, size, 1.0)
    monkeypatch.setattr(M, "zero_fill", fake_zero)
    res, _ = operate(OperationRequest("zero", Identity.of(base), FormatOptions(fs=None)), r, scan)
    assert res.ok and got["path"] == "/dev/disk4" and got["opener"] is M.open_raw_disk
    assert got["size_fn"] is M.raw_disk_size and got["sync_fn"] is M.full_sync
    assert ["diskutil", "unmountDisk", "/dev/disk4"] in r.mutating
    assert not any(c[:2] == ["diskutil", "eraseDisk"] for c in r.mutating)


def test_dry_run_never_executes_mutating_commands_or_opens_devices(monkeypatch, patch_tools):
    import subprocess

    import fourmatter.core.runner as R
    import fourmatter.core.zero_fill as Z
    ran = []
    monkeypatch.setattr(R.subprocess, "run", lambda argv, **k: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(Z.os, "open", lambda *a, **k: pytest.fail("device opened in dry-run"))
    monkeypatch.setattr(R, "find_tool", lambda n: "/usr/sbin/" + n)
    base = ext(good_world())
    for mode, o in (("format", {"fs": "exfat", "table": "gpt"}), ("zero_format", {"fs": "exfat", "table": "gpt", "zero_fill": True}),
                    ("zero", {"fs": None})):
        runner = DryRunRunner()
        res, _ = operate(req(base, mode, **o), runner, lambda: [base])
        assert res.ok and res.simulated, res.message
        assert runner.planned
    assert not any(a[1] in ("unmount", "unmountDisk", "eraseDisk", "zeroDisk") for a in ran if "diskutil" in a[0])


# ---- raw zero-fill primitives ----------------------------------------------------------------------------------------------
def test_raw_open_validates_path_and_dry_run(monkeypatch):
    from fourmatter.core.zero_fill import DryRunViolation, ZeroFillError
    monkeypatch.setattr(M.os, "open", lambda *a, **k: pytest.fail("opened"))
    with pytest.raises(DryRunViolation):
        M.open_raw_disk("/dev/disk4", dry_run=True)
    for bad in ("/dev/disk0s1", "/etc/passwd", "/dev/disk4/../disk0", "disk4", "/dev/rdisk4"):
        with pytest.raises(ZeroFillError):
            M.open_raw_disk(bad)


def test_raw_open_uses_the_raw_node_exclusively(monkeypatch):
    seen = {}
    monkeypatch.setattr(M.os, "open", lambda p, f: seen.update(p=p, f=f) or 9)
    assert M.open_raw_disk("/dev/disk4") == 9
    assert seen["p"] == "/dev/rdisk4" and seen["f"] & M.os.O_WRONLY
    assert seen["f"] & getattr(M.os, "O_EXLOCK", 0) == getattr(M.os, "O_EXLOCK", 0)


def test_raw_size_from_ioctls(monkeypatch):
    import struct

    def ioctl(fd, req_, buf):
        return struct.pack("I", 4096) if req_ == M.DKIOCGETBLOCKSIZE else struct.pack("Q", 1000)
    monkeypatch.setattr(M.fcntl, "ioctl", ioctl)
    assert M.raw_disk_size(3) == 4_096_000


def test_full_sync_falls_back_to_fsync(monkeypatch):
    called = []
    monkeypatch.setattr(M.fcntl, "fcntl", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    monkeypatch.setattr(M.os, "fsync", lambda fd: called.append(fd))
    M.full_sync(7)
    assert called == [7]


def test_zero_fill_engine_works_with_injected_mac_primitives(tmp_path):
    from fourmatter.core.zero_fill import write_zeros
    p = tmp_path / "img"
    p.write_bytes(b"\xff" * (3 * 1024 * 1024 + 512))
    synced = []
    fd = M.os.open(str(p), M.os.O_WRONLY)
    res = write_zeros(fd, p.stat().st_size, lambda e: None, threading.Event(), chunk=1024 * 1024,
                      sync_every=1024 * 1024, sync_fn=lambda f: synced.append(f))
    M.os.close(fd)
    assert res.written == res.total and p.read_bytes() == bytes(p.stat().st_size) and synced
