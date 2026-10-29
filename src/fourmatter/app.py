from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib import resources

from textual.app import App
from textual.binding import Binding

from . import theme
from .core.executor import Backend
from .core.models import Drive, FormatOptions, Identity, OperationRequest
from .core.presets import BY_KEY, recommend


@dataclass
class Wizard:
    drive: Drive | None = None
    mode: str = "format"
    preset: str | None = None
    options: FormatOptions = field(default_factory=FormatOptions)
    warnings: list[str] = field(default_factory=list)

    def reset(self, drive: Drive, mode: str = "format") -> None:
        self.drive, self.mode, self.preset = drive, mode, None
        self.options = FormatOptions(zero_fill=mode == "zero_format")
        self.warnings = []

    def apply_preset(self, key: str, available: dict[str, bool], host: str = "linux",
                     reasons: dict[str, str] | None = None) -> None:
        fs, self.warnings = recommend(key, available, self.drive.size, host, reasons)
        self.preset = key
        self.options.fs, self.options.table = fs, BY_KEY[key].table

    def request(self) -> OperationRequest:
        assert self.drive is not None
        o = FormatOptions(self.options.fs, self.options.label, self.options.table, self.options.zero_fill)
        if self.mode == "zero":
            o.fs = None
        mode = "zero" if self.mode == "zero" else ("zero_format" if o.zero_fill else "format")
        return OperationRequest(mode, Identity.of(self.drive), o, _owner())


def _owner() -> tuple[int, int] | None:
    """The user who should own the new filesystem's root (matters for ext4/btrfs)."""
    uid, gid = os.getuid(), os.getgid()
    if uid == 0 and os.environ.get("SUDO_UID", "").isdigit():
        uid, gid = int(os.environ["SUDO_UID"]), int(os.environ.get("SUDO_GID", "0") or 0)
    return (uid, gid) if uid > 0 else None


class FourMatterApp(App):
    TITLE = "4matter"
    BINDINGS = [Binding("ctrl+q", "quit_safe", show=False, priority=True)]
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, backend: Backend, mono: bool = False) -> None:
        theme.set_mono(mono)
        css = resources.files("fourmatter").joinpath("styles/main.tcss").read_text(encoding="utf-8")
        self.CSS = theme.tcss(css)
        super().__init__()
        self.backend = backend
        self.wizard = Wizard()
        self.busy = False
        if mono:
            self.ansi_color = True

    def on_mount(self) -> None:
        from .screens.dashboard import DashboardScreen
        self.push_screen(DashboardScreen())

    def on_resize(self, event) -> None:
        self.screen.set_class(event.size.height < 28, "compact")
        self.screen.set_class(event.size.width >= 120, "wide")

    def action_quit_safe(self) -> None:
        if self.busy:
            self.notify("An operation is running; quitting is disabled. Press C to request "
                        "cancellation.", severity="warning")
        else:
            self.exit()

    def return_to_dashboard(self) -> None:
        from .screens.dashboard import DashboardScreen
        while len(self.screen_stack) > 1 and not isinstance(self.screen, DashboardScreen):
            self.pop_screen()
        if isinstance(self.screen, DashboardScreen):
            self.screen.refresh_scan()
