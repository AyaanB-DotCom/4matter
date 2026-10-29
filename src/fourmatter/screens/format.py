from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option

from ..core.filesystems import FS, normalize_label
from ..theme import c
from .base import BaseScreen


class FormatScreen(BaseScreen):
    STAGE = 2
    KEYS = [("Tab", "Next field"), ("↑↓", "Choose"), ("Enter", "Accept"), ("Esc", "Back"), ("?", "Help")]
    BINDINGS = [Binding("escape", "app.pop_screen", show=False),
                Binding("question_mark", "help", show=False)]

    def body(self):
        w = self.app.wizard
        support = self.app.backend.fs_support()
        with VerticalScroll(id="body"):
            with Horizontal(id="cfg", classes="panel") as h:
                h.border_title = "FILESYSTEM & OPTIONS"
                h.styles.height = "auto"
                with Vertical(id="fs-col"):
                    yield Label("FILESYSTEM", classes="hdr")
                    yield OptionList(*[Option(self._fs_prompt(k, support[k].ok), id=k)
                                       for k in self.app.backend.fs_order()], id="fs")
                    yield Static(id="fs-desc", classes="note")
                with Vertical(id="opt-col"):
                    yield Label("VOLUME LABEL", classes="hdr")
                    yield Input(value=w.options.label, placeholder="optional", id="label")
                    yield Label("PARTITION TABLE", classes="hdr")
                    with RadioSet(id="table"):
                        yield RadioButton("GPT (recommended)", value=w.options.table == "gpt")
                        yield RadioButton("MBR (legacy, max 2 TiB)", value=w.options.table == "mbr")
                    yield Checkbox("Zero-fill drive first (slow)", w.options.zero_fill, id="zero")
            yield Static(id="msg")
            with Horizontal(id="buttons"):
                yield Button("Review ▸", id="next")
                yield Button("◂ Back", id="back")

    @staticmethod
    def _fs_prompt(key: str, ok: bool) -> Text:
        t = Text(f"{FS[key].name:<7}", c("bone", "bold") if ok else c("slate"))
        t.append("" if ok else "unavailable", c("rust"))
        return t

    def on_mount(self) -> None:
        super().on_mount()
        fs = self.query_one("#fs", OptionList)
        order = self.app.backend.fs_order()
        fs.highlighted = order.index(self.app.wizard.options.fs) if self.app.wizard.options.fs in order else 0
        fs.focus()
        self._sync()

    # -- state ---------------------------------------------------------------------
    def _current_fs(self) -> str:
        fs = self.query_one("#fs", OptionList)
        return self.app.backend.fs_order()[fs.highlighted or 0]

    def _sync(self) -> None:
        w = self.app.wizard
        fs = self._current_fs()
        w.options.fs = fs
        w.options.label = self.query_one("#label", Input).value
        w.options.table = "mbr" if self.query_one("#table", RadioSet).pressed_index == 1 else "gpt"
        w.options.zero_fill = self.query_one("#zero", Checkbox).value
        w.mode = "zero_format" if w.options.zero_fill else "format"
        self.query_one("#fs-desc", Static).update(FS[fs].summary + f"\nMax file: {FS[fs].max_file}")
        be = self.app.backend
        problems = be.validate_config(w.drive.size, fs, w.options.table, w.options.label)
        a = be.fs_support()[fs]
        if not a.ok and a.hint not in problems:
            problems.append(a.hint)
        msg = Text()
        for p in problems:
            msg.append("✕ " + p + "\n", c("rust"))
        if not problems:
            if w.options.label and fs == "fat32" and w.options.label != normalize_label(fs, w.options.label):
                msg.append("Label will be stored in upper case.\n", c("slate"))
            for pw in w.warnings:
                msg.append("· " + pw + "\n", c("amber"))
            if w.options.zero_fill:
                msg.append("Zero-fill is slow, adds flash wear and is not secure erasure.\n", c("amber"))
        self.query_one("#msg", Static).update(msg)
        self.query_one("#next", Button).disabled = bool(problems)

    def on_option_list_option_highlighted(self, ev) -> None:
        if self.is_mounted:
            self._sync()

    def on_option_list_option_selected(self, ev) -> None:
        self.query_one("#label", Input).focus()

    def on_input_changed(self, ev) -> None:
        self._sync()

    def on_input_submitted(self, ev) -> None:
        self.query_one("#table", RadioSet).focus()

    def on_radio_set_changed(self, ev) -> None:
        self._sync()

    def on_checkbox_changed(self, ev) -> None:
        self._sync()

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id == "back":
            self.app.pop_screen()
        elif not ev.button.disabled:
            from .confirm import ConfirmScreen
            self.app.push_screen(ConfirmScreen())
