from __future__ import annotations

import threading

from rich.table import Table
from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Collapsible, Static

from ..core.models import OperationRequest, OperationResult, Plan, human_size
from ..theme import c
from ..widgets.progress_panel import ProgressPanel
from .base import BaseScreen

TITLES = {"zero": "ZERO-FILL OPERATION", "format": "FORMAT OPERATION",
          "zero_format": "ZERO-FILL + FORMAT OPERATION"}


class ProgressScreen(BaseScreen):
    STAGE = 5
    KEYS = [("C", "Cancel (where possible)"), ("?", "Help")]
    BINDINGS = [Binding("c", "cancel", show=False), Binding("question_mark", "help", show=False),
                Binding("escape", "noop", show=False)]

    def __init__(self, req: OperationRequest, drive, plan: Plan) -> None:
        super().__init__()
        self.req, self.drive, self.plan = req, drive, plan
        self.cancel_event = threading.Event()
        self.log_lines: list[str] = []

    def body(self):
        sim = self.app.backend.mode != "live"
        with Vertical(id="body", classes="panel warn") as v:
            v.border_title = "IN PROGRESS"
            target = f"{self.drive.display_name} / {human_size(self.drive.size)}  ({self.drive.path})"
            yield ProgressPanel(TITLES[self.req.mode], target, simulated=sim, id="pp")
            yield Static("", id="log")

    def on_mount(self) -> None:
        super().on_mount()
        self.app.busy = True
        self.run_op()

    @work(thread=True, exclusive=True)
    def run_op(self) -> None:
        def emit(ev: dict) -> None:
            self.app.call_from_thread(self._event, ev)
        try:
            result = self.app.backend.execute(self.req, emit, self.cancel_event)
        except Exception as e:  # never leave the user on a dead progress screen
            result = OperationResult(False, f"Unexpected error: {e}", repr(e),
                                     "Unknown: check the drive before trusting it.")
        self.app.call_from_thread(self._done, result)

    def _event(self, ev: dict) -> None:
        pp = self.query_one(ProgressPanel)
        pp.handle(ev)
        if ev["event"] == "log":
            self.log_lines = (self.log_lines + [ev["line"]])[-200:]
            tail = "\n".join(l[:self.size.width - 6] for l in self.log_lines[-4:])
            self.query_one("#log", Static).update(Text(tail, c("slate")))

    def _done(self, result: OperationResult) -> None:
        self.app.busy = False
        self.query_one(ProgressPanel).stop()
        from .result import ResultScreen
        self.app.switch_screen(ResultScreen(result, self.req, "\n".join(self.log_lines)))

    def action_cancel(self) -> None:
        if self.cancel_event.is_set():
            return
        self.cancel_event.set()
        self.notify("Cancel requested. Zero-fill stops at the next block; a running tool "
                    "(partitioning, mkfs) cannot be interrupted and finishes first.",
                    timeout=8)

    def action_noop(self) -> None:
        self.notify("An operation is running. Press C to request cancellation.")
