"""Usage presets: what is the drive for? Recommend a filesystem and explain honestly."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    fs: str | None          # recommended filesystem (None = no recommendation)
    table: str
    alternatives: tuple[str, ...]
    explanation: str
    notes: tuple[str, ...] = ()
    # Different advice when the host is macOS (where APFS/HFS+ exist and ext4/NTFS do not).
    mac_fs: str | None = None
    mac_alternatives: tuple[str, ...] | None = None
    mac_explanation: str | None = None


PRESETS: tuple[Preset, ...] = (
    Preset("general", "General storage", "exfat", "gpt", ("fat32", "ntfs", "ext4"),
           "exFAT works on almost every modern computer, TV and console, and has no 4 GiB file "
           "limit. It is the safest default for a drive you carry around."),
    Preset("windows", "Windows", "exfat", "gpt", ("ntfs", "fat32"),
           "exFAT: portable, also readable on macOS and Linux. NTFS: Windows-native, adds "
           "permissions and journaling, but macOS can only read it and Linux needs the ntfs3 or "
           "ntfs-3g driver. Pick NTFS only if the drive stays with Windows machines.",
           ("4matter formats drives; it does not write Windows installer ISOs.",)),
    Preset("macos", "macOS", "exfat", "gpt", ("fat32",),
           "exFAT is the format macOS shares with Windows and Linux. macOS-native APFS and HFS+ "
           "cannot be created from Linux, so they are not offered.",
           ("APFS / HFS+ are unavailable on Linux.",),
           mac_fs="apfs", mac_alternatives=("hfsplus", "exfat"),
           mac_explanation="APFS is the native format for a drive used only with Macs (macOS 10.13 "
           "or later). Choose Mac OS Extended for older Macs, or exFAT if Windows or Linux must "
           "also read the drive."),
    Preset("linux", "Linux", "ext4", "gpt", ("exfat", "btrfs", "ntfs"),
           "ext4 is the native Linux filesystem: journaling and Unix permissions. Choose exFAT "
           "instead if the drive must also work with Windows or macOS.",
           ("The new filesystem's root folder is given to the user who launched 4matter.",)),
    Preset("winmac", "Windows + macOS", "exfat", "gpt", ("fat32",),
           "exFAT is read and written natively by both. Use FAT32 only for very old devices, "
           "and remember its 4 GiB file limit."),
    Preset("playstation", "PlayStation", "exfat", "gpt", ("fat32",),
           "PlayStation consoles generally read FAT32 or exFAT USB drives for file transfers, "
           "backups and media; exFAT avoids the 4 GiB file limit.",
           ("Game storage: the console formats and manages its own extended-storage drives "
            "using a layout Linux cannot create. Let the console format the drive for that.",
            "Compatibility depends on console model and firmware; check Sony's current guidance.")),
    Preset("xbox", "Xbox", "exfat", "gpt", ("ntfs", "fat32"),
           "Xbox consoles read FAT32, exFAT and NTFS drives for media and file transfer.",
           ("Game storage: the console formats and manages its own game drives with a "
            "proprietary layout Linux cannot create. Let the console format the drive for that.",
            "Compatibility depends on console model and firmware; check Microsoft's guidance.")),
    Preset("switch", "Nintendo Switch", "exfat", "gpt", ("fat32",),
           "The Switch reads FAT32 and exFAT cards and drives for screenshots, videos and "
           "transfers. FAT32 is the most compatible; exFAT removes the 4 GiB file limit.",
           ("Game storage on microSD is managed by the console, which may reformat the card.",
            "Older firmware may need an update before it can read exFAT.")),
    Preset("steamdeck", "Steam Deck / handheld PC", "ext4", "gpt", ("exfat", "btrfs"),
           "ext4 suits game libraries on Linux handhelds: Unix permissions and symlinks matter "
           "for Proton. Use exFAT if the card is also read by Windows (not recommended for "
           "installed games).",
           ("SteamOS can also format a card itself from Gaming Mode.",)),
    Preset("custom", "Custom / Advanced", None, "gpt", ("exfat", "fat32", "ntfs", "ext4", "btrfs"),
           "No recommendation. Choose every option yourself on the next screen."),
)

BY_KEY = {p.key: p for p in PRESETS}


def advice(p: Preset, host: str) -> tuple[str | None, tuple[str, ...], str]:
    """(recommended fs, alternatives, explanation) for this preset on this host OS."""
    if host == "macos" and p.mac_fs:
        return p.mac_fs, p.mac_alternatives or p.alternatives, p.mac_explanation or p.explanation
    return p.fs, p.alternatives, p.explanation


def recommend(key: str, available: dict[str, bool], size: int, host: str = "linux",
              reasons: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """Pick the preset's filesystem, falling back to an installed alternative.

    Returns (filesystem, warnings). The warning explains any fallback instead of
    silently choosing something incompatible.
    """
    p = BY_KEY[key]
    warnings: list[str] = []
    want, alts, _ = advice(p, host)
    candidates = ((want,) if want else ()) + alts
    fs = next((c for c in candidates if available.get(c)), candidates[0])
    if want and fs != want:
        why = (reasons or {}).get(want) or f"the tools for {want} are not installed"
        warnings.append(f"{want} is the usual choice here but is unavailable: {why} "
                        f"{fs} was selected instead.")
    if host == "macos" and p.key == "steamdeck" and fs != "ext4":
        warnings.append("exFAT works for the card but is not recommended for installed games; "
                        "format the card in Gaming Mode on the Deck, or from Linux.")
    if fs == "fat32" and size > 32 * 1000**3:
        warnings.append("FAT32 on a drive over 32 GB works but is unusual; some devices dislike it.")
    return fs, warnings
