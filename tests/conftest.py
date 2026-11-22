"""Configuration pytest commune (agent 07).

- Detection des outils via ``e7twin.toolchain`` (aucune logique de recherche dupliquee ici).
- Skips propres pour les markers declares dans pyproject.toml :
    ``toolchain`` : arm-none-eabi-gcc present ;
    ``renode``    : Renode present.
  Les autres tests ne dependent d'aucun outil.
- Fixture ``examples_built`` : compile une fois par session les firmwares de reference de ``examples/``
  (``examples/build_all.py``) dans ``build/examples/``. Sautee si cbuild, GCC, les packs manquent.
  Un echec de compilation, lui, est un echec de test (jamais un skip).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from e7twin import toolchain

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "examples"
EXAMPLES_OUT = ROOT / "build" / "examples"
EXAMPLES = {  # projet -> coeur (voir examples/alif.csolution.yml)
    "hello_hp": "m55_hp",
    "hello_he": "m55_he",
    "helium_dot": "m55_hp",
    "too_big": "m55_he",
    "stack_overflow": "m55_hp",
}


def _have(name: str) -> bool:
    return toolchain.find(name) is not None


def _build_prereqs_missing() -> list[str]:
    """Ce qu'il manque pour compiler les exemples ('' si rien) : outils requis, packs, PowerShell."""
    # Renode (et son .NET embarque) n'est pas necessaire pour compiler
    missing = [n for n in toolchain.missing_required() if "renode" not in n.lower() and ".net" not in n.lower()]
    return missing


def pytest_collection_modifyitems(config, items):
    skip_tc = pytest.mark.skip(reason="arm-none-eabi-gcc absent (voir `e7twin doctor`)")
    skip_rn = pytest.mark.skip(reason="Renode absent (voir `e7twin doctor`)")
    have_gcc, have_renode = _have("arm-none-eabi-gcc"), _have("renode")
    for item in items:
        if "toolchain" in item.keywords and not have_gcc:
            item.add_marker(skip_tc)
        if "renode" in item.keywords and not have_renode:
            item.add_marker(skip_rn)


@pytest.fixture(scope="session")
def gcc() -> Path:
    """Chemin d'arm-none-eabi-gcc (session) ; saute si absent. Source unique pour tous les tests."""
    path = toolchain.find("arm-none-eabi-gcc")
    if path is None:
        pytest.skip("arm-none-eabi-gcc absent")
    return path


@pytest.fixture(scope="session")
def examples_built() -> Path:
    """Dossier ``build/examples`` apres compilation des 5 firmwares de reference (une fois par session)."""
    missing = _build_prereqs_missing()
    if missing:
        pytest.skip("compilation des exemples impossible, manque : " + ", ".join(missing))
    # script multiplateforme, meme interpreteur que pytest
    cmd = [sys.executable, str(EXAMPLES_DIR / "build_all.py")]
    cp = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        pytest.fail(f"examples/build_all.py a echoue (code {cp.returncode}) :\n{cp.stdout[-3000:]}\n{cp.stderr[-3000:]}",
                    pytrace=False)
    absent = [n for n in EXAMPLES if not (EXAMPLES_OUT / f"{n}.elf").is_file()]
    if absent:
        pytest.fail(f"ELF manquants apres build_all : {absent}", pytrace=False)
    return EXAMPLES_OUT


@pytest.fixture
def example_elf(examples_built):
    """``example_elf('hello_hp')`` -> chemin de l'ELF compile."""
    return lambda name: examples_built / f"{name}.elf"


@pytest.fixture
def su_dir_of(examples_built, tmp_path):
    """``su_dir_of('too_big')`` -> dossier ne contenant que le .su de ce projet (build/examples/ les melange tous)."""

    def make(name: str) -> Path:
        d = tmp_path / f"su_{name}"
        d.mkdir(exist_ok=True)
        shutil.copy2(examples_built / f"{name}.su", d / f"{name}.su")
        return d

    return make
