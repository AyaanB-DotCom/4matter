from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen

from ..widgets.chrome import Header, KeyBar, Rule, StageStrip


class BaseScreen(Screen):
    """Header, circuit rule, stage strip, body and key bar shared by every screen."""

    STAGE = 0
    KEYS: list[tuple[str, str]] = []
    BINDINGS = [Binding("question_mark", "help", "Help", show=False),
                Binding("q", "quit_safe", "Quit", show=False)]

    def body(self) -> ComposeResult:
        yield from ()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Rule()
        yield StageStrip(self.STAGE)
        yield from self.body()
        yield KeyBar(self.KEYS)

    def on_mount(self) -> None:
        self.set_class(self.app.size.height < 28, "compact")
        self.set_class(self.app.size.width >= 120, "wide")

    def action_help(self) -> None:
        from .help import HelpScreen
        if not isinstance(self.app.screen, HelpScreen):
            self.app.push_screen(HelpScreen())

    def action_quit_safe(self) -> None:
        self.app.action_quit_safe()
