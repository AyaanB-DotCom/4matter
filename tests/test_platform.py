import asyncio
import subprocess

import pytest
from textual.widgets import Button, Input

from fourmatter.app import FourMatterApp
from fourmatter.backends.base import OSBackend
from fourmatter.backends.linux import LinuxBackend
from fourmatter.backends.macos import MacOSBackend
from fourmatter.core.executor import Backend, DryRunBackend
from fourmatter.core.models import FormatOptions, Identity, OperationRequest
from fourmatter.core.platform import UnsupportedPlatform, get_backend, host_os
from fourmatter.core.presets import advice, BY_KEY, recommend
from fourmatter.demo.fixtures import DemoBackend
from fourmatter.screens.confirm import ConfirmScreen
from fourmatter.screens.dashboard import DashboardScreen
from fourmatter.screens.format import FormatScreen
from fourmatter.screens.result import ResultScreen
from fourmatter.widgets.drive_list import DriveList


# ---- backend selection ---------------------------------------------------------------------------
@pytest.mark.parametrize("system,expected", [("Linux", "linux"), ("Darwin", "macos"), ("Windows", "windows"),
                                              ("FreeBSD", "freebsd")])
def test_host_os_detection(system, expected):
    assert host_os(system) == expected


def test_backend_selection_by_name_and_host(monkeypatch):
    assert isinstance(get_backend("linux"), LinuxBackend) and isinstance(get_backend("macos"), MacOSBackend)
    for os_name, cls in (("Linux", LinuxBackend), ("Darwin", MacOSBackend)):
        monkeypatch.setattr("fourmatter.core.platform.platform.system", lambda n=os_name: n)
        assert isinstance(get_backend(), cls) and isinstance(Backend().os, cls)


def test_unsupported_os_raises_clear_error(monkeypatch):
    monkeypatch.setattr("fourmatter.core.platform.platform.system", lambda: "Plan9")
    with pytest.raises(UnsupportedPlatform, match="Linux and macOS"):
        get_backend()


def test_both_backends_implement_the_interface():
    for b in (LinuxBackend(), MacOSBackend()):
        assert isinstance(b, OSBackend) and not getattr(type(b), "__abstractmethods__", None)
    assert LinuxBackend().fs_order() == ("exfat", "fat32", "ntfs", "ext4", "btrfs")
    assert MacOSBackend().fs_order()[:4] == ("exfat", "fat32", "apfs", "hfsplus")


def test_linux_backend_rejects_mac_only_filesystems():
    b = LinuxBackend()
    assert any("cannot be created on Linux" in e for e in b.validate_config(64 * 10**9, "apfs", "gpt", ""))
    sup = b.fs_support()
    assert not sup["apfs"].ok and not sup["hfsplus"].ok and "Linux" in sup["apfs"].hint


def test_helper_picks_backend_from_the_host_not_the_request(monkeypatch):
    seen = {}

    class Fake:
        def run_operation(self, req, emit, cancel, *, runner):
            seen["ran"] = True
            from fourmatter.core.models import OperationResult
            return OperationResult(True, "ok")

    import json
    import io
    from fourmatter.privilege import helper
    req = {"mode": "format", "identity": {"path": "/dev/sdb", "maj_min": "8:16", "size": 5, "vendor": None,
                                          "model": "x", "serial": "s", "wwn": None, "transport": "usb"},
           "options": {"fs": "ext4", "label": "", "table": "gpt", "zero_fill": False}, "owner": None,
           "backend": "macos"}  # smuggled field must be ignored
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.sys, "stdin", io.StringIO(json.dumps(req) + "\n"))
    monkeypatch.setattr("fourmatter.core.platform.get_backend", lambda name=None: seen.setdefault("name", name) or Fake())
    monkeypatch.setattr("fourmatter.core.platform.get_backend", lambda name=None: (seen.update(name=name), Fake())[1])
    helper.main()
    assert seen == {"name": None, "ran": True}


# ---- presets across hosts ------------------------------------------------------------------------------------
ALL = {"fat32": True, "exfat": True, "ntfs": True, "ext4": True, "btrfs": False, "apfs": False, "hfsplus": False}
MAC = {"fat32": True, "exfat": True, "ntfs": False, "ext4": False, "btrfs": False, "apfs": True, "hfsplus": True}
WHY = {"ext4": "macOS has no native ext4 support."}


def test_macos_only_preset_recommends_apfs_on_mac_and_exfat_on_linux():
    assert recommend("macos", MAC, 10**10, "macos")[0] == "apfs"
    assert recommend("macos", ALL, 10**10, "linux")[0] == "exfat"
    assert advice(BY_KEY["macos"], "macos")[0] == "apfs" and "Macs" in advice(BY_KEY["macos"], "macos")[2]


def test_general_and_cross_platform_presets_stay_exfat_on_mac():
    for k in ("general", "winmac", "windows", "playstation", "xbox", "switch"):
        assert recommend(k, MAC, 10**10, "macos")[0] == "exfat", k


def test_linux_and_steamdeck_presets_fall_back_with_an_explanation_on_mac():
    fs, warns = recommend("linux", MAC, 10**10, "macos", WHY)
    assert fs == "exfat" and "ext4" in warns[0] and "no native ext4" in warns[0]
    fs, warns = recommend("steamdeck", MAC, 10**10, "macos", WHY)
    assert fs == "exfat" and any("Gaming Mode" in w for w in warns)


