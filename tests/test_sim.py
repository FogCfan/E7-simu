"""Tests de e7twin.sim (agent 04).

Les tests marqués `renode` lancent un vrai Renode 1.17 sur les ELF `hello` du SDK (build/spike/) ;
ils sont sautés proprement si Renode ou les ELF sont absents. Les autres n'ont besoin de rien.
"""

from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import pytest

from e7twin import board as B
from e7twin import sim, toolchain

REPO = Path(__file__).resolve().parents[1]
HELLO_HP = REPO / "build" / "spike" / "hello_hp.elf"
HELLO_HE = REPO / "build" / "spike" / "hello_he.elf"


@pytest.fixture(scope="module")
def brd():
    return B.load()


def _fake_elf(path: Path, paddr: int, data: bytes = b"\x00" * 16) -> Path:
    """ELF32 ARM minimal : un seul PT_LOAD à `paddr` (vaddr = 0, comme les ELF SDK liés en TCM)."""
    ehdr = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF\x01\x01\x01" + b"\x00" * 9, 2, 40, 1, paddr | 1,
                       52, 0, 0x05000400, 52, 32, 1, 0, 0, 0)
    phdr = struct.pack("<IIIIIIII", 1, 84, 0, paddr, len(data), len(data), 5, 4)
    path.write_bytes(ehdr + phdr + data)
    return path


# ----------------------------------------------------------------------------- sans Renode

def test_assign_core_by_load_address(tmp_path, brd):
    hp = _fake_elf(tmp_path / "hp.elf", brd.slot("m55_hp").base)
    he = _fake_elf(tmp_path / "he.elf", brd.slot("m55_he").base + 0x1000)
    assert sim.assign_core(hp, brd) == ("m55_hp", brd.slot("m55_hp").base)
    assert sim.assign_core(he, brd) == ("m55_he", brd.slot("m55_he").base + 0x1000)


def test_assign_core_rejects_unmapped(tmp_path, brd):
    bad = _fake_elf(tmp_path / "bad.elf", 0x0200_0000)  # SRAM0 : pas un slot ATOC
    with pytest.raises(sim.SimError, match="slot ATOC"):
        sim.assign_core(bad, brd)
    with pytest.raises(sim.SimError, match="introuvable"):
        sim.assign_core(tmp_path / "absent.elf", brd)


def test_parse_atoc(tmp_path, brd):
    (tmp_path / "hp.bin").write_bytes(b"\x00" * 8)
    (tmp_path / "he.bin").write_bytes(b"\x00" * 8)
    (tmp_path / "a32.bin").write_bytes(b"\x00" * 8)
    atoc = {
        "HP_APP": {"binary": "hp.bin", "mramAddress": "0x80200000", "cpu_id": "M55_HP", "flags": ["boot"]},
        "HE_APP": {"binary": "he.bin", "mramAddress": "0x80000000", "cpu_id": "M55_HE", "flags": ["load"]},
        "A32_STUB": {"binary": "a32.bin", "loadAddress": "0x02000000", "cpu_id": "A32_0", "flags": ["load", "boot"]},
    }
    p = tmp_path / "atoc.json"
    p.write_text(json.dumps(atoc), encoding="utf-8")
    imgs = {i.core: i for i in sim.parse_atoc(p, brd)}
    assert set(imgs) == {"m55_hp", "m55_he"}  # A32 ignoré (inerte en v1)
    assert imgs["m55_hp"].boot and imgs["m55_hp"].load_address == 0x8020_0000
    assert not imgs["m55_he"].boot and imgs["m55_he"].vtor == 0x8000_0000


def test_parse_atoc_errors(tmp_path, brd):
    p = tmp_path / "atoc.json"
    p.write_text(json.dumps({"HP": {"binary": "manque.bin", "mramAddress": "0x80200000",
                                    "cpu_id": "M55_HP", "flags": ["boot"]}}), encoding="utf-8")
    with pytest.raises(sim.SimError, match="introuvable"):
        sim.parse_atoc(p, brd)
    p.write_text("pas du json", encoding="utf-8")
    with pytest.raises(sim.SimError, match="illisible"):
        sim.parse_atoc(p, brd)


