"""Tests de bout en bout des firmwares de reference (examples/, agent 07).

Chaine complete : cbuild (fixture ``examples_built``) -> ``e7twin check`` -> ``e7twin run`` (Renode).
Sautes proprement si la toolchain, les packs ou Renode manquent ; un echec de compilation, lui, echoue.

Ce que ces tests prouvent, et ne prouvent pas :
- les verdicts de ``check`` sont ceux de l'analyse statique (pas de preuve d'execution sur la carte) ;
- ``run`` prouve l'execution dans Renode (fidelite : docs/FIDELITE.md), pas sur la vraie carte.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from e7twin import board as B
from e7twin import check

pytestmark = pytest.mark.toolchain

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = {  # projet -> coeur (voir examples/alif.csolution.yml et tests/conftest.py)
    "hello_hp": "m55_hp",
    "hello_he": "m55_he",
    "helium_dot": "m55_hp",
    "too_big": "m55_he",
    "stack_overflow": "m55_hp",
    "dual_core_mhu_hp": "m55_hp",
    "dual_core_mhu_he": "m55_he",
    "load_loop_hp": "m55_hp",
    "load_loop_he": "m55_he",
}


def _rule(rep: check.Report, rid: str) -> check.RuleResult:
    return next(r for r in rep.rules if r.id == rid)


def _cli(*args: str, cwd: Path, timeout: float = 240) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "e7twin.cli", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# ----------------------------------------------------------------------------- build

def test_all_examples_built(examples_built):
    for name in EXAMPLES:
        for ext in ("elf", "map", "su"):
            f = examples_built / f"{name}.{ext}"
            assert f.is_file() and f.stat().st_size > 0, f


@pytest.mark.parametrize("name", EXAMPLES)
def test_su_contains_the_application_main(examples_built, name):
    """-fstack-usage est bien actif pour le code de l'exemple (sinon la regle `stack` ne voit rien)."""
    assert f"examples/{name}/main.c" in (examples_built / f"{name}.su").read_text(encoding="utf-8", errors="replace").replace("\\", "/")


# ----------------------------------------------------------------------------- check : exemples sains

@pytest.mark.parametrize("name", ["hello_hp", "hello_he", "helium_dot", "dual_core_mhu_hp", "dual_core_mhu_he", "load_loop_hp", "load_loop_he"])
def test_check_ok_examples(name, example_elf, su_dir_of):
    """Aucune regle structurelle en FAIL.

    Le verdict global vaut PASS sans .su (la regle `stack` est alors SKIP, cf. test_check) et WARN avec .su :
    les handlers d'interruption du SDK Alif appellent par pointeur (`blx rN`), donc la pile n'est qu'une
    borne inferieure. WARN y est attendu ; ce n'est pas un defaut de l'exemple.
    """
    core = EXAMPLES[name]
    rep = check.run(example_elf(name), core, su_dir=su_dir_of(name))
    assert rep.verdict in ("PASS", "WARN"), rep.to_dict()
    for rid in ("arch", "placement", "usage", "boot"):
        assert _rule(rep, rid).verdict == "PASS", _rule(rep, rid)
    assert _rule(rep, "stack").verdict in ("PASS", "WARN"), _rule(rep, "stack")
    assert _rule(rep, "atoc").verdict == "SKIP"


@pytest.mark.parametrize("name", ["hello_hp", "hello_he", "helium_dot", "dual_core_mhu_hp", "dual_core_mhu_he", "load_loop_hp", "load_loop_he"])
def test_check_without_su_is_pass(name, example_elf, tmp_path):
    empty = tmp_path / "no_su"
    empty.mkdir()
    rep = check.run(example_elf(name), EXAMPLES[name], su_dir=empty)
    assert rep.verdict == "PASS", rep.to_dict()
    assert _rule(rep, "stack").verdict == "SKIP"


