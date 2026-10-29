"""Palette and the monochrome fallback. Rich styles go through `c()` so --mono works."""

from __future__ import annotations

PALETTE = {
    "bg": "#0d0f0e", "panel": "#131615", "bone": "#cfc8b0", "moss": "#7f9a66", "teal": "#4a8782",
    "amber": "#c8993a", "rust": "#b5472f", "slate": "#6b7680", "dim": "#3b4240",
    "selbg": "#2b2416", "selfg": "#c8993a",
}
MONO_TCSS = {  # terminal-native colours; emphasis comes from borders and reverse video
    "bg": "ansi_default", "panel": "ansi_default", "bone": "ansi_default", "moss": "ansi_default",
    "teal": "ansi_default", "amber": "ansi_default", "rust": "ansi_default",
    "slate": "ansi_bright_black", "dim": "ansi_bright_black", "selbg": "ansi_white",
    "selfg": "ansi_black",
}
MONO_RICH = {"bone": "", "moss": "bold", "teal": "", "amber": "bold", "rust": "bold underline",
             "slate": "dim", "dim": "dim"}

_mono = False


def set_mono(flag: bool) -> None:
    global _mono
    _mono = flag


def is_mono() -> bool:
    return _mono


def c(name: str, extra: str = "") -> str:
    """Rich style string for a palette colour (plus extra attributes like 'bold')."""
    base = MONO_RICH[name] if _mono else PALETTE[name]
    return f"{base} {extra}".strip() or "default"


def tcss(source: str) -> str:
    """Rewrite the `$name: value;` token block of main.tcss for the active palette."""
    import re
    table = MONO_TCSS if _mono else PALETTE
    for name, value in table.items():
        source = re.sub(rf"^\${name}:.*$", f"${name}: {value};", source, flags=re.M)
    return source
