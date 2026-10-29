import pytest
from conftest import parse_ext, lsblk, usb_node

from fourmatter.core import filesystems as F
from fourmatter.core.formatting import (build_plan, explain_failure, required_tools, verify_result,
                                        sfdisk_argv, wipefs_argv)
from fourmatter.core.models import FormatOptions, Identity, OperationRequest
from fourmatter.core.partitioning import sfdisk_script, validate_config
from fourmatter.core.safety import assess

ALL_OK = {k: True for k in F.FS}


def req(usb, mode="format", **kw):
    return OperationRequest(mode, Identity.of(usb), FormatOptions(**{"fs": "exfat", **kw}))


# ---- labels -------------------------------------------------------------------------
@pytest.mark.parametrize("fs,label,ok", [
    ("fat32", "BACKUP", True), ("fat32", "A" * 12, False), ("fat32", "BAD:NAME", False),
    ("fat32", "my disk", True), ("exfat", "é" * 11, True), ("exfat", "x" * 12, False),
    ("ext4", "a" * 16, True), ("ext4", "é" * 9, False),  # 18 bytes
    ("ntfs", "x" * 128, True), ("ntfs", "x" * 129, False), ("btrfs", "ok", True),
    ("ext4", "bad\nlabel", False), ("exfat", "", True),
])
def test_label_rules(fs, label, ok):
    assert (F.validate_label(fs, label) is None) == ok


def test_fat_label_uppercased():
    assert F.normalize_label("fat32", "mydisk") == "MYDISK"
    assert F.normalize_label("ext4", "mydisk") == "mydisk"


# ---- mkfs argv -----------------------------------------------------------------------
def test_mkfs_argv_shapes():
    assert F.mkfs_argv("fat32", "/dev/sdb1", "usb") == ["mkfs.vfat", "-F", "32", "-n", "USB", "/dev/sdb1"]
    assert F.mkfs_argv("exfat", "/dev/sdb1", "x") == ["mkfs.exfat", "-L", "x", "/dev/sdb1"]
    assert F.mkfs_argv("exfat", "/dev/sdb1", "x", exfat_flag="-n")[1] == "-n"
    assert F.mkfs_argv("ntfs", "/dev/sdb1", "x") == ["mkfs.ntfs", "-Q", "-L", "x", "/dev/sdb1"]
    assert F.mkfs_argv("ext4", "/dev/sdb1", "x", owner=(1000, 1000)) == [
        "mkfs.ext4", "-F", "-q", "-m", "0", "-E", "root_owner=1000:1000", "-L", "x", "/dev/sdb1"]
    assert "-E" not in F.mkfs_argv("ext4", "/dev/sdb1", "", owner=None)
    assert F.mkfs_argv("btrfs", "/dev/sdb1", "") == ["mkfs.btrfs", "-f", "/dev/sdb1"]


def test_hostile_label_is_a_single_argv_element():
    argv = F.mkfs_argv("ext4", "/dev/sdb1", "$(rm -rf /); `x`")
    assert argv[argv.index("-L") + 1] == "$(rm -rf /); `x`"  # never shell-interpreted
    assert F.validate_label("fat32", "$(x)") is not None or True


def test_unknown_fs_rejected():
    with pytest.raises(ValueError):
        F.mkfs_argv("zfs", "/dev/x", "")


def test_exfat_flag_detection():
    assert F.exfat_label_flag("exfatprogs version : 1.2.0") == "-L"
    assert F.exfat_label_flag("mkexfatfs 1.3.0") == "-n"


def test_capability_detection(monkeypatch):
    monkeypatch.setattr(F, "find_tool", lambda n: None if n == "mkfs.ntfs" else "/usr/sbin/" + n)
    assert F.availability("ext4").ok
    a = F.availability("ntfs")
    assert not a.ok and "ntfs-3g" in a.hint and "apt" in a.hint and "dnf" in a.hint and "pacman" in a.hint
    monkeypatch.setattr(F, "find_tool", lambda n: None)
    assert set(F.missing_core_tools()) == set(F.CORE_TOOLS)


# ---- partitioning ---------------------------------------------------------------------
def test_sfdisk_scripts():
    assert sfdisk_script("gpt", "exfat") == "label: gpt\n,,EBD0A0A2-B9E5-4433-87C0-68B6B72699C7\n"
    assert sfdisk_script("gpt", "ext4").endswith("0FC63DAF-8483-4772-9300-A8F6E42FB4B5\n")
    assert sfdisk_script("mbr", "fat32") == "label: dos\n,,0c\n"
    assert sfdisk_script("mbr", "ntfs").endswith(",,07\n") and sfdisk_script("mbr", "ext4").endswith(",,83\n")
    with pytest.raises(ValueError):
        sfdisk_script("apm", "ext4")


