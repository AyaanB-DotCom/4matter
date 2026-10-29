from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..core.models import human_size
from ..theme import c
from .base import BaseScreen

WARNING = (
    "ZERO-FILL DRIVE writes zero bytes across the entire addressable capacity of the device.\n\n"
    "· It is destructive: all data, partitions and filesystems are lost.\n"
    "· It may take a long time (roughly capacity divided by the drive's write speed).\n"
    "· It adds write wear to flash storage.\n"
    "· It is NOT guaranteed secure erasure: wear-leveling and over-provisioning leave areas "
    "of USB flash and SSDs that software cannot reach. Do not rely on it for sensitive data."
)


class WipeScreen(BaseScreen):
    STAGE = 2
    KEYS = [("↑↓", "Navigate"), ("Enter", "Choose"), ("Esc", "Back"), ("?", "Help")]
    BINDINGS = BaseScreen.BINDINGS + [Binding("escape", "app.pop_screen", show=False)]

    def body(self):
        d = self.app.wizard.drive
        with Vertical(id="body", classes="panel danger") as v:
            v.border_title = "ZERO-FILL DRIVE"
            yield Static(Text(f"Target: {d.path}  {d.display_name}  {human_size(d.size)}\n", c("bone", "bold")))
            yield Static(Text(WARNING, c("amber")))
            yield OptionList(
                Option("Zero-fill only (leave the drive unformatted)", id="zero"),
                Option("Zero-fill, then format (choose format options next)", id="zero_format"),
                id="wipe-mode")

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one(OptionList).focus()
        self.query_one(OptionList).highlighted = 0

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        w = self.app.wizard
        if ev.option.id == "zero":
            w.mode = "zero"
            from .confirm import ConfirmScreen
            self.app.push_screen(ConfirmScreen())
        else:
            w.mode = "zero_format"
            w.options.zero_fill = True
            from .presets import PresetScreen
            self.app.push_screen(PresetScreen())