def test_mpu_prime_covers_mram_and_xip(brd):
    cmds = sim.mpu_prime(brd, "cpu_hp")
    assert cmds[-1].endswith("0x5 cpu_hp")  # ENABLE | PRIVDEFENA en dernier
    joined = "\n".join(cmds)
    assert f"{brd.region('mram').base | 2:#x}" in joined and f"{brd.region('ospi_xip').base | 2:#x}" in joined


def test_main_errors_return_2(tmp_path):
    assert sim.main(["/n/existe/pas.elf"], None, None, None, True, "x", 1) == 2
    assert sim.main([], None, None, None, True, "x", 1) == 2           # aucune image
    elf = _fake_elf(tmp_path / "hp.elf", B.load().slot("m55_hp").base)
    assert sim.main([str(elf)], str(elf), None, None, True, "x", 1) == 2  # deux images pour m55_hp


# ----------------------------------------------------------------------------- avec Renode

needs_renode = [
    pytest.mark.renode,
    pytest.mark.skipif(toolchain.find("renode") is None, reason="Renode absent"),
    pytest.mark.skipif(not HELLO_HP.is_file(), reason="build/spike/hello_hp.elf absent (agent 02)"),
]


def _renode(fn):
    for m in needs_renode:
        fn = m(fn)
    return fn


@_renode
def test_main_hello_hp_expect_ok(capfd):
    assert sim.main([], str(HELLO_HP), None, None, True, "Hello World!", 60) == 0
    assert "[uart4] Hello World!" in capfd.readouterr().out


@_renode
def test_main_expect_absent_returns_1():
    assert sim.main([], str(HELLO_HP), None, None, True, "TEXTE ABSENT", 8) == 1


@_renode
@pytest.mark.skipif(not HELLO_HE.is_file(), reason="build/spike/hello_he.elf absent")
def test_main_assigns_he_core_by_address():
    # ELF passé nu : le cœur est déduit du slot MRAM (HE) ; le LPUART n'est pas utilisé par le SDK (UART4)
    assert sim.main([str(HELLO_HE)], None, None, None, True, "Hello World!", 60) == 0


@_renode
def test_session_roundtrip():
    with sim.Session() as s:
        s.load_elf("m55_hp", HELLO_HP)
        s.start()
        assert s.run_until("Hello World!", 60)
        s.pause()
        # la table des vecteurs est bien en MRAM au slot HP : SP initial = fin de DTCM HP
        sp = int.from_bytes(s.read_mem(B.load().slot("m55_hp").base, 4), "little")
        assert sp == B.load().region("dtcm_hp").end
        assert any(ln.uart == "uart4" and ln.text == "Hello World!" and ln.core == "m55_hp"
                   for ln in s.uart_lines())


# ----------------------------------------------------------------------------- MHU / mesures : sans Renode

def test_mhu_pairing(brd):
    from e7twin.renode import stubs

    links = stubs.mhu_links(brd)
    assert len(links) == 20  # 24 fenêtres : 4 liens HP<->HE complets (8 fenêtres) + 16 liens à une extrémité
    full = {k: v for k, v in links.items() if v["tx"] and v["rx"]}
    assert set(full) == {"m55hp_m55he_0", "m55hp_m55he_1", "m55he_m55hp_0", "m55he_m55hp_1"}
    hp_he = full["m55hp_m55he_0"]
    assert (hp_he["tx"].visible_to, hp_he["tx"].base, hp_he["tx"].irq) == (("m55_hp",), 0x4009_0000, {"m55_hp": 42})
    assert (hp_he["rx"].visible_to, hp_he["rx"].base, hp_he["rx"].irq) == (("m55_he",), 0x4008_0000, {"m55_he": 41})
    # les liens vers les A32 / le SE n'ont qu'une extrémité simulée
    assert links["m55hp_a32_0"]["rx"] is None and links["a32_m55hp_0"]["tx"] is None


