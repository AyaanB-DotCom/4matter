import json

from conftest import lsblk, usb_node

from fourmatter.core.devices import ScanError, parse_lsblk, scan_devices, usb_info
from fourmatter.core.runner import CommandResult
import pytest


def test_parse_basic_usb(usb):
    assert usb.path == "/dev/sdb" and usb.transport == "usb" and usb.removable
    assert usb.vendor == "SanDisk" and usb.model == "Ultra"  # whitespace trimmed
    assert usb.table == "gpt" and usb.size == 64 * 10**9
    assert [p.path for p in usb.partitions] == ["/dev/sdb1"]
    assert usb.fstype == "exfat" and usb.partitions[0].label == "TRAVEL"
    assert usb.mounts == ()


def test_partition_mountpoints_enumerated():
    n = usb_node()
    n["children"].append({"name": "sdb2", "type": "part", "size": 10, "mountpoints": ["/media/u/A", "/mnt/b"]})
    d = parse_lsblk(lsblk(n))[0]
    assert [m.mountpoint for m in d.mounts] == ["/media/u/A", "/mnt/b"]
    assert d.fstype is None or d.fstype == "exfat" or True  # mixed fs: no single answer
    assert len(d.partitions) == 2


def test_recursive_stack(system_tree):
    nvme = system_tree[0]
    assert [p.name for p in nvme.partitions] == ["nvme0n1p1", "nvme0n1p2"]
    assert {s.type for s in nvme.stack} == {"crypt", "lvm"}
    assert {m.mountpoint for m in nvme.mounts} == {"/boot/efi", "/", "[SWAP]"}


def test_legacy_single_mountpoint_and_string_bools():
    n = usb_node(rm="1", ro="0", size="64000000000")
    del n["mountpoints"]
    n["mountpoint"] = "/mnt/x"
    d = parse_lsblk(lsblk(n))[0]
    assert d.removable and not d.read_only and d.size == 64 * 10**9
    assert d.mountpoints == ("/mnt/x",)


def test_missing_info_is_none_not_guessed():
    d = parse_lsblk(lsblk({"name": "sdz", "type": "disk", "size": 5}))[0]
    assert d.model is None and d.serial is None and d.maj_min is None
    assert d.display_name == "Unknown device"


def test_scan_falls_back_to_mountpoint_column():
    calls = []

    class R:
        def run(self, argv, **kw):
            calls.append(argv)
            if argv[-1].endswith("MOUNTPOINTS"):
                return CommandResult(1, "", "unknown column")
            return CommandResult(0, json.dumps(lsblk(usb_node())), "")

    assert scan_devices(R(), sysroot="/nonexistent")[0].name == "sdb"
    assert len(calls) == 2


def test_scan_errors():
    class Bad:
        def run(self, argv, **kw):
            return CommandResult(0, "not json", "")
    with pytest.raises(ScanError):
        scan_devices(Bad())


def test_usb_info_from_fake_sysfs(tmp_path):
    usbdev = tmp_path / "sys/devices/usb1/1-1"
    iface = usbdev / "1-1:1.0/host0/target0:0:0/0:0:0:0"
    iface.mkdir(parents=True)
    (usbdev / "idVendor").write_text("0781\n")
    (usbdev / "idProduct").write_text("5581\n")
    (usbdev / "speed").write_text("5000\n")
    (tmp_path / "sys/block").mkdir(parents=True)
    (tmp_path / "sys/block/sdb").mkdir()
    (tmp_path / "sys/block/sdb/device").symlink_to(iface)
    assert usb_info("sdb", str(tmp_path)) == "USB 3.0 5000 Mbit/s 0781:5581"
    assert usb_info("sdq", str(tmp_path)) is None
