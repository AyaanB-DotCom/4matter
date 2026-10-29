import asyncio

import pytest
from textual.widgets import Button, Input

from fourmatter.app import FourMatterApp
from fourmatter.demo.fixtures import DemoBackend
from fourmatter.screens.confirm import ConfirmScreen
from fourmatter.screens.dashboard import DashboardScreen
from fourmatter.screens.format import FormatScreen
from fourmatter.screens.inspect import InspectScreen
from fourmatter.screens.presets import PresetScreen
from fourmatter.screens.progress import ProgressScreen
from fourmatter.screens.result import ResultScreen
from fourmatter.screens.wipe import WipeScreen
from fourmatter.widgets.drive_list import DriveList


def drive_test(coro, size=(100, 34), mono=False, os_name="linux"):
    async def runner():
        app = FourMatterApp(DemoBackend(sim_seconds=0.3, os_name=os_name), mono=mono)
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.3)
            await coro(app, pilot)
    asyncio.run(runner())


async def until(pilot, cond, tries=60):
    for _ in range(tries):
        if cond():
            return
        await pilot.pause(0.1)
    raise AssertionError("condition never became true")


def test_dashboard_lists_verified_external_drives_only_and_has_no_override():
    async def t(app, p):
        lst = app.screen.query_one(DriveList)
        assert [d.name for d, _ in lst.drives.values()] == ["sdb", "sdc", "sdd", "sde", "sdf"]  # no internal disk
        await p.press("a")  # the old "show all disks" key no longer exists
        await p.pause(0.2)
        assert "sda" not in [d.name for d, _ in app.screen.query_one(DriveList).drives.values()]
        assert app.screen.query_one(DriveList).border_subtitle == "EXTERNAL DEVICES ONLY"
        from fourmatter.widgets.drive_details import status_tag
        tags = {d.name: status_tag(d, s).plain for d, s in app.screen.query_one(DriveList).drives.values()}
        assert tags["sdb"] == "READY" and tags["sdc"] == "MOUNTED"
        assert tags["sdf"] == "PROTECTED" and tags["sde"] == "UNAVAILABLE"
    drive_test(t)


def test_jk_navigation_changes_selection():
    async def t(app, p):
        lst = app.screen.query_one(DriveList)
        assert lst.current[0].name == "sdb"
        await p.press("j")
        assert lst.current[0].name == "sdc"
        await p.press("k")
        assert lst.current[0].name == "sdb"
    drive_test(t)


def test_ineligible_drive_cannot_start_wizard():
    async def t(app, p):
        await p.press("j", "j", "j")  # sde: read-only
        await p.press("f")
        await p.pause(0.2)
        assert isinstance(app.screen, DashboardScreen)
        await p.press("enter")  # inspection is still allowed
        assert isinstance(app.screen, InspectScreen)
        await p.press("f")
        assert isinstance(app.screen, InspectScreen)
    drive_test(t)


def test_full_format_wizard_in_demo():
    async def t(app, p):
        await p.press("f")
        assert isinstance(app.screen, PresetScreen)
        await p.press("enter")  # general storage -> exFAT
        assert isinstance(app.screen, FormatScreen)
        assert app.wizard.options.fs == "exfat"
        app.screen.query_one("#label", Input).value = "TRAVEL"
        await p.pause(0.1)
        app.screen.query_one("#next", Button).press()
        await p.pause(0.2)
        assert isinstance(app.screen, ConfirmScreen)
        assert app.screen.focused.id == "cancel"  # destructive action is never the default
        assert not app.screen.query_one("#go", Button).disabled
        app.screen.query_one("#go", Button).press()
        await until(p, lambda: isinstance(app.screen, ResultScreen))
        r = app.screen.result
        assert r.ok and r.simulated and "SIMULATED" in r.message
        await p.press("enter")
        await until(p, lambda: isinstance(app.screen, DashboardScreen))
        assert app.busy is False
    drive_test(t)


def test_invalid_label_blocks_review():
    async def t(app, p):
        await p.press("f", "enter")
        app.screen.query_one("#label", Input).value = "THIS LABEL IS WAY TOO LONG"
        await p.pause(0.2)
        assert app.screen.query_one("#next", Button).disabled
    drive_test(t)


def test_zero_fill_requires_typed_device_id():
    async def t(app, p):
        await p.press("w")
        assert isinstance(app.screen, WipeScreen)
        await p.press("enter")  # zero-fill only
        assert isinstance(app.screen, ConfirmScreen)
        go = app.screen.query_one("#go", Button)
        assert go.disabled
        tok = app.screen.query_one("#token", Input)
        tok.value = "sdc"
        await p.pause(0.1)
        assert go.disabled
        tok.value = "sdb"
        await p.pause(0.1)
        assert not go.disabled
        go.press()
        await until(p, lambda: isinstance(app.screen, ResultScreen))
        assert app.screen.result.ok and "zero-fill" in app.screen.result.message.lower()
    drive_test(t)


def test_quit_blocked_while_busy_and_works_when_idle():
    async def t(app, p):
        app.busy = True
        app.action_quit_safe()
        await p.pause(0.1)
        assert app.is_running
        app.busy = False
    drive_test(t)


@pytest.mark.parametrize("size", [(80, 24), (60, 20), (160, 50)])
def test_every_screen_renders_at_sizes(size):
    async def t(app, p):
        await p.press("enter")
        assert isinstance(app.screen, InspectScreen)
        await p.press("f")
        assert isinstance(app.screen, PresetScreen)
        await p.press("enter")
        assert isinstance(app.screen, FormatScreen)
        app.screen.query_one("#next", Button).press()
        await p.pause(0.2)
        assert isinstance(app.screen, ConfirmScreen)
        await p.press("question_mark")
        await p.pause(0.1)
        await p.press("escape")
        await p.pause(0.1)
    drive_test(t, size=size)


def test_mono_mode_starts():
    async def t(app, p):
        await p.press("enter", "escape")
        assert isinstance(app.screen, DashboardScreen)
    drive_test(t, mono=True)
    from fourmatter import theme
    theme.set_mono(False)