def test_mhu_overlay_registers_both_cpus(tmp_path, brd):
    from e7twin.renode import stubs

    repl = stubs.generate(brd, tmp_path)
    blk = repl[repl.index("mhu_m55hp_m55he_0:"):].split("\n\n")[0]
    assert "address: 0x40090000; cpu: cpu_hp" in blk and "address: 0x40080000; cpu: cpu_he" in blk
    src = (tmp_path / "mhu_m55hp_m55he_0.py").read_text(encoding="utf-8")
    compile(src, "mhu_m55hp_m55he_0.py", "exec")  # syntaxe valide (IronPython 2.7 et Python 3)
    assert "'nvic': 'nvic_he'" in src and "'irq': 41" in src


def test_mhu_events_parsing_drains_queue(tmp_path):
    s = sim.Session()
    s._workdir = tmp_path
    (tmp_path / "renode.log").write_text(
        "[01:00:00.1] [INFO] x: bruit\n"
        "[01:00:00.2] [INFO] mhu_a: MHU_EVT t_us=1500000 link=m55hp_m55he_0 kind=send src=m55_hp dst=m55_he ch=0 val=0x1001\n"
        "[01:00:00.3] [INFO] mhu_a: MHU_EVT t_us=1500100 link=m55hp_m55he_0 kind=ack src=m55_he dst=m55_hp ch=0 val=0x1001\n"
        "[01:00:00.4] [INFO] mhu_a: MHU_EVT t_us=15", encoding="utf-8")  # dernière ligne incomplète
    ev = s.mhu_events()
    assert [(e.kind, e.src, e.dst, e.channel, e.value) for e in ev] == [
        ("send", "m55_hp", "m55_he", 0, 0x1001), ("ack", "m55_he", "m55_hp", 0, 0x1001)]
    assert ev[0].t_virtual_s == pytest.approx(1.5) and ev[1].link == "m55hp_m55he_0"
    assert s.mhu_events() == []  # file vidée
    with (tmp_path / "renode.log").open("a", encoding="utf-8") as f:
        f.write("00 link=l kind=send src=m55_he dst=none ch=1 val=0x2\n")  # la ligne se complète
    assert len(s.mhu_events()) == 1


def test_fault_str():
    f = sim.Fault("m55_hp", "hardfault", 0x80200370, 0x10000, 0x40000000)
    assert str(f) == "hardfault m55_hp PC=0x80200370 CFSR=0x10000 HFSR=0x40000000"


# ----------------------------------------------------------------------------- MHU / mesures : avec Renode

SPIKE = REPO / "e7twin" / "renode" / "spike"


@pytest.fixture(scope="module")
def spike_elfs():
    """Compile et exécute les scénarios mhu_pingpong et fault via run_spike.py (nécessite gcc + Renode)."""
    import subprocess
    import sys

    if toolchain.find("renode") is None or toolchain.find("arm-none-eabi-gcc") is None:
        pytest.skip("Renode ou arm-none-eabi-gcc absent")
    out = {}
    for name in ("mhu_pingpong", "fault"):
        cp = subprocess.run([sys.executable, str(SPIKE / "run_spike.py"), "--only", name],
                            capture_output=True, text=True, cwd=REPO, timeout=300)
        out[name] = cp
    return out


@pytest.mark.renode
def test_mhu_pingpong_100(spike_elfs):
    cp = spike_elfs["mhu_pingpong"]
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "PONG 100" in cp.stdout and "SERVED 100" in cp.stdout


@pytest.mark.renode
def test_mhu_events_journal(spike_elfs):
    with sim.Session() as s:
        s.load_elf("m55_hp", REPO / "build/spike/mhu_hp.elf")
        s.load_elf("m55_he", REPO / "build/spike/mhu_he.elf")
        s.start()
        assert s.run_until("PONG 100", 60)
        ev = s.mhu_events()
        sends = [e for e in ev if e.kind == "send"]
        assert len(sends) == 200 and len([e for e in ev if e.kind == "ack"]) == 200
        assert {(e.src, e.dst) for e in sends} == {("m55_hp", "m55_he"), ("m55_he", "m55_hp")}
        assert [e.value for e in sends if e.src == "m55_hp"][:2] == [0x1001, 0x1002]
        assert all(a.t_virtual_s <= b.t_virtual_s for a, b in zip(ev, ev[1:]))


