"""Tests de e7twin.toolchain : indépendants des outils installés (passent sur une machine nue).

Les appels système sont neutralisés : ``toolchain._which`` (recherche dans le PATH) et
``toolchain._run`` (exécution des outils pour lire leur version) sont remplacés, et ``tools/``
est un dossier temporaire factice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e7twin import toolchain

CONTRACT_KEYS = {"name", "found", "path", "version", "hint", "required"}


@pytest.fixture
def bare(monkeypatch, tmp_path):
    """Machine nue : tools/ vide, rien dans le PATH, aucun outil exécutable, pack root vide."""
    tdir = tmp_path / "tools"
    tdir.mkdir()
    monkeypatch.setenv("E7TWIN_TOOLS_DIR", str(tdir))
    monkeypatch.setenv("CMSIS_PACK_ROOT", str(tmp_path / "no-packs"))
    monkeypatch.setattr(toolchain, "_which", lambda name: None)
    monkeypatch.setattr(toolchain, "_run", lambda cmd, timeout=30.0: None)
    return tdir


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def _fake_tools(tdir: Path) -> None:
    gcc = tdir / "arm-gnu-toolchain-14.3.rel1" / "bin"
    for t in ("arm-none-eabi-gcc", "arm-none-eabi-objcopy", "arm-none-eabi-objdump",
              "arm-none-eabi-size", "arm-none-eabi-readelf"):
        _touch(gcc / toolchain._exe(t))
    ctb = tdir / "cmsis-toolbox-2.12.0"
    for t in ("cbuild", "csolution", "cpackget"):
        _touch(ctb / "bin" / toolchain._exe(t))
    (ctb / "etc").mkdir(parents=True)
    ren = tdir / "renode-1.17.0"
    _touch(ren / toolchain._exe("renode"))
    (ren / "Renode.runtimeconfig.json").write_text(
        json.dumps(
            {"runtimeOptions": {"includedFrameworks": [{"name": "Microsoft.NETCore.App", "version": "8.0.10"}]}}
        ),
        encoding="utf-8",
    )
    packs = tdir / "packs"
    _touch(packs / "pack.idx")
    for vendor, name, version in toolchain.REQUIRED_PACKS:
        _touch(packs / vendor / name / version / f"{vendor}.{name}.pdsc")


def _fake_run(cmd, timeout=30.0):
    exe = Path(cmd[0]).name.lower()
    if "gcc" in exe:
        return "14.3.1\n"
    if exe.startswith("cpackget"):
        return "cpackget version 2.1.9 (C) 2021-2023 Linaro\n"
    if exe.startswith(("cbuild", "csolution")):
        return f"{exe.split('.')[0]} version 2.12.0 (C) 2022-2025 Arm Ltd.\n"
    if exe.startswith("renode"):
        return "Renode v1.17.0\n  build: 1.17.0+20260906gitf1dd1b4af\n"
    return None


def _by_name(results):
    return {r["name"]: r for r in results}


# ---------------------------------------------------------------------------------------------


def test_doctor_bare_machine_reports_everything_missing(bare):
    results = toolchain.doctor()
    assert isinstance(results, list) and results
    for r in results:
        assert CONTRACT_KEYS <= set(r), r
        assert r["found"] is False
        assert r["path"] is None and r["version"] is None
        assert isinstance(r["hint"], str) and r["hint"]
    names = {r["name"] for r in results}
    for expected in ("arm-none-eabi-gcc", "cbuild", "csolution", "cpackget", "renode",
                     "pack AlifSemiconductor::Ensemble", ".NET (runtime Renode)"):
        assert expected in names
    # Tout est requis sauf .NET (le Renode portable embarque son runtime).
    assert _by_name(results)[".NET (runtime Renode)"]["required"] is False
    assert "arm-none-eabi-gcc" in toolchain.missing_required(results)
    assert "install_tools.ps1" in _by_name(results)["cbuild"]["hint"]


def test_doctor_finds_pinned_tools_in_tools_dir(bare, monkeypatch):
    _fake_tools(bare)
    monkeypatch.setattr(toolchain, "_run", _fake_run)
    res = _by_name(toolchain.doctor())
    assert res["arm-none-eabi-gcc"]["version"] == "14.3.1"
    assert res["cbuild"]["version"] == "2.12.0"
    assert res["csolution"]["version"] == "2.12.0"
    assert res["cpackget"]["version"] == "2.1.9"
    assert res["renode"]["version"] == "1.17.0"
    assert res["pack AlifSemiconductor::Ensemble"]["version"] == "2.2.1"
    assert res[".NET (runtime Renode)"]["version"] == "8.0.10"
    for name in ("arm-none-eabi-gcc", "cbuild", "renode", "pack ARM::CMSIS"):
        assert res[name]["found"] is True
        assert Path(res[name]["path"]).is_relative_to(bare)
    assert "épinglée" in res["cbuild"]["hint"]
    # cmake / ninja ne sont pas dans tools/ et le PATH est vide : seuls manquants.
    assert set(toolchain.missing_required(list(res.values()))) == {"cmake", "ninja"}


def test_doctor_falls_back_to_path(bare, monkeypatch, tmp_path):
    fake = _touch(tmp_path / "bin" / toolchain._exe("cbuild"))
    monkeypatch.setattr(toolchain, "_which", lambda name: str(fake) if name == "cbuild" else None)
    res = _by_name(toolchain.doctor())
    assert res["cbuild"]["found"] is True
    assert res["cbuild"]["path"] == str(fake)
    assert "PATH" in res["cbuild"]["hint"]
    assert res["csolution"]["found"] is False


def test_tools_dir_preferred_over_path(bare, monkeypatch, tmp_path):
    _fake_tools(bare)
    other = _touch(tmp_path / "elsewhere" / toolchain._exe("arm-none-eabi-gcc"))
    monkeypatch.setattr(toolchain, "_which", lambda name: str(other))
    assert toolchain.find("arm-none-eabi-gcc").is_relative_to(bare)


def test_newest_tools_version_wins(bare):
    for v in ("13.2.rel1", "14.3.rel1", "14.10.rel1"):
        _touch(bare / f"arm-gnu-toolchain-{v}" / "bin" / toolchain._exe("arm-none-eabi-gcc"))
    assert "14.10.rel1" in str(toolchain.find("arm-none-eabi-gcc"))


def test_pack_downloaded_but_license_not_accepted(bare):
    packs = bare / "packs"
    _touch(packs / "pack.idx")
    _touch(packs / ".Download" / "AlifSemiconductor.Ensemble.2.2.1.pack")
    res = _by_name(toolchain.doctor())
    alif = res["pack AlifSemiconductor::Ensemble"]
    assert alif["found"] is False
    assert "AcceptPackLicenses" in alif["hint"]
    assert "AcceptPackLicenses" not in res["pack ARM::CMSIS"]["hint"]


def test_pack_root_resolution(bare, monkeypatch, tmp_path):
    # tools/packs non initialisé -> CMSIS_PACK_ROOT
    assert toolchain.pack_root() == tmp_path / "no-packs"
    _touch(bare / "packs" / "pack.idx")
    assert toolchain.pack_root() == bare / "packs"


def test_env_sets_cmsis_variables(bare, monkeypatch):
    _fake_tools(bare)
    monkeypatch.setattr(toolchain, "_run", _fake_run)
    e = toolchain.env(base={"PATH": "/usr/bin"})
    gcc_bin = bare / "arm-gnu-toolchain-14.3.rel1" / "bin"
    assert e["GCC_TOOLCHAIN_14_3_1"] == str(gcc_bin)
    assert e["CMSIS_PACK_ROOT"] == str(bare / "packs")
    assert e["CMSIS_COMPILER_ROOT"] == str(bare / "cmsis-toolbox-2.12.0" / "etc")
    parts = e["PATH"].split(toolchain.os.pathsep)
    assert parts[0] == str(bare / "cmsis-toolbox-2.12.0" / "bin")
    assert str(gcc_bin) in parts and parts[-1] == "/usr/bin"


def test_env_on_bare_machine_does_not_fail(bare):
    e = toolchain.env(base={"PATH": "/usr/bin"})
    assert e["PATH"] == "/usr/bin"
    assert "CMSIS_PACK_ROOT" in e
    assert not any(k.startswith("GCC_TOOLCHAIN_") for k in e)


def test_gcc_version_fallback_from_dir_name(bare):
    gcc = _touch(bare / "arm-gnu-toolchain-14.3.rel1" / "bin" / toolchain._exe("arm-none-eabi-gcc"))
    assert toolchain.gcc_version(gcc) == "14.3.1"  # _run renvoie None -> déduit du dossier


# --- multiplateforme : sys.platform simulé, aucun outil réel -----------------------------------
@pytest.mark.parametrize("plat,suffix", [("win32", ".exe"), ("darwin", ""), ("linux", "")])
def test_exe_suffix_only_on_windows(monkeypatch, plat, suffix):
    monkeypatch.setattr(toolchain.sys, "platform", plat)
    assert toolchain._exe("bin/cbuild") == "bin/cbuild" + suffix


@pytest.mark.parametrize("plat,expected,forbidden", [
    ("win32", "install_tools.ps1", "install_tools.sh"),
    ("darwin", "install_tools.sh", "powershell"),
    ("linux", "install_tools.sh", "powershell"),
])
def test_doctor_hints_depend_on_os(bare, monkeypatch, plat, expected, forbidden):
    monkeypatch.setattr(toolchain.sys, "platform", plat)
    hints = " ".join(r["hint"] for r in toolchain.doctor())
    assert expected in hints and forbidden not in hints
    assert expected in toolchain.license_hint()


def test_renode_binary_macos_falls_back_to_applications(bare, monkeypatch, tmp_path):
    app = _touch(tmp_path / "Applications" / "Renode.app" / "Contents" / "MacOS" / "renode")
    monkeypatch.delenv("E7TWIN_RENODE", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    monkeypatch.setattr(toolchain, "MACOS_RENODE_CANDIDATES", (str(app),))
    assert toolchain.renode_binary() == app
    # le PATH passe avant /Applications
    monkeypatch.setattr(toolchain, "_which", lambda n: "/opt/bin/renode" if n == "renode" else None)
    assert toolchain.renode_binary() == Path("/opt/bin/renode")


def test_renode_binary_macos_candidates_ignored_elsewhere(bare, monkeypatch, tmp_path):
    app = _touch(tmp_path / "renode")
    monkeypatch.delenv("E7TWIN_RENODE", raising=False)
    monkeypatch.setattr(toolchain, "MACOS_RENODE_CANDIDATES", (str(app),))
    for plat in ("linux", "win32"):
        monkeypatch.setattr(toolchain.sys, "platform", plat)
        assert toolchain.renode_binary() is None


def test_renode_binary_linux_path_and_override(bare, monkeypatch, tmp_path):
    monkeypatch.setattr(toolchain.sys, "platform", "linux")
    monkeypatch.delenv("E7TWIN_RENODE", raising=False)
    monkeypatch.setattr(toolchain, "_which", lambda n: "/usr/bin/renode" if n == "renode" else None)
    assert toolchain.renode_binary() == Path("/usr/bin/renode")
    mine = _touch(tmp_path / "mine" / "renode")
    monkeypatch.setenv("E7TWIN_RENODE", str(mine))
    assert toolchain.renode_binary() == mine


def test_renode_in_tools_macos_app_bundle(bare, monkeypatch):
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    monkeypatch.delenv("E7TWIN_RENODE", raising=False)
    app = _touch(bare / "renode-1.17.0" / "Renode.app" / "Contents" / "MacOS" / "renode")
    assert toolchain.renode_binary() == app


def test_default_pack_root_per_os(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "X:/L")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(toolchain.sys, "platform", "win32")
    assert toolchain._default_pack_root().parts[-2:] == ("Arm", "Packs")
    monkeypatch.setattr(toolchain.sys, "platform", "darwin")
    assert toolchain._default_pack_root().parts[-3:] == (".cache", "arm", "packs")
