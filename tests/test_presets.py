from fourmatter.core.presets import BY_KEY, PRESETS, recommend

ALL = {"fat32": True, "exfat": True, "ntfs": True, "ext4": True, "btrfs": False}


def test_ten_presets_in_spec_order():
    assert [p.name for p in PRESETS][:3] == ["General storage", "Windows", "macOS"]
    assert len(PRESETS) == 10


def test_recommendations():
    assert recommend("general", ALL, 10**10)[0] == "exfat"
    assert recommend("linux", ALL, 10**10)[0] == "ext4"
    assert recommend("winmac", ALL, 10**10)[0] == "exfat"
    assert recommend("steamdeck", ALL, 10**10)[0] == "ext4"


def test_fallback_is_explained_not_silent():
    fs, warns = recommend("general", {**ALL, "exfat": False}, 10**10)
    assert fs != "exfat" and warns and "exfat" in warns[0]


def test_console_presets_do_not_promise_game_install():
    for k in ("playstation", "xbox", "switch"):
        notes = " ".join(BY_KEY[k].notes).lower()
        assert "console" in notes and ("format" in notes or "manage" in notes)


def test_macos_native_formats_disclaimed():
    assert "APFS" in " ".join(BY_KEY["macos"].notes)
