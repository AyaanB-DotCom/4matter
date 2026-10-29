from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Static

from ..core.filesystems import FS
from ..core.models import human_size
from ..core.safety import confirmation_ok, confirmation_token
from ..theme import c
from ..widgets.drive_details import fs_name, layout_summary
from .base import BaseScreen


class ConfirmScreen(BaseScreen):
    STAGE = 3
    KEYS = [("Tab", "Move focus"), ("Enter", "Press button"), ("Esc", "Back"), ("?", "Help")]
    BINDINGS = [Binding("escape", "app.pop_screen", show=False),
                Binding("question_mark", "help", show=False)]

    def __init__(self) -> None:
        super().__init__()
        w = self.app.wizard
        self.drive = w.drive
        self.req = w.request()
        be = self.app.backend
        self.safety = be.assess(self.drive)
        self.plan = be.build_plan(self.drive, self.req, self.safety)
        self.needs_token = self.req.does_zero or self.req.options.zero_fill

    def summary(self) -> Table:
        d, o, be = self.drive, self.req.options, self.app.backend
        t = Table.grid(padding=(0, 1))
        t.add_column(style=c("slate"), no_wrap=True)
        t.add_column(overflow="fold")
        t.add_row("Target", Text(f"{d.path}  {d.display_name}  {human_size(d.size)}", c("bone", "bold")))
        t.add_row("Serial/ID", Text(d.serial or d.wwn or "Unknown", c("bone")))
        t.add_row("Currently", Text(f"{fs_name(d.fstype) if (d.fstype or d.partitions) else 'No filesystem'}"
                                    f" · {layout_summary(d)}", c("bone")))
        if self.req.does_format:
            lab = f' "{o.label}"' if o.label else ""
            t.add_row("Will become", Text(f"{FS[o.fs].name}{lab} · {o.table.upper()} · 1 partition", c("moss")))
        else:
            t.add_row("Will become", Text("Zeros everywhere; no partition table, no filesystem", c("amber")))
        t.add_row("Scope", Text("ENTIRE DEVICE: the whole partition layout is replaced", c("rust", "bold")))
        t.add_row("Zero-fill", Text("YES - every byte is overwritten" if self.req.does_zero or o.zero_fill
                                    else "No (quick format; old data may remain recoverable)", c("amber")))
        if self.safety.to_unmount:
            t.add_row("Unmount", Text(", ".join(m.mountpoint for m in self.safety.to_unmount)
                                      + "  (plain unmount, never forced)", c("amber")))
        if be.mode != "live":
            t.add_row("Mode", Text("DRY-RUN: nothing will be written" if be.mode == "dry-run"
                                   else "DEMO: simulated drive, nothing real is touched", c("amber", "bold")))
        return t

    def plan_text(self) -> Text:
        t = Text()
        for p in self.plan.errors:
            t.append("✕ " + p + "\n", c("rust", "bold"))
        for p in self.plan.warnings:
            t.append("· " + p + "\n", c("amber"))
        t.append("\nEXECUTION PLAN\n", c("teal", "bold"))
        for i, s in enumerate(self.plan.steps, 1):
            t.append(f"{i:>2}. ", c("slate"))
            t.append(s.title + "\n", c("rust") if s.destructive else c("bone"))
            if s.argv and self.app.backend.mode != "live":
                t.append("      $ " + " ".join(s.argv) + "\n", c("slate"))
        return t

    def body(self):
        mode = self.app.backend.mode
        title = ("ZERO-FILL" if self.req.mode == "zero" else "ERASE & FORMAT")
        with VerticalScroll(id="body", classes="panel danger") as v:
            v.border_title = "REVIEW - THIS DESTROYS DATA" if mode == "live" else "REVIEW"
            yield Static(self.summary())
            yield Static(self.plan_text(), id="plan")
            if self.needs_token:
                yield Static(Text(f"\nType {confirmation_token(self.req.identity)} to confirm "
                                  "zero-fill of this device:", c("amber", "bold")))
                yield Input(placeholder=confirmation_token(self.req.identity), id="token")
        with Horizontal(id="buttons"):
            go = {"live": title, "dry-run": "Run dry-run", "demo": "Run simulation"}[mode]
            yield Button("Cancel", id="cancel")
            yield Button(go, id="go", classes="-danger" if mode == "live" else "",
                         disabled=not self.plan.ok or self.needs_token)

    def on_mount(self) -> None:
        super().on_mount()
        if not self.safety.eligible:  # defence in depth: a protected target never reaches this screen
            self.app.pop_screen()
            self.app.notify("Protected or unavailable target: " + self.safety.reason,
                            severity="error", timeout=8)
            return
        self.query_one("#cancel", Button).focus()  # destructive action is never the default

    def on_input_changed(self, ev: Input.Changed) -> None:
        ok = confirmation_ok(self.req.identity, ev.value)
        self.query_one("#go", Button).disabled = not (ok and self.plan.ok)

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id == "cancel":
            self.app.pop_screen()
            return
        if not self.plan.ok:
            return
        if self.app.backend.mode == "live":
            from ..privilege.helper import acquire_privileges, have_privileges
            if not have_privileges():
                with self.app.suspend():
                    print("\n4matter needs administrator rights to modify the drive.\n"
                          "Authenticate with sudo to continue.\n")
                    ok = acquire_privileges()
                if not ok:
                    self.notify("Insufficient privileges: authentication failed or sudo is unavailable.",
                                severity="error", timeout=8)
                    return
        from .progress import ProgressScreen
        self.app.push_screen(ProgressScreen(self.req, self.drive, self.plan))
