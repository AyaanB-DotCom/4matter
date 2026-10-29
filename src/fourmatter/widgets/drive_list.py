from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..core.models import Drive, human_size
from ..core.safety import Safety
from ..theme import c
from .drive_details import fs_name, status_tag


class DriveList(OptionList):
    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False)]

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.drives: dict[str, tuple[Drive, Safety]] = {}

    def set_drives(self, drives: list[Drive], assess_fn, selected: str | None = None) -> None:
        self.clear_options()
        self.drives = {d.path: (d, assess_fn(d)) for d in drives}
        for path, (d, s) in self.drives.items():
            top = Text()
            top.append(f"{d.path:<10}", c("bone", "bold"))
            name = d.display_name
            top.append(name if len(name) <= 20 else name[:19] + "…", c("bone"))
            sub = Text("  ")
            sub.append(f"{human_size(d.size)}  ", c("teal"))
            sub.append(f"{fs_name(d.fstype) if (d.fstype or d.partitions) else 'No fs'}  ", c("bone"))
            if d.transport != "usb":
                sub.append((d.transport or "?").upper() + "  ", c("slate"))
            sub.append_text(status_tag(d, s))
            self.add_option(Option(Text("\n").join([top, sub]), id=path))
        if self.drives:
            keys = list(self.drives)
            self.highlighted = keys.index(selected) if selected in keys else 0

    @property
    def current(self) -> tuple[Drive, Safety] | None:
        if self.highlighted is None or not self.drives:
            return None
        return self.drives[self.get_option_at_index(self.highlighted).id]