@pytest.mark.renode
def test_hardfault_detected(spike_elfs):
    assert spike_elfs["fault"].returncode == 0, spike_elfs["fault"].stdout
    with sim.Session() as s:
        s.load_elf("m55_hp", REPO / "build/spike/fault_hp.elf")
        s.start()
        assert s.run_until("HARDFAULT", 30)
        f = [x for x in s.faults() if x.kind == "hardfault"]
        assert f and f[0].core == "m55_hp" and f[0].cfsr & 0x10000 and f[0].hfsr & 0x40000000  # UNDEFINSTR, FORCED


@_renode
def test_cpu_stats_and_watermark():
    with sim.Session() as s:
        s.load_elf("m55_hp", HELLO_HP)
        assert s.stack_watermark("m55_hp") == 0  # peinte, pas encore utilisée
        s.start()
        first = s.cpu_stats("m55_hp")["instructions"]
        assert s.run_until("Hello World!", 60)
        st = s.cpu_stats("m55_hp")
        assert st["instructions"] > first
        assert st["pc"] != 0 and st["halted_or_wfi"] in (True, False)
        assert (s.stack_watermark("m55_hp") or 0) > 0
        assert s.virtual_time() > 0


LOAD_LOOP_HP = Path(__file__).resolve().parent.parent / "build" / "examples" / "load_loop_hp.elf"


def _ratio(s, dt=1.0):
    import time
    v0, t0 = s.virtual_time(), time.monotonic()
    time.sleep(dt)
    return (s.virtual_time() - v0) / (time.monotonic() - t0)


@_renode
@pytest.mark.skipif(not LOAD_LOOP_HP.is_file(), reason="build/examples/load_loop_hp.elf absent")
def test_set_advance_immediately_hot():
    with sim.Session() as s:
        s.load_elf("m55_hp", LOAD_LOOP_HP)
        s.start()
        s.run_until("zzz-absent", 8)  # laisse passer le palier de boot
        assert _ratio(s) < 2.0
        s.set_advance_immediately(True)
        assert s.advance_immediately is True
        assert _ratio(s) > 1.5  # borné par la charge (le WFI seul est accéléré)
        s.set_advance_immediately(False)
        # l'avance prise sur l'horloge réelle est d'abord résorbée (temps virtuel figé), puis le ratio revient à 1
        for _ in range(30):
            if _ratio(s, 0.5) > 0.8:
                break
        r = _ratio(s)
        assert 0.5 < r < 2.0, r
        s.set_advance_immediately(True)
        s.reset()
        assert s.advance_immediately is True


@_renode
@pytest.mark.skipif(not LOAD_LOOP_HP.is_file(), reason="build/examples/load_loop_hp.elf absent")
def test_three_successive_loads_each_restart_from_zero():
    """Recharger une image sur une session déjà démarrée remet la machine à zéro (temps virtuel, état)."""
    with sim.Session() as s:
        s.set_advance_immediately(True)
        for _ in range(3):
            s.load_elf("m55_hp", LOAD_LOOP_HP)
            s.start()
            time.sleep(7)
            vt = s.virtual_time()
            assert 0 < vt < 60, vt   # avant le correctif : le temps repartait de l'état précédent
            assert s.cpu_stats("m55_hp")["instructions"] > 0
            assert s.read_framebuffer() is None  # écran inactif, mais la capture ne casse pas le monitor
            s.pause()
        mon = s._mon
    assert mon.proc.poll() is not None  # aucun renode.exe orphelin


