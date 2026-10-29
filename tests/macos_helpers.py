"""Fixtures for macOS tests: real plists from a temporary disk image, plus synthetic layouts."""

import copy
import plistlib
from pathlib import Path

from fourmatter.core.runner import CommandResult

FIX = Path(__file__).parent / "fixtures" / "macos"


def load(name):
    return plistlib.loads((FIX / name).read_bytes())


def devno(path):
    n = int(path.removeprefix("/dev/disk").split("s")[0])
    return f"1:{n * 4}"


def usb_info(did="disk4", **kw):
    i = {"DeviceIdentifier": did, "DeviceNode": f"/dev/{did}", "Internal": False, "BusProtocol": "USB",
         "VirtualOrPhysical": "Physical", "WritableMedia": True, "Writable": True, "TotalSize": 64 * 10**9,
         "Size": 64 * 10**9, "MediaName": "SanDisk Ultra Media", "WholeDisk": True, "DeviceBlockSize": 512,
         "RemovableMediaOrExternalDevice": True, "Ejectable": True, "Content": "GUID_partition_scheme"}
    i.update(kw)
    return i


def fat_part(did="disk4s2", mount="/Volumes/TRAVEL", content="Microsoft Basic Data", name="TRAVEL"):
    return {"DeviceIdentifier": did, "Content": content, "Size": 63 * 10**9, "VolumeName": name,
            "MountPoint": mount, "VolumeUUID": "V-1"}


def external_list(parts=None, did="disk4", content="GUID_partition_scheme", extra=()):
    parts = parts if parts is not None else [fat_part()]
    e = {"DeviceIdentifier": did, "Content": content, "OSInternal": False, "Size": 64 * 10**9,
         "Partitions": parts}
    return {"AllDisksAndPartitions": [system_entry(), system_container(), e, *extra],
            "WholeDisks": [did]}


def system_entry():
    return {"DeviceIdentifier": "disk0", "Content": "GUID_partition_scheme", "OSInternal": False,
            "Size": 500 * 10**9, "Partitions": [
                {"DeviceIdentifier": "disk0s1", "Content": "Apple_APFS_ISC", "Size": 5 * 10**8},
                {"DeviceIdentifier": "disk0s2", "Content": "Apple_APFS", "Size": 494 * 10**9}]}


def system_container():
    return {"DeviceIdentifier": "disk3", "Content": "Apple_APFS_Container", "OSInternal": True,
            "Size": 494 * 10**9, "APFSPhysicalStores": [{"DeviceIdentifier": "disk0s2"}],
            "APFSVolumes": [{"DeviceIdentifier": "disk3s1", "VolumeName": "Macintosh HD",
                             "MountPoint": "/System/Volumes/Data"}]}


def system_infos():
    return {"disk0": usb_info("disk0", Internal=True, BusProtocol="Apple Fabric", Content="GUID_partition_scheme",
                              MediaName="APPLE SSD", RemovableMediaOrExternalDevice=False)}


def apfs_list(*containers):
    return {"Containers": list(containers)}


def apfs_container(ref, stores, volumes):
    return {"ContainerReference": ref, "PhysicalStores": [{"DeviceIdentifier": s} for s in stores],
            "Volumes": [{"DeviceIdentifier": f"{ref}s{i + 1}", "Name": n, "Roles": r}
                        for i, (n, r) in enumerate(volumes)]}


class MacRunner:
    """Answers diskutil/tmutil/ioreg queries from a dict; records every call."""
    dry_run = False

    def __init__(self, answers=None, fail=None):
        self.answers = answers or {}
        self.fail = fail or {}
        self.calls = []

    def run(self, argv, *, mutating=False, input=None, timeout=None):
        self.calls.append((list(argv), mutating))
        key = tuple(argv)
        for k, (rc, err) in self.fail.items():
            if key[: len(k)] == k:
                return CommandResult(rc, "", err)
        if key in self.answers:
            v = self.answers[key]
            return CommandResult(0, plistlib.dumps(v).decode() if not isinstance(v, str) else v, "")
        return CommandResult(1, "", f"no fixture for {' '.join(argv)}")

    @property
    def mutating(self):
        return [c[0] for c in self.calls if c[1]]
