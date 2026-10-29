import pytest

from fourmatter import __version__, cli


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0 and capsys.readouterr().out.strip() == f"4matter {__version__}"


def test_help_documents_modes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for flag in ("--demo", "--dry-run", "--version", "--check", "--mono"):
        assert flag in out


def test_demo_and_dry_run_are_mutually_exclusive():
    with pytest.raises(SystemExit) as e:
        cli.main(["--demo", "--dry-run"])
    assert e.value.code == 2


def test_unsupported_os_gets_a_clear_error(monkeypatch, capsys):
    monkeypatch.setattr("fourmatter.core.platform.platform.system", lambda: "Windows")
    assert cli.main([]) == 2 and cli.main(["--dry-run"]) == 2 and cli.main(["--check"]) == 2
    err = capsys.readouterr().err
    assert "supports Linux and macOS" in err and "Windows" in err


def test_demo_selects_demo_backend(monkeypatch):
    seen = {}

    class FakeApp:
        def __init__(self, backend, mono=False):
            seen["backend"] = backend

        def run(self):
            pass
    monkeypatch.setattr("fourmatter.app.FourMatterApp", FakeApp)
    assert cli.main(["--demo"]) == 0 and seen["backend"].mode == "demo"


def test_dry_run_selects_dry_backend_on_linux(monkeypatch):
    seen = {}

    class FakeApp:
        def __init__(self, backend, mono=False):
            seen["backend"] = backend

        def run(self):
            pass
    monkeypatch.setattr("fourmatter.app.FourMatterApp", FakeApp)
    monkeypatch.setattr("fourmatter.core.platform.platform.system", lambda: "Linux")
    monkeypatch.setattr("fourmatter.backends.linux.missing_core_tools", lambda: [])
    assert cli.main(["--dry-run"]) == 0
    assert seen["backend"].mode == "dry-run" and seen["backend"].writes is False
    assert seen["backend"].host == "linux"


def test_dry_run_selects_macos_backend_on_darwin(monkeypatch):
    seen = {}

    class FakeApp:
        def __init__(self, backend, mono=False):
            seen["backend"] = backend

        def run(self):
            pass
    monkeypatch.setattr("fourmatter.app.FourMatterApp", FakeApp)
    monkeypatch.setattr("fourmatter.core.platform.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fourmatter.backends.macos.find_tool", lambda n: "/usr/sbin/" + n)
    assert cli.main(["--dry-run"]) == 0 and seen["backend"].host == "macos"


def test_missing_core_tool_stops_real_mode(monkeypatch, capsys):
    monkeypatch.setattr("fourmatter.core.platform.platform.system", lambda: "Darwin")
    monkeypatch.setattr("fourmatter.backends.macos.find_tool", lambda n: None)
    assert cli.main([]) == 1 and "diskutil" in capsys.readouterr().err
