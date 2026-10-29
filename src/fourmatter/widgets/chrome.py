"""Frame furniture: header, stage strip, key bar and the decorative circuit rule."""

from __future__ import annotations

import random

from rich.text import Text
from textual.widget import Widget

from .. import __version__
from ..theme import c

EMBLEM = ("┌┴┐", "├◉┤", "└┬┘")
LOGO = ("╻ ╻  ┏┳┓┏━┓╺┳╸╺┳╸┏━╸┏━┓",
        "┗━╋  ┃┃┃┣━┫ ┃  ┃ ┣╸ ┣┳┛",
        "  ╹  ╹ ╹╹ ╹ ╹  ╹ ┗━╸╹┗╸")
STAGES = ("SELECT", "USE", "FORMAT", "REVIEW", "CONFIRM", "EXECUTE")


def circuit_line(width: int, seed: int = 4) -> str:
    """A deterministic strip of thin trace lines, e.g. ───┬──── ──◦───┴──"""
    rng = random.Random(seed)
    out: list[str] = []
    while len(out) < width:
        out += "─" * rng.randint(3, 9)
        out.append(rng.choice("┬┴┼◦•"))
        if rng.random() < 0.4:
            out += " " * rng.randint(2, 6)
    return "".join(out[:width])


class Header(Widget):
    def render(self) -> Text:
        backend = getattr(self.app, "backend", None)
        mode = backend.label if backend else ""
        compact = self.app.size.height < 28
        tag = Text()
        if mode and mode != "LIVE":
            tag.append(f" {mode} ", c("amber", "reverse"))
        if compact:
            t = Text()
            t.append("┌◉┐ ", c("teal"))
            t.append("4matter", c("bone", "bold"))
            t.append(" / removable media utility", c("slate"))
            pad = max(1, self.size.width - 2 - t.cell_len - tag.cell_len - len(__version__) - 3)
            t.append(" " * pad)
            t.append_text(tag)
            t.append(f" v{__version__}", c("slate"))
            return t
        lines = []
        for i in range(3):
            t = Text()
            t.append(EMBLEM[i] + "  ", c("teal"))
            t.append(LOGO[i], c("bone", "bold"))
            if i == 0:
                t.append("   4matter / removable media utility", c("slate"))
            elif i == 1:
                t.append("   " + "─" * 3 + " forbidden machinery for flash media", c("dim"))
            else:
                t.append(f"   v{__version__}  ", c("slate"))
                t.append_text(tag)
            lines.append(t)
        return Text("\n").join(lines)


class Rule(Widget):
    def render(self) -> Text:
        return Text(circuit_line(max(0, self.size.width - 2)), c("dim"))


class StageStrip(Widget):
    """SELECT ▸ USE ▸ FORMAT ▸ REVIEW ▸ CONFIRM ▸ EXECUTE with the current stage lit."""

    def __init__(self, stage: int = 0, **kw) -> None:
        super().__init__(**kw)
        self.stage = stage

    def render(self) -> Text:
        t = Text()
        for i, name in enumerate(STAGES):
            if i:
                t.append(" ─ ", c("dim"))
            if i < self.stage:
                t.append("■ " + name, c("moss"))
            elif i == self.stage:
                t.append("▣ " + name, c("amber", "bold"))
            else:
                t.append("□ " + name, c("slate"))
        return t


class KeyBar(Widget):
    def __init__(self, items: list[tuple[str, str]], **kw) -> None:
        super().__init__(**kw)
        self.items = items

    def render(self) -> Text:
        t = Text()
        for key, label in self.items:
            t.append(key, c("amber", "bold"))
            t.append(" " + label + "  ", c("slate"))
        return t