# ---- demo mode on both flavours -----------------------------------------------------------------------------------
@pytest.fixture
def no_side_effects(monkeypatch):
    import os

    def boom(*a, **k):
        pytest.fail("demo mode touched the system")
    for obj, name in ((subprocess, "run"), (subprocess, "Popen"), (os, "open"), (os, "system")):
        monkeypatch.setattr(obj, name, boom)


def test_macos_demo_fixtures_and_isolation(no_side_effects):
    b = DemoBackend(sim_seconds=0.2, os_name="macos")
    drives = b.scan()
    assert b.host == "macos" and all(d.simulated and d.path.startswith("/dev/disk") for d in drives)
    listed = [d.name for d in drives if b.listed(d)]
    assert "disk0" not in listed and {"disk4", "disk5", "disk8", "disk9"} <= set(listed)
    verdict = {d.name: b.assess(d).eligible for d in drives}
    assert verdict["disk4"] and not verdict["disk7"] and not verdict["disk8"] and not verdict["disk9"] and not verdict["disk0"]
    sup = b.fs_support()
    assert sup["apfs"].ok and sup["hfsplus"].ok and not sup["ntfs"].ok and not sup["ext4"].ok
    d = drives[0]
    res = b.execute(OperationRequest("zero_format", Identity.of(d), FormatOptions("apfs", "DEMO", "gpt", True)),
                    lambda e: None, __import__("threading").Event())
    assert res.ok and res.simulated and "SIMULATED" in res.message
    bad = b.execute(OperationRequest("format", Identity.of(d), FormatOptions("ntfs", "", "gpt")), lambda e: None,
                    __import__("threading").Event())
    assert not bad.ok and "NTFS" in bad.message


def test_linux_demo_still_offers_linux_filesystems(no_side_effects):
    b = DemoBackend(os_name="linux")
    sup = b.fs_support()
    assert all(sup[k].ok for k in ("fat32", "exfat", "ntfs", "ext4", "btrfs")) and not sup["apfs"].ok


# ---- the TUI on the macOS flavour --------------------------------------------------------------------------------------
def run_mac(coro, size=(110, 34)):
    async def go():
        app = FourMatterApp(DemoBackend(sim_seconds=0.3, os_name="macos"))
        async with app.run_test(size=size) as p:
            await p.pause(0.4)
            await coro(app, p)
    asyncio.run(go())


def test_mac_registry_lists_external_drives_only():
    async def t(app, p):
        lst = app.screen.query_one(DriveList)
        assert "/dev/disk0" not in lst.drives and lst.current[0].path == "/dev/disk4"
        await p.press("a")
        await p.pause(0.2)
        assert "/dev/disk0" not in app.screen.query_one(DriveList).drives  # internal disks never appear
    run_mac(t)


def test_mac_protected_disks_cannot_start_the_wizard():
    async def t(app, p):
        lst = app.screen.query_one(DriveList)
        for target in ("/dev/disk8", "/dev/disk9"):  # bootable macOS install, Time Machine disk
            lst.highlighted = list(lst.drives).index(target)
            await p.pause(0.1)
            await p.press("f")
            await p.pause(0.2)
            assert isinstance(app.screen, DashboardScreen), target
    run_mac(t)


def test_mac_format_screen_marks_unsupported_filesystems_and_blocks_review():
    async def t(app, p):
        await p.press("f", "enter")
        assert isinstance(app.screen, FormatScreen)
        fs = app.screen.query_one("#fs")
        order = app.backend.fs_order()
        assert [fs.get_option_at_index(i).id for i in range(fs.option_count)] == list(order)
        fs.highlighted = order.index("ntfs")
        await p.pause(0.2)
        assert app.screen.query_one("#next", Button).disabled
        fs.highlighted = order.index("apfs")
        await p.pause(0.2)
        assert not app.screen.query_one("#next", Button).disabled
        app.screen.query_one("#table").focus()
    run_mac(t)


def test_mac_macos_preset_and_end_to_end_simulated_format():
    async def t(app, p):
        await p.press("f")
        await p.press("down", "down", "enter")  # "macOS" preset
        assert isinstance(app.screen, FormatScreen) and app.wizard.options.fs == "apfs"
        app.screen.query_one("#next", Button).press()
        await p.pause(0.2)
        assert isinstance(app.screen, ConfirmScreen)
        assert "diskutil" in app.screen.plan_text().plain and "eraseDisk" in app.screen.plan_text().plain
        app.screen.query_one("#go", Button).press()
        for _ in range(60):
            if isinstance(app.screen, ResultScreen):
                break
            await p.pause(0.1)
        assert app.screen.result.ok and app.screen.result.simulated
    run_mac(t)


def test_mac_zero_fill_needs_the_disk_name():
    async def t(app, p):
        await p.press("w", "enter")
        go = app.screen.query_one("#go", Button)
        assert go.disabled
        app.screen.query_one("#token", Input).value = "disk4"
        await p.pause(0.1)
        assert not go.disabled
    run_mac(t)