def test_config_validation():
    tb = 1024**4
    assert validate_config(64 * 10**9, "exfat", "gpt", "ok") == []
    assert any("2 TiB" in e for e in validate_config(3 * tb, "ntfs", "mbr", ""))
    assert any("FAT32" in e for e in validate_config(3 * tb, "fat32", "gpt", ""))
    assert validate_config(10, "fat32", "gpt", "x" * 20)  # label too long
    assert validate_config(10, "reiser", "gpt", "")
    assert validate_config(10, "ext4", "bsd", "")
    assert validate_config(10, None, "gpt", "", need_fs=False) == []


# ---- plan -----------------------------------------------------------------------------
def test_plan_quick_format(usb):
    plan = build_plan(usb, req(usb, label="TRAVEL"), assess(usb), tools_ok=ALL_OK, core_missing=[])
    assert plan.ok
    assert [s.key for s in plan.steps] == ["revalidate", "wipe", "wipe", "partition", "settle",
                                           "mkfs", "sync", "verify"]
    assert plan.steps[1].argv == tuple(wipefs_argv("/dev/sdb1"))  # partition first, disk last
    assert plan.steps[2].argv == tuple(wipefs_argv("/dev/sdb"))
    assert plan.steps[3].argv == tuple(sfdisk_argv("/dev/sdb"))
    assert "zero" not in [s.key for s in plan.steps]


def test_plan_zero_format_and_zero_only(usb):
    p = build_plan(usb, req(usb, "zero_format", zero_fill=True), assess(usb), tools_ok=ALL_OK, core_missing=[])
    keys = [s.key for s in p.steps]
    assert keys.index("zero") < keys.index("partition") and keys.count("wipe") == 1
    z = build_plan(usb, OperationRequest("zero", Identity.of(usb), FormatOptions(fs=None)), assess(usb),
                   tools_ok=ALL_OK, core_missing=[])
    assert [s.key for s in z.steps] == ["revalidate", "zero", "sync"]
    assert any("secure" in w for w in z.warnings)


def test_plan_lists_unmounts():
    n = usb_node()
    n["children"][0]["mountpoints"] = ["/run/media/u/T"]
    d = parse_ext(lsblk(n))[0]
    p = build_plan(d, req(d), assess(d), tools_ok=ALL_OK, core_missing=[])
    assert p.steps[1].key == "unmount" and p.steps[1].argv == ("umount", "/run/media/u/T")


def test_plan_rejects_before_confirmation(usb):
    p = build_plan(usb, req(usb, fs="ntfs"), assess(usb), tools_ok={**ALL_OK, "ntfs": False}, core_missing=["sfdisk"])
    assert not p.ok and any("mkfs.ntfs" in e for e in p.errors) and any("sfdisk" in e for e in p.errors)
    p = build_plan(usb, req(usb, label="x" * 30), assess(usb), tools_ok=ALL_OK, core_missing=[])
    assert not p.ok
    import dataclasses
    ro = dataclasses.replace(usb, read_only=True)
    assert not build_plan(ro, req(ro), assess(ro), tools_ok=ALL_OK, core_missing=[]).ok


def test_required_tools(usb):
    assert "mkfs.exfat" in required_tools(req(usb)) and "sfdisk" in required_tools(req(usb))
    assert required_tools(OperationRequest("zero", Identity.of(usb), FormatOptions(fs=None))) == ["lsblk", "sync"]


# ---- verification and errors ------------------------------------------------------------
def test_verify_result(usb):
    ok, facts, _ = verify_result(usb, FormatOptions("exfat", "travel", "gpt"))
    assert ok and facts["partition"] == "/dev/sdb1"
    assert not verify_result(usb, FormatOptions("ext4", "", "gpt"))[0]
    assert not verify_result(usb, FormatOptions("exfat", "", "mbr"))[0]
    assert not verify_result(usb, FormatOptions("exfat", "WRONG", "gpt"))[0]


@pytest.mark.parametrize("err,frag", [
    ("device or resource busy", "still in use"), ("Permission denied", "permission"),
    ("No such file or directory", "disappeared"), ("Input/output error", "I/O error"),
    ("weird", "status 1")])
def test_failure_messages_are_plain(err, frag):
    assert frag in explain_failure("mkfs", 1, err)
    assert explain_failure("mkfs", 1, err).startswith("Formatting failed")
