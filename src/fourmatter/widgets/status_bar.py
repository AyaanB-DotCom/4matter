from __future__ import annotations

import os

from rich.text import Text
from textual.widget import Widget

from ..theme import c


class StatusBar(Widget):
    """Compact status strip: mode, privilege level, tool problems, device count."""

    DEFAULT_CSS = "StatusBar { height: 1; padding: 0 1; }"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.count = 0
        self.message = ""

    def set(self, count: int, message: str = "") -> None:
        self.count, self.message = count, message
        self.refresh()

    def render(self) -> Text:
        b = self.app.backend
        t = Text()
        seg = lambda on: "▰" if on else "▱"  # noqa: E731
        t.append("".join(seg(i < min(self.count, 4)) for i in range(4)), c("teal"))
        t.append(f" {self.count} device{'s' if self.count != 1 else ''}   ", c("bone"))
        t.append(f"mode {b.label}", c("amber" if b.label != "LIVE" else "moss"))
        t.append(f"   {b.host}", c("teal"))
        t.append("   user " + ("root" if os.geteuid() == 0 else "unprivileged"), c("slate"))
        if self.message:
            t.append("   " + self.message, c("rust"))
        return t