def test_lost_message_format_and_journal_rotation(tmp_path, monkeypatch):
    p = Path("build/logs/renode_x.log")
    m = sim.format_lost("[WinError 10054] reset", None, p)
    assert m.startswith("connexion au monitor perdue : [WinError 10054] reset")
    assert "encore vivant" in m and m.endswith(f" — journal : {p}")
    m = sim.format_lost("x", 3, p)
    assert "terminé (code 3)" in m and m.endswith(str(p))
    monkeypatch.setattr(sim, "REPO", tmp_path)
    d = tmp_path / "build" / "logs"
    d.mkdir(parents=True)
    for i in range(14):
        f = d / f"renode_old{i:02d}.log"
        f.write_text("x")
        import os
        os.utime(f, (1000 + i, 1000 + i))
    new = sim._new_journal(4242)
    new.write_text("x")
    left = sorted(f.name for f in d.glob("renode_*.log"))
    assert len(left) == 10 and new.name in left and "renode_old00.log" not in left and "renode_old13.log" in left


# ----------------------------------------------------------------------------- robustesse (cycle 4)

def test_free_port_never_repeats():
    ports = [sim._free_port() for _ in range(200)]
    assert len(set(ports)) == len(ports)


def test_unmapped_parsing_and_classification(tmp_path):
    s = sim.Session()
    s._workdir = tmp_path
    (tmp_path / "renode.log").write_text(
        # PPB (registre propre au M55) : accès normal sur la carte, pas une faute
        "[07:00:00.1] [WARNING] sysbus: [cpu_hp: 0x802009DC] WriteDoubleWord to non existing peripheral at 0xE001E004, value 0x2E5.\n"
        # bloc d'horloges non modélisé (0x1A...) : périphérique réel, pas une faute
        "[07:00:00.2] [WARNING] sysbus: [cpu_hp: 0x802008FE] (tag: 'aon') ReadDoubleWord from non existing peripheral at 0x1A604008, returning 0x00000000.\n"
        # sous la DTCM : aucune mémoire ni périphérique -> BusFault sur la carte
        "[07:00:01.0] [WARNING] sysbus: [cpu_hp: 0x11A] WriteDoubleWord to non existing peripheral at 0x1FFFFEFC, value 0x0.\n"
        "[07:00:01.1] [ERROR] cpu_he: CPU abort [PC=0x40000]: Trying to execute code outside RAM or ROM at 0x00040000.\n",
        encoding="utf-8")
    ev = s.unmapped_events()
    assert [(u.core, u.access, u.size, hex(u.addr), u.fault) for u in ev] == [
        ("m55_hp", "write", 4, "0xe001e004", False),
        ("m55_hp", "read", 4, "0x1a604008", False),
        ("m55_hp", "write", 4, "0x1ffffefc", True)]
    assert s.unmapped_events() == []
    fl = s.faults()
    assert [f.kind for f in fl] == ["unmapped", "abort"]
    assert fl[0].pc == 0x11A and fl[1].core == "m55_he" and fl[1].pc == 0x40000


@_renode
def test_expect_absent_never_returns_0_even_with_parallel_sessions():
    """Deux sessions simultanées dans le même processus : chacune garde son monitor/ses ports ; un `expect` absent
    donne toujours 1 (jamais 0), un `expect` présent 0."""
    import concurrent.futures as cf

    def run(expect):
        return sim.main([], str(HELLO_HP), None, None, True, expect, 40 if expect == "Hello World!" else 15)

    with cf.ThreadPoolExecutor(2) as ex:
        a, b = ex.submit(run, "Hello World!"), ex.submit(run, "TEXTE ABSENT")
        assert (a.result(), b.result()) == (0, 1)


def test_kill_tree_posix_uses_killpg_windows_uses_kill(monkeypatch):
    import os
    import signal
    import subprocess
    from types import SimpleNamespace

    calls = []
    proc = SimpleNamespace(pid=4242, kill=lambda: calls.append("kill"),
                           wait=lambda t=None: calls.append("wait"))
    monkeypatch.setattr(sim.os, "name", "posix")
    monkeypatch.setattr(os, "getpgid", lambda pid: 99, raising=False)
    monkeypatch.setattr(os, "killpg", lambda g, s: calls.append(("killpg", g, s)), raising=False)
    sim._kill_tree(proc)
    assert ("killpg", 99, signal.SIGTERM) in calls and "kill" not in calls

    calls.clear()
    monkeypatch.setattr(sim.os, "name", "nt")
    sim._kill_tree(proc)
    assert calls == ["kill"]
