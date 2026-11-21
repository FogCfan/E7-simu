"""Smoke tests de la CLI (périmètre agent 07 ; créé par l'agent 00 au cycle 1)."""

import sys
from pathlib import Path
import types

import pytest
from typer.testing import CliRunner

from e7twin import __version__
from e7twin.cli import app

runner = CliRunner()

COMMANDS = ["build", "check", "run", "serve", "package", "npu", "doctor"]


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for cmd in COMMANDS:
        assert cmd in result.output, f"commande {cmd!r} absente de --help"


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.parametrize(
    "module,args",
    [
        ("build", ["build", "proj.csolution.yml"]),
        ("package", ["package", "app.elf"]),
        ("npu", ["npu", "model.tflite"]),
    ],
)
def test_absent_module_exits_2(monkeypatch, module, args):
    """Module absent (import impossible) : message propre et code 2, sans trace."""
    monkeypatch.setitem(sys.modules, f"e7twin.{module}", None)
    result = runner.invoke(app, args)
    assert result.exit_code == 2, result.output
    assert "non implémenté" in result.output


def _fake_module(monkeypatch, name, main):
    mod = types.ModuleType(f"e7twin.{name}")
    if main is not None:
        mod.main = main
    monkeypatch.setitem(sys.modules, f"e7twin.{name}", mod)


def test_check_delegates_to_check_main(monkeypatch):
    calls = []

    def fake_main(target, core, atoc=None, out_dir="reports"):
        calls.append((target, core, atoc))
        return 1

    _fake_module(monkeypatch, "check", fake_main)
    result = runner.invoke(app, ["check", "a.elf", "--core", "m55_he", "--atoc", "x.json"])
    assert result.exit_code == 1
    assert calls == [("a.elf", "m55_he", "x.json")]


def test_check_default_core_and_no_atoc(monkeypatch):
    calls = []
    _fake_module(monkeypatch, "check", lambda t, c, a=None, out_dir="reports": calls.append((t, c, a)) or 0)
    result = runner.invoke(app, ["check", "a.elf"])
    assert result.exit_code == 0
    assert calls == [("a.elf", "m55_hp", None)]


def test_run_delegates_to_sim_main(monkeypatch):
    calls = []

    def fake_main(elfs, hp, he, atoc, headless, expect, timeout):
        calls.append((elfs, hp, he, atoc, headless, expect, timeout))
        return 0

    _fake_module(monkeypatch, "sim", fake_main)
    result = runner.invoke(
        app,
        ["run", "a.elf", "b.elf", "--hp", "hp.elf", "--he", "he.elf", "--atoc", "t.json",
         "--headless", "--expect", "PONG 100", "--timeout", "5"],
    )
    assert result.exit_code == 0, result.output
    assert calls == [(["a.elf", "b.elf"], "hp.elf", "he.elf", "t.json", True, "PONG 100", 5.0)]


def test_run_propagates_exit_codes(monkeypatch):
    for code in (1, 2):
        _fake_module(monkeypatch, "sim", lambda *a, _c=code: _c)
        assert runner.invoke(app, ["run", "--hp", "hp.elf", "--headless"]).exit_code == code


def test_serve_delegates_to_server_main(monkeypatch):
    calls = []
    _fake_module(monkeypatch, "server", lambda host="127.0.0.1", port=8000, fake=True: calls.append((host, port, fake)) or 0)
    result = runner.invoke(app, ["serve", "--fake", "--port", "8001"])
    assert result.exit_code == 0, result.output
    assert calls == [("127.0.0.1", 8001, True)]


@pytest.mark.parametrize("module,args", [("sim", ["run", "a.elf"]), ("server", ["serve"]), ("check", ["check", "a.elf"])])
def test_missing_main_is_clean_exit_2(monkeypatch, module, args):
    _fake_module(monkeypatch, module, None)  # module présent, sans main()
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "non implémenté" in result.output


@pytest.mark.parametrize("module,args", [("sim", ["run", "a.elf"]), ("server", ["serve"])])
def test_missing_module_is_clean_exit_2(monkeypatch, module, args):
    monkeypatch.setitem(sys.modules, f"e7twin.{module}", None)  # force ImportError
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "non implémenté" in result.output


def test_check_rejects_unknown_core():
    """a32_0 n'est pas un cœur exécutable en v1 : typer/click refuse la valeur (erreur d'usage = code 2,
    voir l'en-tête de cli.py) avant toute délégation à check.main."""
    result = runner.invoke(app, ["check", "app.elf", "--core", "a32_0"])
    assert result.exit_code == 2, result.output
    assert "a32_0" in result.output and "m55_hp" in result.output  # la valeur refusée et les valeurs permises


def _fake_toolchain(monkeypatch, tools):
    mod = types.ModuleType("e7twin.toolchain")
    mod.doctor = lambda: tools
    monkeypatch.setitem(sys.modules, "e7twin.toolchain", mod)


