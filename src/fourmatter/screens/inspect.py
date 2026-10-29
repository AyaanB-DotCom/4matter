from __future__ import annotations

from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from ..core.models import Drive
from ..core.safety import Safety
from ..widgets.drive_details import describe, layout_bar
from .base import BaseScreen


class InspectScreen(BaseScreen):
    STAGE = 0
    KEYS = [("Enter/F", "Format"), ("W", "Zero-fill"), ("Esc", "Back"), ("?", "Help")]
    BINDINGS = BaseScreen.BINDINGS + [
        Binding("escape", "app.pop_screen", show=False), Binding("enter", "format", show=False),
        Binding("f", "format", show=False), Binding("w", "wipe", show=False)]

    def __init__(self, drive: Drive, safety: Safety) -> None:
        super().__init__()
        self.drive, self.safety = drive, safety

    def body(self):
        with VerticalScroll(id="body", classes="panel") as v:
            v.border_title = f"INSPECTING {self.drive.path}"
            yield Static(describe(self.drive, self.safety, full=True))
            yield Static("PARTITION LAYOUT", classes="hdr")
            yield Static(self.layout_view())

    def layout_view(self):
        from rich.console import Group
        return Group(*layout_bar(self.drive))

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#body").focus()

    def _go(self, mode: str) -> None:
        if not self.safety.eligible:
            self.notify("Not eligible: " + self.safety.reason, severity="error", timeout=8)
            return
        self.app.wizard.reset(self.drive, mode)
        if mode == "zero":
            from .wipe import WipeScreen
            self.app.push_screen(WipeScreen())
        else:
            from .presets import PresetScreen
            self.app.push_screen(PresetScreen())

    def action_format(self) -> None:
        self._go("format")

    def action_wipe(self) -> None:
        self._go("zero")
