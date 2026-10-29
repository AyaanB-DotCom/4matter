from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..core.filesystems import FS
from ..core.presets import PRESETS, advice
from ..theme import c
from ..widgets.drive_list import DriveList  # noqa: F401  (keeps j/k binding style consistent)
from .base import BaseScreen


class PresetList(OptionList):
    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False)]


class PresetScreen(BaseScreen):
    STAGE = 1
    KEYS = [("↑↓", "Navigate"), ("Enter", "Choose"), ("Esc", "Back"), ("?", "Help")]
    BINDINGS = BaseScreen.BINDINGS + [Binding("escape", "app.pop_screen", show=False)]

    def body(self):
        with Horizontal(id="body"):
            lst = PresetList(*[Option(f"{i + 1:>2}  {p.name}", id=p.key) for i, p in enumerate(PRESETS)],
                             id="presets", classes="panel")
            lst.border_title = "WHAT IS THIS DRIVE FOR?"
            lst.styles.width = "40%"
            yield lst
            with VerticalScroll(id="detail", classes="panel") as d:
                d.border_title = "ADVICE"
                yield Static(id="advice")

    def on_mount(self) -> None:
        super().on_mount()
        lst = self.query_one(PresetList)
        lst.focus()
        lst.highlighted = 0
        self.show(PRESETS[0].key)

    def show(self, key: str) -> None:
        p = next(x for x in PRESETS if x.key == key)
        support = self.app.backend.fs_support()
        want, alts, text = advice(p, self.app.backend.host)
        t = Text()
        t.append(p.name + "\n", c("bone", "bold"))
        if want:
            t.append("Recommended: ", c("slate"))
            t.append(FS[want].name, c("moss", "bold"))
            if not support[want].ok:
                t.append("  (unavailable here)", c("rust"))
            t.append("\n")
        else:
            t.append("No recommendation.\n", c("slate"))
        names = [FS[a].name + ("" if support[a].ok else " (unavailable here)") for a in alts]
        t.append("Also possible: " + ", ".join(names) + "\n\n", c("slate"))
        t.append(text + "\n", c("bone"))
        for n in p.notes:
            t.append("\n· " + n, c("amber"))
        self.query_one("#advice", Static).update(t)

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option.id:
            self.show(ev.option.id)

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        be = self.app.backend
        self.app.wizard.apply_preset(ev.option.id, be.fs_available(), be.host,
                                     {k: a.hint for k, a in be.fs_support().items()})
        from .format import FormatScreen
        self.app.push_screen(FormatScreen())
