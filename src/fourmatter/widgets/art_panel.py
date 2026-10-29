"""Decorative ASCII art for the free space on wide terminals.

assets/art.txt is a 200x75 character picture. It is box-averaged down to whatever space the
panel has (never enlarged), then mapped to a light-to-dark ramp so it stays readable small.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from rich.text import Text
from textual.widget import Widget

from ..theme import c

# Relative "ink" of the characters used in art.txt; anything else counts as mid-weight.
INK = {" ": 0, ".": 1, "'": 1, ",": 2, ";": 3, ":": 3, "c": 4, "l": 5, "o": 6, "d": 7, "x": 8,
       "k": 9, "O": 10, "0": 11, "K": 12, "X": 13, "W": 14, "N": 14}
RAMP = " .:-=+*#%@"


@lru_cache(maxsize=1)
def load_art() -> tuple[tuple[str, ...], int]:
    text = resources.files("fourmatter").joinpath("assets/art.txt").read_text(encoding="utf-8")
    lines = text.rstrip("\n").split("\n")
    width = max(len(ln) for ln in lines)
    return tuple(ln.ljust(width) for ln in lines), width


def downsample(lines: tuple[str, ...], width: int, max_w: int, max_h: int) -> list[list[float]]:
    """Box-average the picture into at most max_w x max_h cells, as 0..1 intensities.

    One scale factor is used for both axes so the picture keeps its proportions; the art is
    never enlarged (factor >= 1).
    """
    h = len(lines)
    scale = max(1.0, width / max(1, max_w), h / max(1, max_h))
    out_w, out_h = int(width / scale + 0.999), int(h / scale + 0.999)
    ink = [[INK.get(ch, 6) / 14 for ch in ln] for ln in lines]
    rows = []
    for oy in range(out_h):
        y0, y1 = int(oy * scale), max(int(oy * scale) + 1, min(h, int((oy + 1) * scale)))
        row = []
        for ox in range(out_w):
            x0, x1 = int(ox * scale), max(int(ox * scale) + 1, min(width, int((ox + 1) * scale)))
            cells = [ink[y][x] for y in range(y0, min(y1, h)) for x in range(x0, min(x1, width))]
            row.append(sum(cells) / len(cells) if cells else 0.0)
        rows.append(row)
    return rows


def render_art(max_w: int, max_h: int) -> list[tuple[str, list[float]]]:
    """(text, per-char intensity) rows. At 1:1 the original characters are kept."""
    lines, width = load_art()
    grid = downsample(lines, width, max_w, max_h)
    if len(grid) == len(lines) and len(grid[0]) == width:
        return [(ln, [INK.get(ch, 6) / 14 for ch in ln]) for ln in lines]
    flat = sorted(v for r in grid for v in r)
    lo, hi = flat[len(flat) // 20], flat[-max(1, len(flat) // 20)]  # auto-contrast
    span = max(hi - lo, 1e-6)
    out = []
    for r in grid:
        norm = [min(1.0, max(0.0, (v - lo) / span)) for v in r]
        out.append(("".join(RAMP[min(len(RAMP) - 1, int(n * len(RAMP)))] for n in norm), norm))
    return out


class ArtPanel(Widget):
    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        t = Text(no_wrap=True, overflow="crop")
        if w < 8 or h < 4:
            return t
        rows = render_art(w, h)
        for i, (line, norm) in enumerate(rows):
            for ch, n in zip(line, norm):
                t.append(ch, c("teal") if n > 0.66 else c("slate") if n > 0.33 else c("dim"))
            if i < len(rows) - 1:
                t.append("\n")
        return t