def test_check_cli_exit_codes(example_elf, tmp_path):
    """`e7twin check` de bout en bout : 0 pour un exemple sain, 1 pour too_big."""
    ok = _cli("check", str(example_elf("hello_hp")), "--core", "m55_hp", cwd=tmp_path)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "m55_hp : " in ok.stdout and "FAIL" not in ok.stdout.splitlines()[0]
    bad = _cli("check", str(example_elf("too_big")), "--core", "m55_he", cwd=tmp_path)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "m55_he : FAIL" in bad.stdout
    assert (tmp_path / "reports" / "too_big.check.json").is_file()


# ----------------------------------------------------------------------------- check : too_big

def test_too_big_links_but_check_refuses(example_elf, su_dir_of):
    """too_big compile (DTCM HE declaree a 4 Mo dans son RTE) mais `check` le refuse : c'est le but."""
    rep = check.run(example_elf("too_big"), "m55_he", su_dir=su_dir_of("too_big"))
    assert rep.verdict == "FAIL"
    placement = _rule(rep, "placement")
    assert placement.verdict == "FAIL" and "dtcm_he" in placement.message and "déborde" in placement.message
    assert _rule(rep, "boot").verdict == "FAIL"  # le SP initial est en fin de DTCM fictive (0x20400000)
    assert _rule(rep, "arch").verdict == "PASS"  # c'est bien un firmware M55 valide : seul le placement est en cause


def test_too_big_data_really_exceeds_the_real_dtcm(example_elf):
    """Garde-fou de l'exemple : si la config RTE est un jour corrigee, le tableau de 2 Mo ne doit plus tenir."""
    he = B.load().region("dtcm_he")
    with open(example_elf("too_big"), "rb") as f:
        top = max(s["p_vaddr"] + s["p_memsz"] for s in ELFFile(f).iter_segments()
                  if s["p_type"] == "PT_LOAD" and he.base <= s["p_vaddr"] < he.base + 0x1000000)
    assert top - he.base >= 2 * 1024 * 1024 > he.size


# ----------------------------------------------------------------------------- run (Renode)