def test_doctor_all_found(monkeypatch):
    _fake_toolchain(
        monkeypatch,
        [{"name": "arm-none-eabi-gcc", "found": True, "path": "/x/gcc", "version": "14.2", "hint": ""}],
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "arm-none-eabi-gcc" in result.output and "OK" in result.output


def test_doctor_missing_required(monkeypatch):
    _fake_toolchain(
        monkeypatch,
        [
            {"name": "arm-none-eabi-gcc", "found": True, "path": "/x/gcc", "version": "14.2", "hint": ""},
            {"name": "renode", "found": False, "path": None, "version": None, "hint": "scripts/install_tools.ps1"},
        ],
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "MANQUANT" in result.output and "install_tools.ps1" in result.output


def test_doctor_missing_optional_only(monkeypatch):
    _fake_toolchain(
        monkeypatch,
        [{"name": "vela", "found": False, "path": None, "version": None, "hint": "pip install", "required": False}],
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output


def test_build_delegates(monkeypatch):
    calls = []
    _fake_module(monkeypatch, "build", lambda target, core=None, build_type="debug", out_dir="build":
                 calls.append((target, core, build_type, out_dir)) or 0)
    result = runner.invoke(app, ["build", "p.csolution.yml", "--core", "m55_he", "--build-type", "release"])
    assert result.exit_code == 0, result.output
    assert calls == [("p.csolution.yml", "m55_he", "release", "build")]


def test_package_delegates(monkeypatch):
    calls = []
    _fake_module(monkeypatch, "package", lambda elf, core=None, out_dir="build/package", atoc_with=None:
                 calls.append((elf, core, out_dir, atoc_with)) or 0)
    result = runner.invoke(app, ["package", "hp.elf", "--core", "m55_hp", "--with", "he.elf"])
    assert result.exit_code == 0, result.output
    assert calls == [("hp.elf", "m55_hp", "build/package", ["he.elf"])]
    calls.clear()
    assert runner.invoke(app, ["package", "hp.elf"]).exit_code == 0
    assert calls == [("hp.elf", "m55_hp", "build/package", None)]


def test_package_propagates_error_code(monkeypatch):
    _fake_module(monkeypatch, "package", lambda *a, **k: 2)
    assert runner.invoke(app, ["package", "hp.elf"]).exit_code == 2


def test_check_passes_su_dir(monkeypatch):
    calls = []
    _fake_module(monkeypatch, "check", lambda t, c, a=None, out_dir="reports", su_dir=None:
                 calls.append((t, c, a, su_dir)) or 0)
    assert runner.invoke(app, ["check", "a.elf", "--su-dir", "build/su"]).exit_code == 0
    assert [(t, c, a, Path(d)) for t, c, a, d in calls] == [("a.elf", "m55_hp", None, Path("build/su"))]


def test_check_su_dir_ignored_if_unsupported(monkeypatch):
    """Un check.main sans su_dir ne doit pas casser (introspection)."""
    calls = []
    _fake_module(monkeypatch, "check", lambda t, c, a=None, out_dir="reports": calls.append((t, c, a)) or 0)
    result = runner.invoke(app, ["check", "a.elf", "--su-dir", "x"])
    assert result.exit_code == 0, result.output
    assert calls == [("a.elf", "m55_hp", None)]


@pytest.mark.parametrize("npu_opt,expected", [([], None), (["--npu", "u55_128"], "u55_128"),
                                              (["--npu", "u55_256"], "u55_256")])
def test_npu_delegates(monkeypatch, npu_opt, expected):
    calls = []
    _fake_module(monkeypatch, "npu", lambda model, npu=None, out_dir="reports":
                 calls.append((model, npu, out_dir)) or 0)
    assert runner.invoke(app, ["npu", "m.tflite", *npu_opt]).exit_code == 0
    assert calls == [("m.tflite", expected, "reports")]


def test_serve_passes_hp_he_when_supported(monkeypatch):
    calls = []
    _fake_module(monkeypatch, "server", lambda host="127.0.0.1", port=8000, fake=True, hp=None, he=None:
                 calls.append((fake, hp, he)) or 0)
    assert runner.invoke(app, ["serve", "--hp", "a.elf", "--he", "b.elf"]).exit_code == 0
    assert calls == [(False, "a.elf", "b.elf")]


def test_serve_hp_he_ignored_if_unsupported(monkeypatch):
    calls = []
    _fake_module(monkeypatch, "server", lambda host="127.0.0.1", port=8000, fake=True:
                 calls.append((host, port, fake)) or 0)
    result = runner.invoke(app, ["serve", "--fake", "--hp", "a.elf"])
    assert result.exit_code == 0, result.output
    assert calls == [("127.0.0.1", 8000, True)]
