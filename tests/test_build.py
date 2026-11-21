"""Tests de e7twin.build (agent 02 ; fichier relevant de l'agent 07).

Unitaires : faux outils (``build._run`` et ``toolchain.doctor`` remplacés), aucun outil requis.
Réels (``@pytest.mark.toolchain``) : sautés proprement si GCC / cbuild / packs / template absents.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

from e7twin import board as board_mod
from e7twin import build, check, toolchain

REPO = Path(__file__).resolve().parent.parent
SDK_SOLUTION = REPO / "tools" / "alif_vscode-template" / "alif.csolution.yml"

CSOLUTION = textwrap.dedent(
    """\
    solution:
      target-types:
        - type: DevKit-E7-HE
          device: Alif Semiconductor::AE722F80F55D5LS:M55_HE
        - type: DevKit-E7-HP
          device: Alif Semiconductor::AE722F80F55D5LS:M55_HP
        - type: DevKit-E8-HP
          device: Alif Semiconductor::AE822FA0E5597LS0:M55_HP
      projects:
        - project: blinky/blinky.cproject.yml
        - project: hello/hello.cproject.yml
    """
)


@pytest.fixture
def sol(tmp_path):
    d = tmp_path / "sdk"
    (d / "hello").mkdir(parents=True)
    (d / "hello" / "hello.cproject.yml").write_text("project: {}\n", encoding="utf-8")
    p = d / "my.csolution.yml"
    p.write_text(CSOLUTION, encoding="utf-8")
    return p


def _all_found(monkeypatch):
    names = ("arm-none-eabi-gcc", "cbuild", "csolution", "cmake", "ninja")
    monkeypatch.setattr(
        toolchain, "doctor",
        lambda tdir=None: [{"name": n, "found": True, "path": n, "version": "x", "hint": "", "required": True}
                           for n in names],
    )
    monkeypatch.setattr(toolchain, "env", lambda base=None, tdir=None: {"PATH": "x", "CFLAGS": "-DFOO"})


# ----------------------------------------------------------------------------- unitaires


def test_memory_usage_parses_table():
    out = textwrap.dedent(
        """\
        [37/37] Linking
        Memory region         Used Size  Region Size  %age Used
                    ITCM:       33664 B       256 KB     12.84%
                    DTCM:          1 MB         1 MB    100.00%
                    MRAM:       42176 B         2 MB      2.01%
        +------------
        """
    )
    rows = build.memory_usage(out)
    assert rows[0].lstrip().startswith("Memory region") and len(rows) == 4
    assert build.memory_usage("rien") == []


def test_select_context_reads_target_type_from_csolution(sol):
    # E7 uniquement : le target E8 HP du même csolution ne doit pas être choisi.
    assert build.select_context(sol, "m55_hp", "debug", "hello") == "hello.debug+DevKit-E7-HP"
    assert build.select_context(sol, "m55_he", "release", "hello") == "hello.release+DevKit-E7-HE"


def test_select_context_errors(sol, tmp_path):
    with pytest.raises(build.BuildError, match="plusieurs projets"):
        build.select_context(sol, "m55_hp", "debug", None)
    with pytest.raises(build.BuildError, match="absent"):
        build.select_context(sol, "m55_hp", "debug", "nope")
    other = tmp_path / "other.csolution.yml"
    other.write_text(
        "solution:\n  target-types:\n    - {type: X, device: 'V::AE822FA0E5597LS0:M55_HP'}\n"
        "  projects:\n    - project: a/a.cproject.yml\n",
        encoding="utf-8",
    )
    with pytest.raises(build.BuildError, match="aucun target-type"):
        build.select_context(other, "m55_hp", "debug", None)


def test_find_csolution_variants(sol):
    assert build.find_csolution(sol) == (sol, None)
    assert build.find_csolution(sol.parent) == (sol, None)
    assert build.find_csolution(sol.parent / "hello") == (sol, "hello")  # dossier d'un cproject
    assert build.find_csolution(sol.parent / "absent") is None
    assert build._split_target(f"{sol}#hello") == (sol, "hello")


def test_collect_su_prefixes_and_skips_probe_files(tmp_path):
    tmp = tmp_path / "tmp"
    for rel in ("a/main.c.su", "b/main.c.su", "CMakeFiles/4.3.3/CompilerIdC/CMakeCCompilerId.su"):
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("main.c:1:1:main\t8\tstatic\n", encoding="utf-8")
    dest = tmp_path / "out" / "su"
    assert build.collect_su(tmp, dest) == 2
    assert sorted(p.name for p in dest.iterdir()) == ["0000_main.c.su", "0001_main.c.su"]
    assert build.collect_su(tmp_path / "vide", dest) == 0 and not dest.exists()


def test_main_environment_missing_returns_2(sol, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        toolchain, "doctor",
        lambda tdir=None: [{"name": "arm-none-eabi-gcc", "found": False, "path": None, "version": None,
                            "hint": "installer", "required": True}],
    )
    assert build.main(f"{sol}#hello", "m55_hp", "debug", str(tmp_path / "o")) == 2
    assert "manquant" in capsys.readouterr().err


def test_main_usage_errors_return_2(monkeypatch, tmp_path):
    _all_found(monkeypatch)
    assert build.main(str(tmp_path / "rien"), "m55_hp", out_dir=str(tmp_path)) == 2
    assert build.main(str(tmp_path), "a32_0", out_dir=str(tmp_path)) == 2
    assert build.main(str(tmp_path), "m55_hp", "fast", out_dir=str(tmp_path)) == 2


def test_main_csolution_command_env_and_outputs(sol, monkeypatch, tmp_path):
    """cbuild : contexte lu dans le csolution, un -O par cœur, -fstack-usage via CFLAGS, .su/.map copiés."""
    _all_found(monkeypatch)
    calls = []

    def fake_run(cmd, cwd, env):
        calls.append((cmd, env))
        out_root = Path(cmd[cmd.index("-O") + 1])
        elf = out_root / "out" / "hello" / "T" / "debug" / "hello.elf"
        elf.parent.mkdir(parents=True)
        elf.write_bytes(b"ELF")
        (out_root / "tmp" / "1" / "CMakeFiles" / "x.dir").mkdir(parents=True)
        (out_root / "tmp" / "1" / "linker.map").write_text("map", encoding="utf-8")
        (out_root / "tmp" / "1" / "CMakeFiles" / "x.dir" / "main.c.su").write_text(
            "main.c:1:1:main\t8\tstatic\n", encoding="utf-8")
        return 0, "Memory region         Used Size  Region Size  %age Used\n   ITCM:  1 B  256 KB  0.00%\n"

    monkeypatch.setattr(build, "_run", fake_run)
    out = tmp_path / "out"
    assert build.main(f"{sol}#hello", "m55_hp", "debug", str(out)) == 0
    assert build.main(f"{sol}#hello", "m55_he", "release", str(out)) == 0
    (c_hp, env_hp), (c_he, _) = calls
    assert c_hp[c_hp.index("--context") + 1] == "hello.debug+DevKit-E7-HP"
    assert c_he[c_he.index("--context") + 1] == "hello.release+DevKit-E7-HE"
    assert c_hp[c_hp.index("-O") + 1] != c_he[c_he.index("-O") + 1]  # un dossier de sortie par cœur
    assert env_hp["CFLAGS"] == "-DFOO -fstack-usage"                # flag ajouté, CFLAGS existants conservés
    elf = next((out / "my" / "hello" / "m55_hp" / "out").rglob("hello.elf"))
    assert (elf.parent / "hello.map").read_text(encoding="utf-8") == "map"
    assert len(list((elf.parent / "su").glob("*.su"))) == 1


def test_main_compile_failure_returns_1(sol, monkeypatch, tmp_path):
    _all_found(monkeypatch)
    monkeypatch.setattr(build, "_run", lambda cmd, cwd, env: (1, "error: boom"))
    assert build.main(f"{sol}#hello", "m55_hp", "debug", str(tmp_path / "o")) == 1


def test_cmake_toolchain_file_imposes_m55_flags():
    t = (REPO / "cmake" / "e7-m55.toolchain.cmake").read_text(encoding="utf-8")
    for needle in ("-mcpu=cortex-m55", "-mfloat-abi=hard", "-mfpu=auto", "-fstack-usage",
                   "-Wl,-Map=", "--print-memory-usage"):
        assert needle in t, needle


# ----------------------------------------------------------------------------- réels


@pytest.fixture(scope="module")
def real_tools():
    missing = [r["name"] for r in toolchain.doctor()
               if r["name"] in ("arm-none-eabi-gcc", "cmake", "ninja", "cbuild") and not r["found"]]
    if missing:
        pytest.skip(f"outils absents : {missing}")


CMAKE_MAIN = """\
extern unsigned char _estack;
void Reset_Handler(void);
__attribute__((section(".isr_vector"), used)) void (* const vectors[])(void) = {(void (*)(void))&_estack, Reset_Handler};
volatile int sink;
__attribute__((noinline)) int f(int n) { volatile char b[64]; b[0] = (char)n; return b[0]; }
void Reset_Handler(void) { sink = f(1); for (;;) {} }
"""


@pytest.fixture
def short_dir():
    """Dossier de travail COURT : sous Windows gcc n'écrit pas les .su au-delà de MAX_PATH (260)."""
    d = Path(tempfile.mkdtemp(prefix="e7b"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.toolchain
def test_real_cmake_build_with_stack_usage_and_map(real_tools, short_dir):
    tmp_path = short_dir
    b = board_mod.load()
    code, ram = b.slot("m55_hp").base, b.region("dtcm_hp")
    src = tmp_path / "proj"
    src.mkdir()
    (src / "main.c").write_text(CMAKE_MAIN, encoding="utf-8")
    (src / "link.ld").write_text(
        f"ENTRY(Reset_Handler)\n_estack = {ram.end:#x};\n__StackTop = {ram.end:#x};\n"
        f"__StackLimit = {ram.end - 0x2000:#x};\n"
        f"MEMORY {{ FLASH (rx) : ORIGIN = {code:#x}, LENGTH = 0x1000000\n"
        f"RAM (rwx) : ORIGIN = {ram.base:#x}, LENGTH = 0x100000 }}\n"
        "SECTIONS { .isr_vector : { KEEP(*(.isr_vector)) } > FLASH .text : { *(.text*) *(.rodata*) } > FLASH\n"
        ".data : { *(.data*) } > RAM AT > FLASH .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM }\n",
        encoding="utf-8",
    )
    (src / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\nproject(fw C)\nadd_executable(fw main.c)\n"
        "target_link_options(fw PRIVATE -nostartfiles -T${CMAKE_SOURCE_DIR}/link.ld)\n"
        "target_compile_options(fw PRIVATE -ffreestanding)\n",
        encoding="utf-8",
    )
    assert build.main(str(src), "m55_hp", "debug", str(tmp_path / "out")) == 0
    elf = tmp_path / "out" / "proj" / "m55_hp" / "out" / "fw.elf"
    assert elf.is_file() and elf.with_suffix(".map").is_file()
    assert any("main.c.su" in s.name for s in (elf.parent / "su").glob("*.su"))
    out = subprocess.run([str(toolchain.find("arm-none-eabi-readelf")), "-A", str(elf)],
                         capture_output=True, text=True).stdout
    assert "v8.1-M.mainline" in out and "MVE Integer and FP" in out and "VFP registers" in out
    # Le .su collecté rend la règle pile de check évaluable (pas SKIP).
    rep = check.run(elf, "m55_hp")
    assert next(r for r in rep.rules if r.id == "stack").verdict != "SKIP"


@pytest.mark.toolchain
def test_real_sdk_hello_builds_and_check_evaluates_stack(real_tools, short_dir):
    tmp_path = short_dir
    if not SDK_SOLUTION.is_file():
        pytest.skip("template Alif absent (scripts/install_tools.ps1)")
    if not all(r["found"] for r in toolchain.doctor() if r["name"].startswith("pack ")):
        pytest.skip("packs CMSIS absents")
    assert build.main(f"{SDK_SOLUTION}#hello", "m55_hp", "debug", str(tmp_path)) == 0
    elf = next((tmp_path / "alif" / "hello" / "m55_hp" / "out").rglob("hello.elf"))
    assert next((elf.parent / "su").glob("*.su"))
    rep = check.run(elf, "m55_hp")
    assert next(r for r in rep.rules if r.id == "stack").verdict != "SKIP"
