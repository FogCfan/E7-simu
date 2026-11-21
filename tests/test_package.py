"""Tests de e7twin.package (agent 02 ; fichier relevant de l'agent 07).

Unitaires : ``analyze_image`` / ``_objcopy`` / ``check.main`` remplacés (aucun outil requis).
Réels (``@pytest.mark.toolchain``) : ELF générés par ``fixtures/elfgen.py`` et hello du SDK.

Règle de sécurité vérifiée ici : ``package`` n'exécute JAMAIS SETOOLS (``app-gen-toc``,
``app-write-mram``). Un garde remplace ``subprocess.run/Popen``, ``os.system`` et ``os.exec*`` : tout
appel dont la ligne de commande mentionne SETOOLS fait échouer le test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from e7twin import board as board_mod
from e7twin import build, check, package, toolchain

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import elfgen  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SDK_SOLUTION = REPO / "tools" / "alif_vscode-template" / "alif.csolution.yml"
B = board_mod.load()
FORBIDDEN = ("app-write-mram", "app-gen-toc", "setools", "tools-config")


@pytest.fixture
def no_setools(monkeypatch):
    """Fait échouer tout lancement de processus qui ressemble à SETOOLS ; enregistre les autres."""
    seen: list[str] = []

    def guard(cmd):
        text = " ".join(map(str, cmd)) if isinstance(cmd, (list, tuple)) else str(cmd)
        seen.append(text)
        assert not any(f in text.lower() for f in FORBIDDEN), f"SETOOLS lancé par package : {text}"

    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(cmd, *a, **k):
        guard(cmd)
        return real_run(cmd, *a, **k)

    class Popen(real_popen):
        def __init__(self, cmd, *a, **k):
            guard(cmd)
            super().__init__(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)
    monkeypatch.setattr(os, "system", lambda c: guard(c))
    for name in ("execv", "execvp", "execl", "execlp", "spawnv", "spawnl"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, lambda *a, **k: guard(a))
    return seen


# ----------------------------------------------------------------------------- unitaires


def _image(tmp_path, core="m55_hp", size=16):
    slot = B.slot(core)
    elf = tmp_path / f"{core}.elf"
    elf.write_bytes(b"x")
    return package.Image(elf, core, slot.name, slot.base, slot.base, slot.base + size, [".text"])


def test_atoc_dict_matches_template_format(tmp_path):
    hp, he = _image(tmp_path, "m55_hp"), _image(tmp_path, "m55_he")
    hp.bin_path, he.bin_path = tmp_path / "a.bin", tmp_path / "b.bin"
    d = package.atoc_dict([hp, he])
    assert d["DEVICE"]["binary"] == "app-device-config.json"
    assert d["HP_APP"] == {
        "disabled": False, "binary": "a.bin", "version": "1.0.0", "signed": False,
        "mramAddress": f"0x{B.slot('m55_hp').base:08X}", "cpu_id": "M55_HP", "flags": ["boot"],
    }
    assert d["HE_APP"]["mramAddress"] == f"0x{B.slot('m55_he').base:08X}" and d["HE_APP"]["cpu_id"] == "M55_HE"
    assert package.atoc_filename([he, hp]) == "M55_HP_HE_mram_cfg.json"
    assert package.atoc_filename([hp]) == "M55_HP_mram_cfg.json"


def test_setools_commands_are_only_text(tmp_path):
    lines = package.setools_commands(tmp_path / "M55_HP_mram_cfg.json", [tmp_path / "x.bin"])
    text = "\n".join(lines)
    assert "./app-gen-toc -f build/config/M55_HP_mram_cfg.json" in text
    assert "./app-write-mram -p" in text
    assert "E7" in text and "humain" in text


def _fake_pipeline(monkeypatch, check_rc=0):
    monkeypatch.setattr(package, "analyze_image",
                        lambda p, core, b=None: _image(p.parent, core or "m55_he", 32))

    def fake_objcopy(img, dest):
        dest.write_bytes(b"\0" * img.size)
        img.bin_path = dest

    monkeypatch.setattr(package, "_objcopy", fake_objcopy)
    monkeypatch.setattr(check, "main", lambda *a, **k: check_rc)


def test_main_prints_but_never_runs_setools(tmp_path, monkeypatch, no_setools, capsys):
    _fake_pipeline(monkeypatch)
    out = tmp_path / "pkg"
    assert package.main(str(tmp_path / "a.elf"), "m55_hp", str(out), [str(tmp_path / "b.elf")]) == 0
    printed = capsys.readouterr().out
    assert "app-write-mram" in printed and "app-gen-toc" in printed       # affichées...
    assert not any(any(f in c.lower() for f in FORBIDDEN) for c in no_setools)  # ...jamais exécutées
    cfg = json.loads((out / "M55_HP_HE_mram_cfg.json").read_text(encoding="utf-8"))
    assert {"HP_APP", "HE_APP"} <= set(cfg)


def test_guard_actually_catches_setools(no_setools):
    with pytest.raises(AssertionError, match="SETOOLS"):
        subprocess.run(["./app-write-mram", "-p"])


def test_check_fail_refuses_and_cleans(tmp_path, monkeypatch, capsys):
    _fake_pipeline(monkeypatch, check_rc=1)
    out = tmp_path / "pkg"
    assert package.main(str(tmp_path / "a.elf"), "m55_hp", str(out)) == 1
    assert not list(out.glob("*.bin")) and not list(out.glob("*.json"))
    assert "app-write-mram" not in capsys.readouterr().out  # aucune commande SETOOLS proposée


def test_two_images_same_core_rejected(tmp_path, monkeypatch):
    _fake_pipeline(monkeypatch)
    assert package.main(str(tmp_path / "a.elf"), "m55_hp", str(tmp_path / "p"), [str(tmp_path / "b.elf")]) == 0
    monkeypatch.setattr(package, "analyze_image", lambda p, core, b=None: _image(p.parent, "m55_hp"))
    assert package.main(str(tmp_path / "a.elf"), "m55_hp", str(tmp_path / "p2"), [str(tmp_path / "b.elf")]) == 2


def test_missing_objcopy_returns_2(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "analyze_image", lambda p, core, b=None: _image(p.parent, "m55_hp"))
    monkeypatch.setattr(toolchain, "find", lambda name, tdir=None: None)
    assert package.main(str(tmp_path / "a.elf"), "m55_hp", str(tmp_path / "p")) == 2


def test_unreadable_elf_returns_2(tmp_path):
    bad = tmp_path / "bad.elf"
    bad.write_bytes(b"not an elf")
    assert package.main(str(bad), "m55_hp", str(tmp_path / "p")) == 2
    assert package.main(str(tmp_path / "absent.elf"), "m55_hp", str(tmp_path / "p")) == 2


# ----------------------------------------------------------------------------- réels


@pytest.fixture(scope="module")
def gcc():
    p = toolchain.find("arm-none-eabi-gcc")
    if p is None or toolchain.find("arm-none-eabi-objcopy") is None:
        pytest.skip("arm-none-eabi-gcc / objcopy absents")
    return p


@pytest.fixture
def short_dir():
    d = Path(tempfile.mkdtemp(prefix="e7p"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _mk(gcc, d, name, core):
    slot, dtcm = B.slot(core), B.region("dtcm_" + core.split("_")[1])
    return elfgen.build_elf(gcc, d, name, code_base=slot.base, ram_base=dtcm.base, sp=dtcm.end)


def _expected_image(elf: Path, core: str) -> bytes:
    from elftools.elf.elffile import ELFFile

    slot = B.slot(core)
    with elf.open("rb") as f:
        segs = [s for s in ELFFile(f).iter_segments()
                if s["p_type"] == "PT_LOAD" and s["p_filesz"] and slot.base <= s["p_paddr"] < slot.end]
        base = min(s["p_paddr"] for s in segs)
        buf = bytearray(max(s["p_paddr"] + s["p_filesz"] for s in segs) - base)
        for s in segs:
            buf[s["p_paddr"] - base:s["p_paddr"] - base + s["p_filesz"]] = s.data()
    return bytes(buf)


@pytest.mark.toolchain
def test_real_package_hp_and_he(gcc, short_dir, no_setools):
    hp, he = _mk(gcc, short_dir, "hp", "m55_hp"), _mk(gcc, short_dir, "he", "m55_he")
    out = short_dir / "pkg"
    assert package.main(str(hp), None, str(out), [str(he)]) == 0
    cfg_path = out / "M55_HP_HE_mram_cfg.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["HP_APP"]["mramAddress"] == f"0x{B.slot('m55_hp').base:08X}"
    assert cfg["HE_APP"]["mramAddress"] == f"0x{B.slot('m55_he').base:08X}"
    assert (out / cfg["HP_APP"]["binary"]).read_bytes() == _expected_image(hp, "m55_hp")
    assert (out / cfg["HE_APP"]["binary"]).read_bytes() == _expected_image(he, "m55_he")
    for elf, core in ((hp, "m55_hp"), (he, "m55_he")):
        atoc = next(r for r in check.run(elf, core, cfg_path).rules if r.id == "atoc")
        assert atoc.verdict == "PASS", atoc.message
    assert not any(any(f in c.lower() for f in FORBIDDEN) for c in no_setools)


@pytest.mark.toolchain
def test_real_package_wrong_core_is_rejected(gcc, short_dir):
    he = _mk(gcc, short_dir, "he", "m55_he")
    assert package.main(str(he), "m55_hp", str(short_dir / "pkg")) == 2  # image dans le slot HE, pas HP
    assert package.main(str(he), "a32_0", str(short_dir / "pkg")) == 2    # cœur non exécutable


@pytest.mark.toolchain
def test_real_package_image_outside_mram_slot_is_rejected(gcc, short_dir):
    # Image HP en SRAM0 (hors slot MRAM) : pas de segment dans le slot -> refus (code 2) sans fichiers.
    sram0 = B.region("sram0")
    elf = elfgen.build_elf(gcc, short_dir, "off", code_base=sram0.base, ram_base=B.region("dtcm_hp").base,
                           sp=B.region("dtcm_hp").end)
    out = short_dir / "pkg"
    assert package.main(str(elf), "m55_hp", str(out)) == 2
    assert not out.exists() or not list(out.glob("*.bin"))


@pytest.mark.toolchain
def test_real_sdk_hello_hp_he(short_dir, no_setools):
    if not SDK_SOLUTION.is_file():
        pytest.skip("template Alif absent")
    if not all(r["found"] for r in toolchain.doctor()
               if r["name"].startswith("pack ") or r["name"] in ("cbuild", "arm-none-eabi-gcc", "cmake", "ninja")):
        pytest.skip("outils/packs absents")
    assert build.main(f"{SDK_SOLUTION}#hello", "m55_hp", "debug", str(short_dir / "b")) == 0
    assert build.main(f"{SDK_SOLUTION}#hello", "m55_he", "debug", str(short_dir / "b")) == 0
    hp = next((short_dir / "b" / "alif" / "hello" / "m55_hp" / "out").rglob("hello.elf"))
    he = next((short_dir / "b" / "alif" / "hello" / "m55_he" / "out").rglob("hello.elf"))
    out = short_dir / "pkg"
    assert package.main(str(hp), "m55_hp", str(out), [str(he)]) == 0
    cfg_path = out / "M55_HP_HE_mram_cfg.json"
    for elf, core in ((hp, "m55_hp"), (he, "m55_he")):
        rep = check.run(elf, core, cfg_path)
        rules = {r.id: r for r in rep.rules}
        assert rules["atoc"].verdict == "PASS", rules["atoc"].message
        assert rules["stack"].verdict != "SKIP", rules["stack"].message
        assert rep.verdict != "FAIL"