def _run_expect(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """`e7twin run ... --headless --expect` ; un seul nouvel essai si le code est 2.

    Le code 2 signifie erreur d'environnement (monitor Renode injoignable / connexion coupee), jamais un
    resultat : observe 2 fois sur ~6 lancements successifs, absent au relancement immediat (voir le compte
    rendu). 0 (texte vu) et 1 (texte absent / fault) ne sont jamais rejoues.
    """
    cp = _cli("run", *args, "--headless", "--timeout", "90", cwd=cwd, timeout=300)
    if cp.returncode == 2:
        cp = _cli("run", *args, "--headless", "--timeout", "90", cwd=cwd, timeout=300)
    return cp


@pytest.mark.renode
@pytest.mark.parametrize("name,flag,text", [
    ("hello_hp", "--hp", "Hello E7 HP"),
    ("hello_he", "--he", "Hello E7 HE"),
    ("helium_dot", "--hp", "MVE OK"),
])
def test_run_prints_expected_text(name, flag, text, example_elf, tmp_path):
    cp = _run_expect([flag, str(example_elf(name)), "--expect", text], tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert text in cp.stdout


@pytest.mark.renode
def test_run_expect_wrong_text_returns_1(example_elf, tmp_path):
    """Le test negatif : `--expect` d'un texte que hello_hp n'affiche pas -> code 1."""
    cp = _run_expect(["--hp", str(example_elf("hello_hp")), "--expect", "Hello E7 HE"], tmp_path)
    assert cp.returncode == 1, cp.stdout + cp.stderr


@pytest.mark.renode
def test_helium_dot_mve_matches_scalar(example_elf, tmp_path):
    """Le MVE n'est pas qu'un texte : les deux sommes affichees sont egales (et non triviales)."""
    cp = _run_expect(["--hp", str(example_elf("helium_dot")), "--expect", "MVE OK"], tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    line = next(ln for ln in cp.stdout.splitlines() if "scalar=" in ln)
    scalar, mve = (int(x.split("=")[1]) for x in line.split("] ", 1)[1].split())
    assert scalar == mve and scalar != 0


# ----------------------------------------------------------------------------- dual_core_mhu (HP <-> HE)

def _atoc_hp_he(tmp_path: Path) -> Path:
    """JSON ATOC HP+HE coherent : adresses = slots de board.load() (meme format que tests/test_check.py)."""
    b = B.load()
    data = {
        "HP_APP": {"binary": "dual_core_mhu_hp.bin", "mramAddress": f"{b.slot('m55_hp').base:#x}",
                   "cpu_id": "M55_HP", "flags": ["boot"], "signed": False},
        "HE_APP": {"binary": "dual_core_mhu_he.bin", "mramAddress": f"{b.slot('m55_he').base:#x}",
                   "cpu_id": "M55_HE", "flags": ["boot"], "signed": False},
    }
    p = tmp_path / "atoc_dual.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.parametrize("name", ["dual_core_mhu_hp", "dual_core_mhu_he"])
def test_dual_core_check_with_atoc(name, example_elf, su_dir_of, tmp_path):
    rep = check.run(example_elf(name), EXAMPLES[name], _atoc_hp_he(tmp_path), su_dir=su_dir_of(name))
    assert rep.verdict in ("PASS", "WARN"), rep.to_dict()  # WARN = pile non bornee (IRQ du SDK par pointeur), cf. plus haut
    for rid in ("arch", "placement", "usage", "boot", "atoc"):
        assert _rule(rep, rid).verdict == "PASS", _rule(rep, rid)


def test_dual_core_elfs_are_in_their_own_slots(example_elf):
    """Les deux images sont des cibles distinctes (HP/HE) : le meme ATOC ne passe pas si on echange les cœurs."""
    rep = check.run(example_elf("dual_core_mhu_hp"), "m55_he")
    assert rep.verdict == "FAIL" and _rule(rep, "placement").verdict == "FAIL"


@pytest.mark.renode
def test_dual_core_run_pong_100(example_elf, tmp_path):
    """Critere de verification du plan : HP+HE ensemble, `run --hp --he --expect "PONG 100"` -> 0."""
    cp = _run_expect(["--hp", str(example_elf("dual_core_mhu_hp")), "--he", str(example_elf("dual_core_mhu_he")),
                      "--expect", "PONG 100"], tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "PONG 100" in cp.stdout
    assert "FAIL" not in cp.stdout


@pytest.mark.renode
def test_dual_core_mhu_events_and_no_uart_interleaving(example_elf):
    """>= 200 evenements MHU (attendu : 400 = 100 PING + 100 PONG, chacun envoye puis acquitte), dans les deux
    sens, et la console UART4 partagee ne recoit que du HP (le HE n'ecrit rien)."""
    from e7twin import sim

    with sim.Session() as s:
        s.load_elf("m55_hp", example_elf("dual_core_mhu_hp"))
        s.load_elf("m55_he", example_elf("dual_core_mhu_he"))
        s.start()
        assert s.run_until("PONG 100", 90), s.uart_text()
        ev = s.mhu_events()
        lines = s.uart_lines()
    assert len(ev) >= 200, len(ev)
    assert {(e.src, e.dst) for e in ev} >= {("m55_hp", "m55_he"), ("m55_he", "m55_hp")}
    assert {e.kind for e in ev} >= {"send", "ack"}
    sends = [e for e in ev if e.kind == "send"]
    assert [e.value for e in sends if e.src == "m55_hp"] == [0x50490000 | n for n in range(1, 101)]
    assert [e.value for e in sends if e.src == "m55_he"] == [0x504F0000 | n for n in range(1, 101)]
    assert [ln.text for ln in lines if ln.text.strip()] == ["HP: MHU ping start", "PONG 100"]


# ----------------------------------------------------------------------------- load_loop (charge cyclique HP + HE)

@pytest.mark.renode
def test_load_loop_run_hp_he_ticks(example_elf, tmp_path):
    """Les deux coeurs tournent ensemble : ticks reguliers, charge en dents de scie (10,30,50 %), pas de melange."""
    cp = _run_expect(["--hp", str(example_elf("load_loop_hp")), "--he", str(example_elf("load_loop_he")),
                      "--expect", "HP tick 3"], tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    for text in ("HP tick 1 load 10%", "HP tick 2 load 30%", "HP tick 3 load 50%", "HE tick 1 load 50%"):
        assert text in cp.stdout, cp.stdout

# ----------------------------------------------------------------------------- stack_overflow
#
# OBSERVE (Renode 1.17.0) : la recursion ecrase la pile bien au-dela des 8 Ko reserves et traverse toute la DTCM ; le
# premier acces sous 0x20000000 est non mappe. Renode ne leve pas de fault : il journalise l'acces et renvoie 0.
# Depuis le cycle 4, sim.Session lit ce journal (UnmappedAccess) et classe un acces hors espace
# memoire/peripheriques comme faute `unmapped` (BusFault sur la carte) : `faults()` la remonte, `e7twin run` sort en 1
# avec "FAULT" sur stderr (docs/FIDELITE.md, « Accès non mappé »).

def _symbol(elf: Path, name: str) -> int:
    with open(elf, "rb") as f:
        sym = ELFFile(f).get_section_by_name(".symtab").get_symbol_by_name(name)[0]
    return sym["st_value"] & ~1


@pytest.mark.renode
def test_stack_overflow_unmapped_fault_in_session(example_elf):
    from e7twin import sim

    elf = example_elf("stack_overflow")
    dtcm = B.load().region("dtcm_hp")
    recurse = _symbol(elf, "recurse")
    with sim.Session() as s:
        s.load_elf("m55_hp", elf)
        s.start()
        assert s.run_until("STACK START", 90)
        # laisse la recursion descendre sous la DTCM (la limite basse de la pile reelle est dtcm.base)
        import time
        deadline = time.time() + 60
        sp = dtcm.end
        while time.time() < deadline and sp >= dtcm.base:
            time.sleep(1)
            s.pause()
            sp = int(s._mon.cmd(f"{s._cpus['m55_hp']} SP").strip().split()[-1], 16)
            if sp >= dtcm.base:
                s.start()
        pc = int(s._mon.cmd(f"{s._cpus['m55_hp']} PC").strip().split()[-1], 16)
        assert sp < dtcm.base, f"SP={sp:#x} toujours dans la DTCM [{dtcm.base:#x}, {dtcm.end:#x})"
        assert abs(pc - recurse) < 0x80, f"PC={pc:#x} hors de recurse() @ {recurse:#x}"
        assert "result=" not in s.uart_text()      # la recursion ne rend jamais la main
        # le premier acces sous la DTCM est remonte comme faute `unmapped` (BusFault sur la carte)
        f = [x for x in s.faults() if x.kind == "unmapped"]
        assert f and f[0].core == "m55_hp", s.faults()


@pytest.mark.renode
def test_stack_overflow_fault_detected(example_elf, tmp_path):
    cp = _cli("run", "--hp", str(example_elf("stack_overflow")), "--headless", "--timeout", "40", cwd=tmp_path, timeout=300)
    assert cp.returncode == 1 and "FAULT" in cp.stderr, cp.stdout + cp.stderr


def test_check_flags_stack_overflow_recursion(example_elf, su_dir_of):
    rep = check.run(example_elf("stack_overflow"), "m55_hp", su_dir=su_dir_of("stack_overflow"))
    msg = _rule(rep, "stack").message
    assert "récursi" in msg and "recurse" in msg, msg
