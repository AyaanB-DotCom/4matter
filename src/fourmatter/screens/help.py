from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

from ..theme import c

SHORTCUTS = [
    ("↑ ↓  /  j k", "Move selection"),
    ("Enter", "Select / continue"),
    ("Tab  Shift+Tab", "Change focus"),
    ("Esc", "Go back / close dialog"),
    ("R", "Refresh devices"),
    ("G", "Show the ASCII art full-screen (registry)"),
    ("I", "Inspect selected device"),
    ("F", "Begin guided formatting"),
    ("W", "Zero-fill workflow"),
    ("C", "Cancel a running operation (where possible)"),
    ("?", "This help"),
    ("Q", "Quit (blocked while an operation runs)"),
]


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", show=False), Binding("question_mark", "dismiss", show=False),
                Binding("q", "dismiss", show=False)]

    def compose(self) -> ComposeResult:
        t = Table.grid(padding=(0, 2))
        t.add_column(style=c("amber", "bold"), no_wrap=True)
        t.add_column()
        for k, v in SHORTCUTS:
            t.add_row(k, v)
        outer = Table.grid()
        outer.add_row(Text("KEYBOARD", c("teal", "bold")))
        outer.add_row(Text(""))
        outer.add_row(t)
        outer.add_row(Text(""))
        outer.add_row(Text("Esc closes this panel.", c("slate")))
        yield Static(outer, id="help")
