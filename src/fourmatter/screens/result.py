from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

from ..core.models import OperationRequest, OperationResult
from ..theme import c
from .base import BaseScreen


class ResultScreen(BaseScreen):
    STAGE = 5
    KEYS = [("Enter", "Back to registry"), ("Q", "Quit"), ("?", "Help")]
    BINDINGS = [Binding("enter", "done", show=False), Binding("escape", "done", show=False),
                Binding("question_mark", "help", show=False), Binding("q", "quit_safe", show=False)]

    def __init__(self, result: OperationResult, req: OperationRequest, log: str) -> None:
        super().__init__()
        self.result, self.req, self.log_text = result, req, log

    def body(self):
        r = self.result
        cls = "good" if r.ok else ("warn" if r.cancelled else "danger")
        with VerticalScroll(id="body", classes=f"panel {cls}") as v:
            v.border_title = ("OPERATION COMPLETE" if r.ok else
                              "OPERATION CANCELLED" if r.cancelled else "OPERATION FAILED")
            t = Text()
            if r.simulated:
                t.append("SIMULATED / NO WRITES: ", c("amber", "bold"))
            t.append(r.message + "\n", c("moss" if r.ok else "rust", "bold"))
            yield Static(t)
            if r.verified:
                g = Table.grid(padding=(0, 2))
                g.add_column(style=c("slate"))
                g.add_column()
                for k, val in r.verified.items():
                    g.add_row(k, Text(val, c("bone")))
                yield Static(Text("\nVERIFIED RESULT" if not r.simulated else "\nRESULT", c("teal", "bold")))
                yield Static(g)
            if r.state:
                yield Static(Text("\nDRIVE STATE\n" , c("teal", "bold")))
                yield Static(Text(r.state, c("amber")))
            if not r.ok:
                yield Static(Text("\nSAFE NEXT STEPS\n· Check that the drive is still connected.\n"
                                  "· Refresh the registry and inspect the drive.\n"
                                  "· Re-run the operation; an interrupted format is not rolled back.",
                                  c("slate")))
            with Collapsible(title="Diagnostics", collapsed=True):
                yield Static(Text(r.details or self.log_text or "(none)", c("slate")))

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#body").focus()

    def action_done(self) -> None:
        self.app.return_to_dashboard()
