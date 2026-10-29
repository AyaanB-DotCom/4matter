from __future__ import annotations

from textual.binding import Binding
from textual.screen import Screen

from ..widgets.art_panel import ArtPanel
from ..widgets.chrome import KeyBar


class ArtScreen(Screen):
    """The bundled ASCII art at full terminal size. Any size works, unlike the side panel."""

    BINDINGS = [Binding("escape", "app.pop_screen", show=False), Binding("g", "app.pop_screen", show=False),
                Binding("q", "app.pop_screen", show=False), Binding("enter", "app.pop_screen", show=False)]

    def compose(self):
        art = ArtPanel(id="art-full", classes="panel")
        art.border_title = "FRAGMENT"
        yield art
        yield KeyBar([("Esc", "Back")])
