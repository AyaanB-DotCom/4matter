import json

import pytest

import dataclasses

from fourmatter.core.devices import parse_lsblk
from fourmatter.core.guard import check_argv

GB = 10**9


def parse_ext(data):
    """parse_lsblk plus what a real scan adds: USB disks get sysfs-verified external evidence."""
    out = []
    for d in parse_lsblk(data):
        if d.dtype == "disk" and d.transport == "usb":
            d = dataclasses.replace(d, external=True, usb="USB 3.0", link=f"/sys/devices/pci0/usb1/1-2/{d.name}",
                                    instance="42")
        out.append(d)
    return out


def usb_node(name="sdb", **kw):
    n = {"name": name, "path": f"/dev/{name}", "kname": name, "pkname": None, "maj:min": "8:16",
         "type": "disk", "size": 64 * GB, "model": "Ultra", "vendor": "SanDisk ", "serial": "4C53",
         "wwn": None, "tran": "usb", "rm": True, "hotplug": True, "ro": False, "fstype": None,
         "label": None, "uuid": None, "pttype": "gpt", "log-sec": 512, "mountpoints": [None],
         "children": [{"name": f"{name}1", "path": f"/dev/{name}1", "type": "part", "size": 63 * GB,
                       "maj:min": "8:17", "fstype": "exfat", "label": "TRAVEL", "uuid": "AAAA-BBBB",
                       "mountpoints": [None]}]}
    n.update(kw)
    return n


def lsblk(*nodes):
    return {"blockdevices": list(nodes)}


def make_token(drive, assess_fn=None, stage="test"):
    """A genuine token, minted the legitimate way (guard.authorize)."""
    from fourmatter.core import guard
    from fourmatter.core.models import Identity
    from fourmatter.core.safety import assess
    return guard.authorize(Identity.of(drive), lambda: [drive], assess_fn or assess, stage=stage)[0]


@pytest.fixture
def usb():
    return parse_ext(lsblk(usb_node()))[0]


@pytest.fixture
def system_tree():
    """nvme with ESP + LUKS -> LVM -> root and swap, plus loop, optical and a USB stick."""
    nvme = {"name": "nvme0n1", "path": "/dev/nvme0n1", "maj:min": "259:0", "type": "disk",
            "size": 500 * GB, "model": "Samsung SSD", "tran": "nvme", "rm": False, "ro": False,
            "pttype": "gpt", "mountpoints": [None], "children": [
                {"name": "nvme0n1p1", "type": "part", "size": GB, "fstype": "vfat",
                 "mountpoints": ["/boot/efi"], "maj:min": "259:1"},
                {"name": "nvme0n1p2", "type": "part", "size": 499 * GB, "fstype": "crypto_LUKS",
                 "mountpoints": [None], "maj:min": "259:2", "children": [
                     {"name": "cryptroot", "type": "crypt", "mountpoints": [None], "children": [
                         {"name": "vg-root", "type": "lvm", "mountpoints": ["/"]},
                         {"name": "vg-swap", "type": "lvm", "mountpoints": ["[SWAP]"]}]}]}]}
    loop = {"name": "loop0", "type": "loop", "size": 100, "maj:min": "7:0", "mountpoints": ["/snap/x"]}
    sr = {"name": "sr0", "type": "rom", "size": 0, "maj:min": "11:0", "tran": "sata", "mountpoints": [None]}
    return parse_ext(lsblk(nvme, loop, sr, usb_node()))


class FakeRunner:
    """Records argv; returns scripted results. Never starts a process."""
    dry_run = False

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}  # tool name -> (returncode, stderr)

    def run(self, argv, *, mutating=False, input=None, timeout=None, token=None):
        from fourmatter.core.runner import CommandResult
        if mutating:
            check_argv(token, argv)  # same gate as the real runner
        self.calls.append((list(argv), mutating, input))
        rc, err = self.fail.get(argv[0], (0, ""))
        return CommandResult(rc, "", err)

    @property
    def mutating(self):
        return [c[0] for c in self.calls if c[1]]
