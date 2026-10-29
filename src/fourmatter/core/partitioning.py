"""Partition-table choices and the sfdisk script for one whole-disk data partition."""

from __future__ import annotations

from .filesystems import FS

GPT_BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
GPT_LINUX_FS = "0FC63DAF-8483-4772-9300-A8F6E42FB4B5"
MBR_MAX_BYTES = 2 * 1024**4  # 2 TiB with 512-byte sectors
FAT32_MAX_BYTES = 2 * 1024**4


def partition_type(table: str, fs: str) -> str:
    if table == "gpt":
        return GPT_LINUX_FS if fs in ("ext4", "btrfs") else GPT_BASIC_DATA
    return {"fat32": "0c", "exfat": "07", "ntfs": "07"}.get(fs, "83")


def sfdisk_script(table: str, fs: str) -> str:
    """One partition, default (1 MiB aligned) start, spanning the rest of the disk."""
    if table not in ("gpt", "mbr"):
        raise ValueError(f"unknown partition table {table!r}")
    return f"label: {'gpt' if table == 'gpt' else 'dos'}\n,,{partition_type(table, fs)}\n"


def expected_pttype(table: str) -> str:
    return "gpt" if table == "gpt" else "dos"


def validate_config(size: int, fs: str | None, table: str, label: str, *,
                    need_fs: bool = True) -> list[str]:
    """Reject impossible combinations before the destructive confirmation step."""
    from .filesystems import validate_label
    errs: list[str] = []
    if table not in ("gpt", "mbr"):
        errs.append(f"Unknown partition table '{table}'")
    if not need_fs:
        return errs
    if fs not in FS:
        return errs + [f"Unknown filesystem '{fs}'"]
    if table == "mbr" and size > MBR_MAX_BYTES:
        errs.append("MBR cannot address more than 2 TiB; use GPT for this drive")
    if fs == "fat32" and size > FAT32_MAX_BYTES:
        errs.append("FAT32 is not practical above 2 TiB; choose exFAT or another filesystem")
    if (e := validate_label(fs, label)):
        errs.append(e)
    return errs
