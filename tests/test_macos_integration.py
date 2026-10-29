"""Opt-in macOS integration tests against a TEMPORARY RAW DISK IMAGE that the test itself
creates and attaches (hdiutil). They never touch real USB hardware: the image is the only
erase target, and the fixture refuses to continue unless diskutil confirms the target is a
"Disk Image" backed by that exact file.

Enable with:  FOURMATTER_MACOS_IMAGE_TESTS=1 python -m pytest tests/test_macos_integration.py
Passing these tests does NOT validate physical USB hardware.
"""

import os
import plistlib
import re
import subprocess
import sys
import threading

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("FOURMATTER_MACOS_IMAGE_TESTS") != "1",
    reason="set FOURMATTER_MACOS_IMAGE_TESTS=1 on macOS to run (uses a temporary disk image)")

from fourmatter.backends.macos import MacOSBackend  # noqa: E402
from fourmatter.core.models import FormatOptions, Identity, OperationRequest  # noqa: E402
from fourmatter.core.runner import CommandRunner, DryRunRunner  # noqa: E402

SIZE = 128 * 1024 * 1024


def _plist(*argv):
    return plistlib.loads(subprocess.run(argv, capture_output=True, check=True).stdout)


@pytest.fixture
def image(tmp_path):
    img = tmp_path / "target.img"
    img.write_bytes(b"\xa5" * SIZE)
    out = subprocess.run(["hdiutil", "attach", "-nomount", "-imagekey", "diskimage-class=CRawDiskImage", str(img)],
                         capture_output=True, text=True, check=True).stdout
    dev = re.search(r"^(/dev/disk\d+)", out, re.M).group(1)
    try:
        info = _plist("diskutil", "info", "-plist", dev)
        ours = [i for i in _plist("hdiutil", "info", "-plist")["images"] if i.get("image-path") == str(img)
                and any(e.get("dev-entry") == dev for e in i["system-entities"])]
        assert info["BusProtocol"] == "Disk Image" and info["WholeDisk"] and ours, "refusing: not our image"
        yield dev, img
    finally:
        subprocess.run(["hdiutil", "detach", dev], capture_output=True)


def backend():
    return MacOSBackend(allow_disk_images=True)


def find(b, dev):
    return next(d for d in b.scan(CommandRunner()) if d.path == dev)


def operate(b, dev, mode, runner=None, **o):
    d = find(b, dev)
    req = OperationRequest(mode, Identity.of(d), FormatOptions(**{"fs": None if mode == "zero" else "exfat", **o}))
    events = []
    res = b.run_operation(req, events.append, threading.Event(), runner=runner or CommandRunner())
    return res, events


def test_image_is_discovered_and_eligible_only_when_images_are_allowed(image):
    dev, _ = image
    assert MacOSBackend().assess(find(MacOSBackend(allow_disk_images=True), dev)).eligible is False
    assert backend().assess(find(backend(), dev)).eligible


def test_real_system_disk_is_refused_by_real_checks():
    b = backend()
    internal = next(d for d in b.scan(CommandRunner()) if d.name == "disk0" or "Internal disk" in d.blockers)
    s = b.assess(internal)
    assert not s.eligible and "Internal disk" in s.reason
    req = OperationRequest("format", Identity.of(internal), FormatOptions("exfat", "X", "gpt"))
    res = b.run_operation(req, lambda e: None, threading.Event(), runner=DryRunRunner())
    assert not res.ok and "not eligible" in res.message


@pytest.mark.parametrize("fs,table,fsname", [("fat32", "mbr", "MS-DOS FAT32"), ("fat32", "gpt", "MS-DOS FAT32"),
                                              ("exfat", "gpt", "ExFAT"), ("hfsplus", "gpt", "Mac OS Extended (Journaled)"),
                                              ("apfs", "gpt", "APFS")])
def test_format_each_filesystem_and_verify_with_diskutil(image, fs, table, fsname):
    dev, _ = image
    b = backend()
    res, _ = operate(b, dev, "format", fs=fs, table=table, label="PROBE")
    assert res.ok, (res.message, res.details)
    part = next(p for p in find(b, dev).partitions if p.role != "efi")
    target = part.volumes[0].source if part.volumes else part.path  # APFS: inspect the volume, not the store
    info = _plist("diskutil", "info", "-plist", target.removeprefix("/dev/"))
    assert info["VolumeName"].upper() == "PROBE" and fsname in info["FilesystemUserVisibleName"] + info["FilesystemName"]


def test_zero_only_really_zeroes_every_byte_of_the_backing_file(image):
    dev, img = image
    res, ev = operate(backend(), dev, "zero")
    assert res.ok, (res.message, res.details)
    assert img.read_bytes() == bytes(SIZE)
    prog = [e for e in ev if e["event"] == "progress"]
    assert prog and prog[-1]["written"] == prog[-1]["total"] == SIZE


def test_zero_then_format(image):
    dev, img = image
    res, _ = operate(backend(), dev, "zero_format", fs="exfat", table="gpt", label="ZF", zero_fill=True)
    assert res.ok, (res.message, res.details) and res.verified["filesystem"] == "exfat"


def test_dry_run_changes_nothing(image):
    dev, img = image
    before = img.read_bytes()
    for mode, o in (("format", {"fs": "exfat"}), ("zero_format", {"fs": "exfat", "zero_fill": True}), ("zero", {})):
        res, _ = operate(backend(), dev, mode, runner=DryRunRunner(), **o)
        assert res.ok and res.simulated
    assert img.read_bytes() == before
