from __future__ import annotations

import time

from rich.text import Text
from textual.widget import Widget

from ..core.models import fmt_duration, fmt_rate, human_size
from ..core.zero_fill import progress_stats
from ..theme import c

BAR = 28


class ProgressPanel(Widget):
    """Bar, bytes, elapsed, rate and ETA. Percent only comes from real zero-fill progress."""

    def __init__(self, title: str, target: str, simulated: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.title, self.target, self.simulated = title, target, simulated
        self.stage_index, self.stage_total, self.stage_title = 0, 1, "Starting"
        self.determinate = False
        self.written = self.total = 0
        self.rate: float | None = None
        self.eta: float | None = None
        self.start = time.monotonic()
        self.frozen: float | None = None
        self.tick = 0

    def stop(self) -> None:
        self.frozen = time.monotonic() - self.start
        self.refresh()

    def on_mount(self) -> None:
        self.set_interval(0.25, self._tick)

    def _tick(self) -> None:
        if self.frozen is None:
            self.tick += 1
            self.refresh()

    def handle(self, ev: dict) -> None:
        if ev["event"] == "stage":
            self.stage_index, self.stage_total = ev["index"], ev["total"]
            self.stage_title, self.determinate = ev["title"], ev["determinate"]
        elif ev["event"] == "progress":
            self.written, self.total = ev["written"], ev["total"]
            self.rate, self.eta = ev["rate"], ev["eta"]
        self.refresh()

    def render(self) -> Text:
        elapsed = self.frozen if self.frozen is not None else time.monotonic() - self.start
        t = Text()
        if self.simulated:
            t.append("SIMULATED - no real device is being written\n", c("amber", "bold"))
        t.append(self.title + "\n", c("bone", "bold"))
        t.append("Target: ", c("slate"))
        t.append(self.target + "\n\n", c("bone"))
        seg = "".join("▰" if i <= self.stage_index else "▱" for i in range(self.stage_total))
        t.append(seg + "  ", c("teal"))
        t.append(f"Step {self.stage_index + 1}/{self.stage_total}: {self.stage_title}\n\n", c("bone"))
        if self.determinate and self.total:
            st = progress_stats(self.written, self.total, elapsed)
            n = int(BAR * st["fraction"])
            t.append("█" * n, c("moss"))
            t.append("░" * (BAR - n), c("dim"))
            t.append(f" {st['fraction'] * 100:5.1f}%\n", c("bone", "bold"))
            t.append(f"{human_size(self.written)} / {human_size(self.total)}\n", c("bone"))
        else:
            pos = self.tick % (2 * (BAR - 4))
            pos = pos if pos < BAR - 4 else 2 * (BAR - 4) - pos
            t.append("░" * pos, c("dim"))
            t.append("▒▒▒▒", c("amber"))
            t.append("░" * (BAR - 4 - pos), c("dim"))
            t.append("  working (no reliable percentage for this step)\n", c("slate"))
        t.append(f"Elapsed: {fmt_duration(elapsed)}", c("bone"))
        if self.determinate and self.total:
            t.append(f"   Write rate: {fmt_rate(self.rate)}   ETA: {fmt_duration(self.eta)}", c("bone"))
        return t

    def get_content_height(self, container, viewport, width: int) -> int:
        return 9 if self.simulated else 8
