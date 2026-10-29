# 4matter
A simple TUI to help you format

```
┌┴┐  ╻ ╻  ┏┳┓┏━┓╺┳╸╺┳╸┏━╸┏━┓
├◉┤  ┗━╋  ┃┃┃┣━┫ ┃  ┃ ┣╸ ┣┳┛    removable media utility
└┬┘    ╹  ╹ ╹╹ ╹ ╹  ╹ ┗━╸╹┗╸                linux / macos
```

<img width="3454" height="2234" alt="4matter-clean" src="https://github.com/user-attachments/assets/87963591-6a92-4e57-b9f1-612fd9dc16f1" />


**4matter** is a keyboard-driven terminal application for formatting, preparing and zero-filling USB
drives on **Linux and macOS**. You pick the drive, say what it is for, review exactly what will happen,
and confirm. No `lsblk`, `fdisk`, `mkfs`, `diskutil` or `dd` incantations to remember, and the same
interface on both systems.

**4matter only ever modifies verified external drives.** It is not a general disk-management tool:
internal disks, the system and boot disk, and anything it cannot positively identify as external physical
storage are refused at every layer, including inside the privileged helper. There is no switch to turn this off.
See [External drives only](#external-drives-only).

> **4matter destroys data.** Formatting and zero-filling permanently erase everything accessible on
> the target drive. Keep backups, and read [Safety](#8-safety-and-data-loss) and
> [Status and verification](#status-and-verification) before pointing it at a drive you care about.

It is written in Python with [Textual](https://textual.textualize.io/), works entirely offline, has no
telemetry, and its look is a deliberate pastiche of 1990s dungeon-crawler utilities: thin box frames,
a limited bone / moss / teal / amber / rust palette, and a plain-text emblem. A monochrome mode exists
for limited terminals.

<!-- Screenshots and demo GIFs: add them here once recorded (docs/ directory suggested). -->

## Contents

1. [Introduction](#1-introduction)
2. [Features](#2-features)
3. [Installation](#3-installation)
4. [How to use 4matter](#4-how-to-use-4matter)
5. [Filesystem guide](#5-filesystem-guide)
6. [Platform recommendations](#6-platform-recommendations)
7. [Zero-fill explained](#7-zero-fill-explained)
8. [Safety and data loss](#8-safety-and-data-loss)
9. [Keyboard shortcuts](#9-keyboard-shortcuts)
10. [Command-line reference](#10-command-line-reference)
11. [Technical architecture](#11-technical-architecture)
12. [Troubleshooting](#12-troubleshooting)
13. [Development and testing](#13-development-and-testing)
14. [Known limitations](#14-known-limitations)
15. [License](#15-license)

Also: [Status and verification](#status-and-verification) (what has and has not been tested).

---

## 1. Introduction

Preparing a USB drive normally means identifying the right `/dev/sdX` or `/dev/diskN`, unmounting its
volumes, writing a partition table, choosing the right tool and flags for the filesystem, and hoping you
did not mistype the device. One wrong character can wipe the wrong disk.

4matter replaces that with a guided workflow:

```
SELECT DRIVE ─ CHOOSE USE ─ CHOOSE FORMAT ─ REVIEW ─ CONFIRM ─ EXECUTE
```

It recognises the host operating system and uses that system's native disk tools behind one interface:
`lsblk`, `sfdisk`, `wipefs` and `mkfs.*` on Linux; `diskutil` (structured plist output) on macOS. It
identifies drives by more than their device name, refuses to touch the system disk, re-checks a drive's
identity immediately before every destructive step, and runs the disk-modifying work in a separate
privileged helper so the interface itself runs as your normal user.

### Supported operating systems

| System | Status |
|---|---|
| Linux (x86-64, arm64) | Implemented. Developed with mocks and simulated metadata; **never executed on a real Linux machine yet.** |
| macOS, Apple Silicon | Implemented. Tested on macOS 27.0.1 (build 26A434), arm64, Python 3.12: automated tests, real read-only discovery of this Mac's disks, and formatting / zero-fill against a temporary disk image. **Not tested with a physical USB drive.** |
| macOS, Intel | Implemented with the same code and tools; **not tested** on Intel hardware. |
| Other macOS versions | Not tested. APFS needs macOS 10.13 or later. The supported formats are detected at run time with `diskutil listFilesystems`. |
| Windows, BSD, others | Not supported: real mode prints a clear error. `4matter --demo` works anywhere Python 3.11+ and Textual run. |

## 2. Features

Everything in the "Implemented" list exists in the code and is covered by automated tests. See
[Status and verification](#status-and-verification) for exactly what was exercised on real systems.

**Implemented**

| Feature | What it does |
|---|---|
| Host detection | `platform.system()` selects the Linux or macOS backend. Unsupported systems get a clear message instead of a crash. |
| Drive discovery | Linux: `lsblk --json` plus `/sys` and `/dev/disk/by-id`. macOS: `diskutil list/info -plist`, `diskutil apfs list -plist`, `tmutil` and `ioreg`, all parsed with `plistlib`. Only verified external USB drives are listed; internal disks never appear. Refresh with **R**. |
| External-drive-only policy | Destructive operations are accepted only for positively verified external physical disks, enforced centrally (`core/guard.py`) and again in the privileged helper. Protected or unverifiable external drives are shown as `PROTECTED` / `UNAVAILABLE` and cannot reach the confirmation screen. There is no override option. |
| Drive inspection | Model, vendor, capacity, filesystem, partition scheme, partition layout bar, mount points, serial (where the OS reports one), transport, read-only flag, and an explicit safe/unsafe verdict with reasons. Missing data shows `Unknown`, never a guess. |
| Guided formatting | The wizard above. Every stage can go back with **Esc**. Nothing starts until the final confirmation. |
| Platform presets | General storage, Windows, macOS, Linux, Windows + macOS, PlayStation, Xbox, Nintendo Switch, Steam Deck / handheld PC, Custom. Each recommends a filesystem for the host OS and explains the trade-offs. |
| Filesystems | Linux: FAT32, exFAT, NTFS, ext4, Btrfs (optional). macOS: FAT32, exFAT, APFS, Mac OS Extended. Filesystems a system cannot create are shown as unavailable with the reason. |
| Partition scheme | GPT or MBR. Invalid combinations (MBR above 2 TiB, APFS or Mac OS Extended on MBR) are rejected before confirmation. |
| Volume label | Validated per filesystem (length, forbidden characters). FAT32 labels are stored upper case. |
| Zero-fill drive | Writes zeros across the whole logical capacity, sequentially, in 4 MiB blocks. |
| Zero-fill + format | Zero-fill, then a new partition table and filesystem. |
| Progress monitoring | Bar, percent, bytes written / total, elapsed time, write rate, ETA, current step and the last log lines. Progress counts only bytes that were flushed. Steps without a reliable percentage (partitioning, formatting) show an indeterminate indicator. |
| Safety confirmations | Full review screen; destructive buttons are never focused by default; zero-fill additionally requires typing the device name (for example `sdb` or `disk4`). |
| Demo mode | `4matter --demo`: fully interactive with clearly labelled simulated drives in the style of the host OS. It cannot reach the real executor. |
| Dry-run mode | `4matter --dry-run`: real drives, real validation, full execution plan; the backend records commands and never runs them or opens a device for writing. |
| Keyboard navigation | Arrows or `j`/`k`, Enter, Tab, Esc, single-letter shortcuts, plus mouse clicks on lists and buttons. |
| ASCII art panel | On terminals 120 columns or wider, the registry screen shows a "FRAGMENT" panel with the bundled ASCII art (`src/fourmatter/assets/art.txt`), scaled down to fit. Press `G` on the registry to view it full-screen at any size. |
| Reduced colour | `--mono`, or automatically with `NO_COLOR` / `TERM=dumb`. 256-colour terminals are handled by Textual's colour downsampling. |

**Not implemented / planned**

- NTFS and ext4 on macOS (no native support; see [Filesystem guide](#5-filesystem-guide)).
- APFS and Mac OS Extended on Linux.
- ReFS, console-proprietary layouts, ISO writing or bootable-media creation.
- LUKS / LVM / RAID aware handling on Linux, and Fusion / RAID / CoreStorage on macOS: such drives are refused.
- Privilege elevation other than `sudo`.
- Real USB-hardware and Linux integration tests.

## 3. Installation

You install 4matter **once**. After that, `4matter` is available from any terminal and any directory;
you do not activate a virtualenv, `cd` into the source tree, or use `sudo` to start the interface.
(Elevation is requested later, only when you confirm an operation.)

Both systems need **Python 3.11 or newer** and **pipx**, which keeps 4matter in its own isolated
environment (nothing is installed into the system-managed Python).

### Linux

Supported targets: Debian, Ubuntu, Fedora, Arch and derivatives. Python: Debian 12 and Fedora 38+ ship
a suitable version; Ubuntu 22.04 ships 3.10 and needs a newer interpreter (for example the deadsnakes PPA
or `uv python install 3.12`), then `pipx install --python python3.12 .`. You also need `sudo`.

Install pipx and the system tools (install only the filesystem tools you need):

```bash
# Debian / Ubuntu
sudo apt install pipx util-linux dosfstools exfatprogs ntfs-3g e2fsprogs
# Fedora
sudo dnf install pipx util-linux dosfstools exfatprogs ntfsprogs e2fsprogs
# Arch
sudo pacman -S python-pipx util-linux dosfstools exfatprogs ntfs-3g e2fsprogs
```

Optional Btrfs: `btrfs-progs`. `udevadm` comes with systemd/udev, which mainstream distributions already have.

| Tool | Package | Needed for |
|---|---|---|
| `lsblk`, `sfdisk`, `wipefs`, `umount`, `blockdev` | util-linux | everything |
| `udevadm` | systemd / udev | formatting |
| `mkfs.vfat` | dosfstools | FAT32 |
| `mkfs.exfat` | exfatprogs | exFAT |
| `mkfs.ntfs` | ntfs-3g (Fedora: ntfsprogs) | NTFS |
| `mkfs.ext4` | e2fsprogs | ext4 |
| `mkfs.btrfs` | btrfs-progs | Btrfs (optional) |

### macOS

No filesystem tools need installing: `diskutil`, `tmutil`, `ioreg` and `hdiutil` are part of macOS. You
need a Python 3.11+ (the Python that ships with Apple's Command Line Tools is too old) and pipx. The
easiest route is [Homebrew](https://brew.sh/):

```bash
brew install pipx python@3.12
```

Any other way of getting Python 3.11+ and pipx works. 4matter does not need Homebrew to run once
installed, and it never installs filesystem drivers for you. Authentication for formatting uses `sudo`
in your terminal (Touch ID works if you have enabled it for `sudo`).

### Step 2 - put pipx on your PATH (both systems)

```bash
pipx ensurepath
```

Then open a new terminal (or `source ~/.zshrc` / `source ~/.bashrc`).

### Step 3 - install 4matter from the cloned repository (both systems)

```bash
git clone <repository-url>
cd 4matter
pipx install --python python3.12 .
```

`<repository-url>` is a placeholder for wherever you obtained the source. Leave out `--python python3.12`
if your default `python3` is already 3.11 or newer.

### Launch

```bash
4matter
```

### Verify the installation

Run these from any directory (not the project folder):

```bash
command -v 4matter
4matter --version
4matter --help
4matter --check
4matter --demo
```

### Update

```bash
cd 4matter
git pull
pipx install --force --python python3.12 .
```

### Uninstall

```bash
pipx uninstall 4matter
```

### Development install

```bash
cd 4matter
pipx install --editable --python python3.12 .
```

Edits to the Python files take effect on the next launch. If you change `pyproject.toml` (entry points,
dependencies, packaged files), run `pipx install --editable --force .` again. A plain (non-editable)
`pipx install` is a frozen copy: it does not see later source changes until you reinstall.

### Fixing PATH problems

`pipx` installs commands into `~/.local/bin`. If `command -v 4matter` prints nothing:

```bash
pipx ensurepath          # adds ~/.local/bin to your shell profile
exec "$SHELL" -l         # or just open a new terminal
echo "$PATH" | tr ':' '\n' | grep local/bin
```

## 4. How to use 4matter

1. **Connect a USB drive.**
2. **Launch** `4matter`. The registry (labelled `EXTERNAL DEVICES ONLY`) lists verified external USB drives
   with model, size, filesystem and a status tag: `READY`, `MOUNTED` (will be unmounted first),
   `PROTECTED` (in use by the system or holding protected data) or `UNAVAILABLE` (for example read-only).
   Internal disks never appear. If nothing qualifies you see `NO ELIGIBLE EXTERNAL DRIVES DETECTED`.
   The right-hand panel shows details for the highlighted drive.
3. **Select the drive** with the arrow keys or `j`/`k`. Check the model, size and serial against the
   label on the stick.
4. **Inspect** it (`Enter` or `I`): partition layout bar, mount points, identifiers, and the reasons if
   it is `PROTECTED` or `UNAVAILABLE`. Inspecting is always allowed; formatting and zero-fill are not.
5. **Choose the use** (`F` starts formatting, `W` starts zero-fill): pick a preset such as "General
   storage" or "macOS". The advice panel recommends a filesystem for this computer and explains why.
6. **Select the filesystem** on the options screen. Filesystems this system cannot create are marked
   `unavailable` and explain why.
7. **Configure options**: volume label, GPT or MBR, and whether to zero-fill first. Problems are shown
   in red and the *Review* button stays disabled until they are fixed.
8. **Review** the summary: exact target, serial, current layout, what it will become, whether the
   whole device is overwritten, whether zero-fill is on, which volumes will be unmounted, and the
   numbered execution plan.
9. **Confirm.** The focus starts on *Cancel*. Tab to the red button and press Enter. For zero-fill you
   must first type the device name shown. If you are not root, your terminal temporarily returns to the
   shell to ask for your `sudo` password; 4matter never asks for it in its own input field.
10. **Monitor progress.** `C` requests cancellation where possible. Quit is disabled while an operation
    runs.
11. **Verify completion.** The result screen reads the drive back and shows the verified partition
    scheme, partition, filesystem and label. On failure it shows a plain-English reason, what state the
    drive is now in, safe next steps, and an expandable *Diagnostics* section with the raw tool output.

### Whole drive versus one partition

4matter always prepares the **entire selected disk**: old signatures are removed, a **new partition
table** is written, **one data partition** spanning the drive is created and formatted. Existing
partitions are destroyed. Formatting just one existing partition and leaving the rest of the layout
alone is **not supported**; use a lower-level tool for that.

On macOS, `diskutil eraseDisk` may also create a small hidden **EFI** partition on GPT disks. 4matter
shows it in the inspection layout, ignores it when verifying, and counts exactly one *data* partition.

## 5. Filesystem guide

### What 4matter can create on each system

| Filesystem | Linux | macOS | Created with |
|---|---|---|---|
| FAT32 | Native (`dosfstools`) | Native | `mkfs.vfat -F 32` / `diskutil eraseDisk FAT32` |
| exFAT | Needs `exfatprogs` | Native | `mkfs.exfat` / `diskutil eraseDisk ExFAT` |
| NTFS | Needs `ntfs-3g` / `ntfsprogs` | **Unavailable** (macOS can only read NTFS) | `mkfs.ntfs -Q` |
| ext4 | Native (`e2fsprogs`) | **Unavailable** (no native support) | `mkfs.ext4` |
| Btrfs | Optional (`btrfs-progs`) | **Unavailable** | `mkfs.btrfs` |
| APFS | **Unavailable** | Native | `diskutil eraseDisk APFS` (GPT only) |
| Mac OS Extended (HFS+) | **Unavailable** | Native | `diskutil eraseDisk JHFS+` (GPT only) |

4matter does not install third-party drivers and does not pretend an unavailable format works. If you need
NTFS or ext4 from a Mac, format the drive on Windows or Linux. FAT32 is verified after creation:
`diskutil` may produce FAT16 or FAT12 on very small volumes, and 4matter reports that as a verification failure.

### Who can read what

| | FAT32 | exFAT | NTFS | ext4 | Btrfs | APFS | Mac OS Extended |
|---|---|---|---|---|---|---|---|
| Windows | read/write | read/write | read/write | third-party driver | third-party driver | no | third-party driver |
| macOS | read/write | read/write | **read only** | third-party driver | third-party driver | read/write | read/write |
| Linux | read/write | read/write (kernel 5.7+ or exfatprogs/fuse) | `ntfs3` or `ntfs-3g` | native | native | third-party driver (read-mostly) | `hfsplus` driver |
| Max file size | 4 GiB − 1 byte | effectively unlimited | effectively unlimited | 16 TiB | 16 EiB | 8 EiB | 8 EiB |
| Label limit | 11 characters, upper case | 11 characters | 128 characters | 16 bytes | 255 bytes | 255 bytes | 255 characters |

Notes:

- FAT32 on drives over 2 TiB is rejected. Over 32 GB it works but some devices dislike it.
- macOS requires a volume name; if you leave the label empty 4matter uses `UNTITLED`.
- Volume names for APFS and Mac OS Extended cannot contain `:` or `/`.
- On Linux, ext4 roots are created owned by the user who launched 4matter (via `root_owner`); without
  this a fresh ext4 stick would be writable only by root. ext4 is created with `-m 0` (no 5% root reserve).

## 6. Platform recommendations

Recommendations depend on the computer you are running 4matter on. The table shows both.

| Use | Recommended | Notes |
|---|---|---|
| General storage | exFAT | Broad modern compatibility, no 4 GiB limit. |
| Windows | exFAT, or NTFS (Linux only) | exFAT also works on macOS and Linux. NTFS adds permissions and journaling but macOS can only read it, and a Mac cannot create it. 4matter does not create Windows installer media. |
| macOS only | **APFS** on macOS; exFAT on Linux | APFS and Mac OS Extended cannot be created from Linux. Choose Mac OS Extended for old Macs; exFAT if Windows or Linux must read the drive. |
| Linux only | ext4 on Linux; exFAT on macOS | A Mac cannot create ext4; 4matter falls back to exFAT and tells you why. |
| Windows + macOS (cross-platform) | exFAT | FAT32 only for very old devices, with its 4 GiB file limit. |
| PlayStation | exFAT (or FAT32) | For file transfers, backups and media. The console formats and manages its own extended game-storage drive in a layout neither Linux nor macOS can create; let the console do that. Support depends on model and firmware. |
| Xbox | exFAT (NTFS or FAT32 also read) | Same caveat: game-storage drives are formatted by the console in a proprietary layout. |
| Nintendo Switch | exFAT (FAT32 is most compatible) | For screenshots, videos and transfers. Game storage on microSD is managed by the console, which may reformat the card. Older firmware may need an update to read exFAT. |
| Steam Deck / handheld PC | ext4 on Linux | Unix permissions and symlinks matter for Proton games. On a Mac ext4 is unavailable: use exFAT only for files, and format a game card in Gaming Mode on the Deck or from Linux. |
| Legacy devices | FAT32 | Cameras, car stereos and old firmware tools. Choose it from the options screen. |

4matter prepares a drive as ordinary storage. Formatting a drive does **not** make it ready for a
console's game-install feature; always check the console maker's current guidance, because these
requirements change with firmware.

## 7. Zero-fill explained

There are three workflows:

1. **Normal format**: removes old signatures, writes a new partition table, creates and formats one
   partition. Takes seconds to a minute or two. Old file data in unused areas is *not* overwritten and
   can often be recovered with forensic tools.
2. **Zero-fill only** (`W`, first option): writes zero bytes across the entire logical capacity of the
   disk and leaves it with no partition table and no filesystem. Format it afterwards before use.
3. **Zero-fill + format**: the zero-fill above, then a new partition table and filesystem. Also
   available as a checkbox on the options screen.

**What writing zeros does.** Every addressable block is overwritten once with `0x00`. A quick format
touches only a few metadata blocks, so zero-filling takes as long as the drive's sustained write speed
allows: roughly capacity ÷ speed (a 64 GB stick at 20 MB/s is under an hour; slow drives take much
longer). 4matter shows the real rate and ETA.

**Why it is not guaranteed secure erasure.** USB flash drives and SSDs remap blocks (wear-leveling), keep
spare and over-provisioned areas, and hide bad blocks. Software writing to the logical address space cannot
reach those, so old data may survive. Zero-fill also adds write wear. It is **not** forensic-grade erasure
and 4matter makes no such claim. For sensitive data use full-disk encryption from the start, or physically
destroy the device.

### How it works on each system

| | Linux | macOS |
|---|---|---|
| Device opened | `/dev/sdX` with `O_EXCL` (the kernel refuses if anything on it is mounted or in use) | `/dev/rdiskN` (raw node) with `O_EXLOCK`, after plain `diskutil unmount` of each volume and `diskutil unmountDisk` (never forced) |
| Capacity | `lseek` to the end | `DKIOCGETBLOCKSIZE` × `DKIOCGETBLOCKCOUNT` ioctls |
| Capacity check | Compared with the size recorded at selection; mismatch aborts before writing | Same |
| Writes | Sequential 4 MiB blocks, short writes completed, final partial block sized exactly | Same |
| Flush | `fsync` every 32 MiB | `F_FULLFSYNC` every 32 MiB (also asks the drive to flush its own cache) |
| Progress | Bytes flushed / total, measured | Same |
| Native alternative | n/a | `diskutil zeroDisk` exists but exposes no machine-readable progress, so 4matter does not use it |

A write or flush error stops the operation and reports how many bytes were confirmed. Cancellation
stops at the next block; the drive is then partly overwritten and 4matter says so.

## 8. Safety and data loss

Formatting and zero-filling **permanently destroy** the existing accessible data on the selected disk.
There is no undo and no rollback; an interrupted operation can leave a drive with a half-written layout.

### External drives only

4matter is an *external storage* formatting utility. A disk is a valid target only if **all** of the following hold,
and anything unknown counts as a failure:

1. It is positively identified as **external physical storage** (Linux and macOS rules below).
2. It is a **whole disk**, not a partition or volume.
3. Nothing the running system uses lives on it.
4. It is writable, has a known capacity, and is not part of stacked, RAID or backup storage.
5. Its identity still matches what you selected (see [Device identity](#device-identity-and-disconnects)).

**Supported:** USB flash drives, and external USB hard disks and SSDs, including ones that report themselves as
"non-removable". **Permanently excluded:** internal SATA/NVMe/Apple SSDs and HDDs, the operating system's boot disk,
any disk with an active root, system, swap, LVM/RAID/device-mapper or macOS system/Recovery storage, Time Machine
destinations, disk images, loop/zram/optical devices, and disks whose connection cannot be verified.
Thunderbolt, PCIe and SD-card-slot storage is not supported yet and is refused.

**Why internal disks are blocked.** Formatting or zero-filling an internal or system disk can destroy the
operating system and personal data in seconds, and the mistake is easy to make (a wrong letter or number). 4matter
removes the possibility instead of relying on care. Being *external* is not enough on its own: an external USB SSD that
holds the currently running system, or a macOS installation, is protected exactly like an internal disk.

**How Linux decides a drive is external.** It does *not* trust the `RM` (removable) flag. A disk qualifies only if
`lsblk` reports transport `usb` **and** `/sys` shows a real USB device above the disk (`idVendor`, found by walking up
from `/sys/block/<disk>/device`). If either is missing the disk is unverified and refused. System use is read from the
kernel's own tables, every time: `/proc/self/mountinfo` (critical mounts such as `/`, `/boot`, `/boot/efi`, `/usr`,
`/var`, `/home`, `/etc`, `/opt`) and `/proc/swaps`. Those devices are expanded through partitions and device-mapper/md
`slaves` to the physical disk underneath, so using any part of a disk protects the whole disk. `lsblk`'s tree also
blocks disks with LUKS/LVM/RAID/device-mapper children, loop, zram, optical and read-only devices. If the mount table
cannot be read, every disk is refused.

**How macOS decides a drive is external.** From `diskutil info -plist`, a disk qualifies only if `Internal` is false,
`OSInternalMedia` is false, it is not virtual, `RemovableMediaOrExternalDevice` is true and the bus protocol is `USB`.
Device numbers like `disk0` are never used to decide anything. The startup disk is found from relationships: the
volumes at `/` and `/System/Volumes/Data` are resolved through their APFS container's physical store to the whole
disk(s). Also refused: APFS containers holding System, Data, Preboot, Recovery, VM, Update, xART, Hardware, ISC or
Backup volumes (so an external disk with a bootable macOS install is refused), Time Machine destinations from `tmutil`,
volumes containing `Backups.backupdb`, Fusion/spanning containers, and Apple_RAID, CoreStorage, Boot, Recovery and ISC
partitions. If the startup disk cannot be determined, **every** disk is refused.

**How the rule is enforced.** Hiding internal drives from the list is only the first layer. The rule lives in the
backend:

- `core/guard.py` is the only place that can issue a *validation token*. `authorize()` re-scans the system, refuses
  partitions and volumes, requires verified external status, re-applies every protection, and compares identity. It is
  called **before every destructive stage** (each unmount, the zero-fill, each wipe, the partition table, the format,
  the final flush), so a fresh token is used each time. Tokens expire after two minutes and name the exact disk, its
  partitions and its mounts.
- The command runner refuses any disk-modifying command without a token, refuses tools outside a short allow-list
  (`wipefs`, `sfdisk`, `mkfs.*`, `umount`, `blockdev`, `sync`, and `diskutil unmount/unmountDisk/eraseDisk`; `dd`,
  `diskutil zeroDisk` and the like are not allowed), and refuses any `/dev` node that is not part of the token's target.
  `mkfs` may only address a partition and `sfdisk`/`eraseDisk` only the whole disk. Dry-run applies the same checks
  before it records a command.
- Zero-fill requires a token for exactly that path and capacity, then checks that the descriptor it opened **is** the
  validated device (Linux: the block device number; macOS: the raw node's unit number) before writing a byte.
- The privileged helper runs the same code on its own scan of the system. It chooses its backend from the host OS and
  ignores anything in a request except the device identity, mode and options; a request cannot claim a disk is safe.
- There is no `--force`, `--unsafe` or `--allow-internal` option, no environment variable and no config file that
  relaxes any of this, and the checks run even as root. Calling the formatting or zero-fill code directly from Python
  goes through the same gates.

### Device identity and disconnects

When you select a drive 4matter records its device path, device number, size, vendor, model, serial/WWN (where the
system reports one), transport, the physical connection path (`link`: on Linux the resolved `/sys` path, which embeds
the USB host number) and a per-connection `instance` (Linux: the kernel's `diskseq`, 5.15+; macOS: the IOKit registry id of
the disk's `IOMedia`). Every field must match exactly before every destructive stage. Consequences:

- If the drive is unplugged and plugged in again, it is a **new connection** and the operation stops
  ("identity changed"); you must select it again and re-confirm. 4matter never redirects an operation to another
  drive, even one that now holds the same `/dev/sdb` or `/dev/disk4` name.
- A different stick that appears at the same path is refused for the same reason.
- If the drive disappears mid-operation the next stage aborts with "no longer present". The drive may be partly
  modified; the result screen says what state it is probably in. Nothing is rolled back.

### Other protections

- **Unmounting needs consent and is never forced.** Mounted volumes in removable-media locations (`/media`,
  `/run/media`, `/mnt`, `/Volumes`) are listed on the review screen and unmounted with a plain `umount` /
  `diskutil unmount`; a busy volume aborts the operation. Volumes mounted anywhere else are refused.
- **No shell.** All commands are argument arrays; labels and paths are never interpreted by a shell.
- **No custom password prompt.** Authentication happens through `sudo` in your own terminal. Passwords are never
  stored or placed in arguments. 4matter does not touch SIP or any macOS protection.
- **Confirmation.** The destructive button is not the default; zero-fill needs the typed device name; choosing a
  preset never starts anything; a protected target never reaches the confirmation screen.
- **Dry-run and demo are enforced in the backend**, not by disabled buttons (see
  [architecture](#11-technical-architecture)).

### Limits of these guarantees

The checks are layered and tested with mocks, fixtures and temporary disk images, but they are **not formally
verified** and have **not been run against real USB hardware or on Linux**. They depend on what the operating system
reports: a USB bridge that misreports itself could be mis-classified, and a disk the kernel or `diskutil` cannot
describe is refused rather than guessed at (which can exclude legitimate drives). macOS has no unplug-proof
per-connection serial for every device, so on macOS the "re-plug" check relies on the IOKit registry id plus the other
fields. `MacOSBackend` has a constructor parameter, `allow_disk_images`, that exists only so the opt-in test suite can treat a
disk image it created as eligible; the CLI and the privileged helper never set it, and it cannot make an internal or
physical disk eligible. A user with root can of course run `dd`, `mkfs` or `diskutil` by hand; 4matter's guarantee covers 4matter's
own code paths only. 4matter cannot know about files on a drive that you care about, cannot protect a drive that lies
about its identity, and cannot guarantee that a flash drive is securely erased. An external disk you consider
important is still eligible if it passes the checks above; unplug anything you do not intend to format.

## 9. Keyboard shortcuts

| Key | Action |
|---|---|
| `↑` `↓` or `j` `k` | Move selection |
| `Enter` | Select / continue (on the registry: inspect) |
| `Tab` / `Shift+Tab` | Change focus |
| `Esc` | Go back / close dialog |
| `R` | Refresh devices (registry) |
| `G` | Show the ASCII art full-screen (registry); `Esc` returns |
| `I` | Inspect selected device (registry) |
| `F` | Begin guided formatting (registry, inspect) |
| `W` | Zero-fill workflow (registry, inspect) |
| `C` | Request cancellation of a running operation |
| `?` | Keyboard help |
| `Q` | Quit (disabled while an operation is running) |
| `Ctrl+Q` | Same as `Q` |

While a text field has focus (volume label, zero-fill confirmation) letters, including `q`, are typed into
it; use `Esc` to go back. Mouse clicks work on lists and buttons.

## 10. Command-line reference

| Command | Effect |
|---|---|
| `4matter` | Launch the real interface (Linux or macOS). Asks for `sudo` only when you confirm an operation. |
| `4matter --help` | Show usage. |
| `4matter --version` | Print the version, for example `4matter 0.1.0`. |
| `4matter --demo` | Interactive demo with simulated drives in the style of the host OS (macOS-style `/dev/diskN` on a Mac, `/dev/sdX` on Linux); touches no disk; works on any OS. |
| `4matter --dry-run` | Real drives, real validation and plans; never writes. |
| `4matter --check` | Print which system tools are available for this OS (and which formats macOS reports); exit status 1 if core tools are missing. |
| `4matter --mono` | Reduced-colour mode. Also automatic with `NO_COLOR` set or `TERM=dumb`. |
| `python3 -m fourmatter` | Equivalent to `4matter`. |

`--demo` and `--dry-run` cannot be combined. There is deliberately no option to include internal disks or to skip
the safety checks. Real mode, `--dry-run` and `--check` print an error and exit
with status 2 on unsupported operating systems.

## 11. Technical architecture

```
src/fourmatter/
  cli.py              argument parsing; picks a backend
  app.py              Textual App, wizard state, theme loading
  theme.py            palette + monochrome fallback
  styles/main.tcss    Textual CSS (colour tokens at the top)
  screens/            dashboard, inspect, presets, format, wipe, confirm, progress, result, help, art
  widgets/            drive_list, drive_details, status_bar, progress_panel, art_panel, chrome
  core/               OS-neutral logic: models, runner, safety, guard, presets, zero_fill, platform;
                      Linux engine: devices, filesystems, partitioning, formatting, executor
  backends/           base.py (the OSBackend interface), linux.py, macos.py
  privilege/helper.py the privileged helper and the code that launches it
  demo/fixtures.py    simulated drives and simulated executor (Linux and macOS flavours)
  assets/art.txt      200x75 ASCII picture shown on wide terminals
```

- **Interface.** A Textual app. It knows nothing about the operating system: it asks the app-level
  `Backend` to scan, assess, plan and execute. Long operations run in worker threads; events flow back through
  `call_from_thread`, so the UI stays responsive.
- **OS backends.** `backends/base.py` defines `OSBackend`: `scan`, `listed`, `assess`, `fs_order`,
  `fs_support`, `validate_config`, `build_plan`, `run_operation`, `prerequisite_report`. `LinuxBackend`
  wraps the original engine. `MacOSBackend` is a separate implementation using only macOS tools. Selection is
  `core/platform.get_backend()`, based on `platform.system()`.
- **Linux discovery.** `lsblk --json --bytes` with explicit columns, recursed through partitions and stacked
  devices into plain dataclasses. USB details from `/sys`; stable names from `/dev/disk/by-id`. Partition
  paths always come from structured output, so `/dev/sdb1` and `/dev/nvme0n1p1` both work.
- **macOS discovery.** `diskutil list -plist` for the layout, `diskutil info -plist` per disk and partition
  for facts, `diskutil apfs list -plist` for container roles, `tmutil destinationinfo -X` for Time Machine, and
  `ioreg -a -r -c IOUSBHostDevice` for vendor/serial. A pure function (`parse_macos`) turns the plists into
  the same `Drive` model Linux uses; everything the backend learns about protection becomes `Drive.blockers`.
- **Safety.** `core/safety.py` turns a drive into an eligibility verdict (blockers, mounts to unmount,
  warnings) and implements identity comparison. It requires `Drive.external`, which only a backend scanner that
  verified the connection can set. It is pure and shared by both backends.
- **Guard.** `core/guard.py` mints the validation tokens that `CommandRunner` and `zero_fill` require; see
  [External drives only](#external-drives-only). Every `mutating=True` call site passes a token (a test greps for it).
- **Planning.** `build_plan` produces the ordered steps shown on the review screen; the executor runs the
  same plan. Linux: `wipefs`, `sfdisk` (one-partition script), `udevadm settle`, `mkfs.*`. macOS:
  `diskutil unmount`, a single `diskutil eraseDisk <format> <name> <GPT|MBR> /dev/diskN`.
- **Privilege management.** The UI never runs as root. At confirmation, if needed, the terminal is
  suspended and `sudo -v` is run. The helper is started as
  `sudo -n -- <python> -I -m fourmatter.privilege.helper` (isolated mode). One JSON request is sent on stdin;
  JSON-line events come back on stdout. A `cancel` line, or the UI disappearing (EOF), cancels the operation.
  The helper selects its backend from the host OS; a request cannot choose one.
- **Zero-fill.** `core/zero_fill.py` is shared; each backend supplies the opener, capacity function and flush
  function (see [section 7](#7-zero-fill-explained)).
- **App backends.** `Backend` (real, privileged helper), `DryRunBackend` (real scan, in-process executor with a
  `DryRunRunner` that records mutating commands and never executes them; the raw-device openers raise in
  dry-run), `DemoBackend` (fixtures plus a simulated executor with no code path that starts a process or opens a
  device; tests patch `subprocess` and `os.open` to fail and run it).
- **Progress.** Zero-fill reports `written` (flushed), `total`, `elapsed`, `rate` and `eta`; other steps report
  only their stage, and the UI shows an indeterminate indicator for them.

## 12. Troubleshooting

**`4matter: command not found`** — Run `pipx ensurepath`, open a new terminal, and check
`command -v 4matter`. See [Fixing PATH problems](#fixing-path-problems).

**pipx PATH issues** — `pipx ensurepath` edits your shell profile; on macOS the default shell is zsh
(`~/.zprofile`/`~/.zshrc`), on Linux usually bash. Open a new terminal afterwards. `pipx environment` shows
where commands are linked.

**`requires a different Python` during install** — You need Python 3.11+. Install one (macOS:
`brew install python@3.12`) and run `pipx install --python python3.12 .`.

**Missing dependencies** — Run `4matter --check`. On Linux install the package it points to (see the
[tool table](#linux)). On macOS the core tool is `diskutil`; if it is reported missing the system is damaged
or this is not a normal macOS install.

**No eligible external drive appears (`NO ELIGIBLE EXTERNAL DRIVES DETECTED`)** — This is intentional behaviour for
anything that is not a verified external USB drive. Reconnect the drive and press `R`. Check the system can see it:
Linux `lsblk -o NAME,TRAN,SIZE,MODEL` (the `TRAN` column must say `usb`) and `ls /sys/block/<disk>/device/..`; macOS
`diskutil list external`. Drives behind a Thunderbolt/PCIe enclosure, in a built-in SD slot, behind a bridge that does
not report USB, or on a very old kernel may be unverifiable and are refused; 4matter has no way to override that. Card
readers appear only when a card is inserted. Plugging the drive straight into a computer port (not a hub) can help
verification on Linux.

**A drive shows `PROTECTED` or `UNAVAILABLE`** — Select it and press `Enter` to read the reasons. `PROTECTED` means
the system uses it or it holds protected data (the running system, swap, a macOS installation or Recovery
volumes, Time Machine backups, LUKS/LVM/RAID): that includes an external SSD you booted from. `UNAVAILABLE` covers
things like a read-only drive or capacity that cannot be read. Neither can be modified from 4matter. Boot from a
different disk or remove the stacked storage yourself, then refresh. To format such a disk anyway you need a different
tool; 4matter will not.

**Identity changed / device no longer present** — The drive was unplugged, replaced or reconnected after you
selected it. Refresh, select it again and re-confirm.

**Permission denied / insufficient privileges** — Formatting needs administrator rights through `sudo`. Make
sure your account can use `sudo` and authenticate when the terminal asks. If sudo is not installed, run
4matter from a root shell; the interface then runs as root, which is less desirable.

**Disk is busy or mounted** — Close programs using the drive (on macOS also Finder windows and Spotlight
indexing). 4matter unmounts ordinary removable volumes for you but never forces: if `umount` /
`diskutil unmount` reports busy the operation stops. Linux: `lsof +f -- /path` or `fuser -vm /path`. macOS:
`lsof +D /Volumes/NAME`. Then retry. Volumes mounted in unusual places are refused; unmount them yourself.

**macOS `diskutil` errors** — The Diagnostics section of the result screen shows the exact command and
output. Typical messages: *"dissented by PID …"* (a process holds the volume: close it), *"Ownership of the
affected disk is required"* (authentication failed or was skipped), *"Couldn't open device"* (the disk
disappeared or is in use). `diskutil list` shows the current state. After formatting, macOS may ask whether
to use the drive for Time Machine; decline unless you want that.

**Unsupported filesystem or configuration** — The review screen lists the reason (for example NTFS or ext4 on
macOS, APFS on MBR, MBR above 2 TiB, a label that is too long). Change the option and review again.

**Formatting failure** — Read the message and *Drive state* on the result screen; nothing is rolled back. The
drive may have no valid partition table. Refresh, inspect, and run the format again. If it fails repeatedly the
drive may be failing or write-protected.

**Read-only drive** — Check for a physical write-protect switch. A drive that silently flips to read-only is
often failing; replace it.

**Interrupted zero-fill** — `C` stops it at the next block. The drive is then partly overwritten with no usable
layout. Run a format (or a zero-fill again) to make it usable.

**Device disconnected** — The operation aborts with "no longer present". The drive may be partly modified.
Reconnect it (it may get a different device name; 4matter re-identifies drives by more than the name) and format
again.

**Formatting succeeded but nothing shows in the file manager** — Unplug and replug the drive. On Linux 4matter
does not mount the new volume; macOS usually mounts it automatically.

## 13. Development and testing

```bash
git clone <repository-url>
cd 4matter
python3.12 -m venv .venv          # Python 3.11+
source .venv/bin/activate
pip install -e '.[dev]'
python -m pytest
```

The same commands work on Linux and macOS. Or use `pipx install --editable .` for the command and a separate
venv for tests.

The default suite (285 tests plus 10 opt-in tests) uses mocks, simulated `lsblk` and `diskutil` data, real
`diskutil` plists captured from a temporary disk image (`tests/fixtures/macos`), temporary files in place of block
devices, and Textual's headless pilot. It never touches real storage and runs on both systems. It covers: Linux
`lsblk` parsing and recursion; macOS plist parsing, external-drive detection, startup-disk, Time Machine and APFS
system-volume protection; identity re-validation; backend selection; presets per host; label rules;
`mkfs`/`sfdisk`/`diskutil` command construction; filesystem and partition-scheme validation; plans; zero-fill
correctness (non-multiple final block, short writes, write errors, cancellation); executor failure, permission,
busy-volume, disconnect and cancellation paths; dry-run enforcement; demo isolation; helper request validation; the
TUI wizard on both flavours; the external-only policy (internal SATA/NVMe/Apple disks, external boot drives, protected APFS stores, swap, device-mapper,
ambiguous transports, partitions, direct calls to the runner, executors, zero-fill and helper, identity changes and re-plugs,
stale and forged tokens, absence of override options); and the CLI.

Safe ways to explore: `4matter --demo` (no disks) and `4matter --dry-run` (real drives, no writes).

### Opt-in macOS disk-image integration tests

```bash
FOURMATTER_MACOS_IMAGE_TESTS=1 python -m pytest tests/test_macos_integration.py
```

These create a 128 MiB raw file, attach it with `hdiutil attach -nomount`, refuse to continue unless `diskutil`
reports it as a "Disk Image" backed by that exact file, and then run the real backend against it: every
supported format, zero-only (checking the backing file is all zeros), zero + format, and dry-run (checking the
file is unchanged). They also confirm that this Mac's real system disk is refused. The image is detached at the
end. **Passing these tests does not validate physical USB hardware**, and the tests are never run automatically.
There is no equivalent Linux loopback test yet; if you add one, create a temporary image with `truncate` and
`losetup`, and gate it behind an explicit environment variable.

## 14. Known limitations

| | Linux | macOS |
|---|---|---|
| NTFS, ext4, Btrfs | Supported | Unavailable (no native tools) |
| APFS, Mac OS Extended | Unavailable | Supported (GPT only) |
| Device identity | Path, device number, size, vendor, model, serial/WWN | Path, device number, size, model, serial (via `ioreg`, when reported) |
| Bus types accepted | USB (verified in `/sys`) | USB (`BusProtocol` USB, external, physical); Thunderbolt/SD/PCIe are refused |
| Per-connection id | `diskseq` (kernel 5.15+) plus the sysfs path | IOKit `IOMedia` registry id |
| Format engine | `wipefs` + `sfdisk` + `mkfs.*` | `diskutil eraseDisk`, which may add a hidden EFI partition |
| Zero-fill device | `/dev/sdX` | `/dev/rdiskN` |

Common to both:

- Not validated on physical removable hardware (see below).
- `sudo` is the only elevation method; passwordless polkit or `osascript` flows are not implemented.
- A running `sfdisk`, `mkfs` or `diskutil eraseDisk` cannot be cancelled; only zero-fill and the gaps between
  steps can.
- Automounters (Linux desktops, macOS Disk Arbitration) can mount the new volume between steps; 4matter
  re-unmounts removable mounts before the next destructive step, but a busy error is still possible (retry).
- Zero-fill progress updates every 32 MiB, so very slow drives update infrequently.
- macOS Time Machine detection relies on `tmutil`; if `tmutil` cannot be read, backups are still caught by APFS
  volume roles and the `Backups.backupdb` marker, but a destination in another format may not be recognised.
- Terminal rendering depends on Unicode box-drawing support. The interface fits 80×24 but is cramped there.

### Status and verification

Verified in development (macOS 27.0.1, arm64, Python 3.12, Textual 8.2.8):

- 285 automated tests pass; the 10 opt-in disk-image tests also pass when enabled (including with the guard
  and per-stage re-validation active).
- `4matter --check`, `--version`, `--help`, `--demo` and `--dry-run` work; the package builds; `pipx install .`
  (isolated pipx home) exposes `4matter` from outside the repository.
- Read-only discovery on this Mac's real disks: the internal startup disk is not external, is not listed, and is
  refused; calling the runner and zero-fill directly against it without a token is refused. No USB drive was
  attached, so none was listed.
- Against a temporary disk image (not real hardware): `diskutil eraseDisk` with FAT32 (MBR and GPT), ExFAT, Mac OS
  Extended and APFS, with read-back verification; zero-fill of the entire image (every byte zero, real progress);
  zero + format; dry-run leaving the image unchanged.
- `--dry-run` write protection is verified by tests that fail if a mutating subprocess or a device open occurs.

**Not verified (needs the real system and real hardware; use a spare stick with nothing on it):**

- **Any physical USB drive on any system**, including real `ioreg` serial/vendor extraction (parsed from a
  synthetic fixture, not from real hardware) and real write throughput.
- **Linux, entirely**: the Linux code paths were exercised only with mocks and simulated metadata, on macOS.
  Live `lsblk` output, `/sys` USB detection, real `wipefs`, `sfdisk`, `udevadm`, `mkfs.*` and `O_EXCL` behaviour on a
  block device are untested. Debian, Ubuntu, Fedora and Arch specifically are untested.
- **The `sudo` helper flow on either system** (the helper is tested as a function and for its request validation,
  not through a real `sudo` prompt).
- macOS on Intel; macOS versions other than 27.0.1.
- Interactive launch of the installed command in a real terminal emulator (the interface was driven headlessly),
  resize behaviour, and unusual terminals.

Until those are done, treat real-mode formatting and zero-fill as **untested on hardware**.

## 15. License

MIT. See [LICENSE](LICENSE).
