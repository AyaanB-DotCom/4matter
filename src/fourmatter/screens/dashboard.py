from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import OptionList, Static

from ..core.models import Drive
from ..theme import c
from ..widgets.art_panel import ArtPanel
from ..widgets.drive_details import DriveDetails
from ..widgets.drive_list import DriveList
from ..widgets.status_bar import StatusBar
from .base import BaseScreen


class DashboardScreen(BaseScreen):
    STAGE = 0
    KEYS = [("↑↓", "Move"), ("Enter", "Select"), ("F", "Format"), ("W", "Zero-fill"),
            ("R", "Refresh"), ("G", "Art"), ("?", "Help"), ("Q", "Quit")]
    BINDINGS = BaseScreen.BINDINGS + [
        Binding("r", "refresh", show=False), Binding("i", "inspect", show=False),
        Binding("f", "format", show=False), Binding("w", "wipe", show=False),
        Binding("g", "art", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.all_drives: list[Drive] = []

    def body(self):
        with Horizontal(id="body"):
            reg = DriveList(id="drives", classes="panel")
            reg.border_title = "DEVICE REGISTRY"
            reg.border_subtitle = "EXTERNAL DEVICES ONLY"
            yield reg
            with VerticalScroll(id="detail", classes="panel") as d:
                d.border_title = "SELECTED DEVICE"
                yield DriveDetails(id="details")
            art = ArtPanel(classes="panel")
            art.border_title = "FRAGMENT"
            yield art
        yield StatusBar(id="status")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#drives").focus()
        self.action_refresh()

    def action_refresh(self) -> None:
        self.query_one(StatusBar).set(0, "scanning...")
        self.scan()

    @work(thread=True, exclusive=True)
    def scan(self) -> None:
        backend = self.app.backend
        try:
            drives, err = backend.scan(), ""
        except Exception as e:
            drives, err = [], f"scan failed: {e}"
        self.app.call_from_thread(self._populate, drives, err)

    def _populate(self, drives: list[Drive], err: str) -> None:
        self.all_drives = drives
        self._render_list(err)

    def _render_list(self, err: str = "") -> None:
        lst = self.query_one(DriveList)
        sel = lst.current[0].path if lst.current else None
        be = self.app.backend
        shown = [d for d in self.all_drives if be.listed(d)]  # verified external drives only
        lst.set_drives(shown, be.assess, sel)
        self.query_one(StatusBar).set(len(shown), err)
        if not shown:
            t = Text("NO ELIGIBLE EXTERNAL DRIVES DETECTED\n\n", c("amber", "bold"))
            t.append("4matter only modifies verified external drives. Internal and system storage "
                     "is excluded on purpose, for safety, and cannot be enabled.\n\n", c("slate"))
            t.append("Plug in a USB drive and press R to refresh.", c("slate"))
            if err:
                t.append("\n\n" + err, c("rust"))
            self.query_one(DriveDetails).update(t)
        else:
            self._show_current()

    def _show_current(self) -> None:
        cur = self.query_one(DriveList).current
        self.query_one(DriveDetails).show(*(cur or (None, None)))

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        self._show_current()

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.action_inspect()

    def action_art(self) -> None:
        from .art import ArtScreen
        self.app.push_screen(ArtScreen())

    def _selected(self):
        cur = self.query_one(DriveList).current
        if cur is None:
            self.notify("No device selected.", severity="warning")
        return cur

    def action_inspect(self) -> None:
        if cur := self._selected():
            from .inspect import InspectScreen
            self.app.wizard.reset(cur[0])
            self.app.push_screen(InspectScreen(*cur))

    def _begin(self, mode: str) -> None:
        if not (cur := self._selected()):
            return
        drive, safety = cur
        if not safety.eligible:
            self.notify("Not eligible: " + safety.reason, severity="error", timeout=8)
            return
        self.app.wizard.reset(drive, mode)
        if mode == "zero":
            from .wipe import WipeScreen
            self.app.push_screen(WipeScreen())
        else:
            from .presets import PresetScreen
            self.app.push_screen(PresetScreen())

    def action_format(self) -> None:
        self._begin("format")

    def action_wipe(self) -> None:
        self._begin("zero")

    def refresh_scan(self) -> None:
        self.action_refresh()
