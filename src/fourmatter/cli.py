"""Command-line entry point: `4matter`."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__

EPILOG = """\
modes:
  (default)   real drives; formatting asks for sudo only when you confirm an operation
  --dry-run   real drives, inspected and planned, but nothing is ever written
  --demo      simulated drives; no disk is touched

4matter can destroy data. Keep backups.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="4matter", description="4matter / removable media utility: format, prepare and "
        "zero-fill USB drives from a keyboard-driven terminal interface (Linux and macOS).",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"4matter {__version__}")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--demo", action="store_true", help="interactive demo with simulated drives")
    g.add_argument("--dry-run", action="store_true",
                   help="inspect real drives and review plans; never write")
    p.add_argument("--check", action="store_true", help="print which system tools are installed and exit")
    p.add_argument("--mono", action="store_true",
                   help="reduced-colour mode (also enabled by NO_COLOR or TERM=dumb)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from .core.platform import UnsupportedPlatform, get_backend
    if args.check or not args.demo:
        try:
            osb = get_backend()
        except UnsupportedPlatform as e:
            print(e, file=sys.stderr)
            return 2
    if args.check:
        lines, core_ok = osb.prerequisite_report()
        print(f"Host: {osb.display}")
        print("\n".join(lines))
        return 0 if core_ok else 1

    from .app import FourMatterApp
    if args.demo:
        from .demo.fixtures import DemoBackend
        backend = DemoBackend()
    else:
        from .core.executor import Backend, DryRunBackend
        missing = osb.missing_core_tools()
        if missing:
            print("Required system tool(s) not found: " + ", ".join(missing) + ". "
                  "Run `4matter --check` for details.", file=sys.stderr)
            return 1
        backend = DryRunBackend(osb) if args.dry_run else Backend(osb)
    mono = args.mono or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"
    FourMatterApp(backend, mono=mono).run()
    return 0
