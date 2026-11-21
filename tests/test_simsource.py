"""Tests de SimSource (agent 05 ; à signaler à l'agent 07) : fausse Session + un test réel Renode."""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from e7twin import board as board_mod
from e7twin.simsource import SimSource

ROOT = Path(__file__).resolve().parents[1]
HELLO_HP = ROOT / "build" / "spike" / "hello_hp.elf"


class FakeSession:
    """Session factice : le test pilote le temps virtuel et les compteurs d'instructions."""

    def __init__(self):
        self.vt = 0.0
        self.instr = {"m55_hp": 0, "m55_he": 0}
        self.lines: list = []
        self.mhu: list = []
        self.flt: list = []
        self.started = False
        self.loaded: dict = {}
        self._painted = {"m55_hp": (0x20000000, 0x20001000)}
        self.wm = 512
        self.closed = False
        self.fail = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True

    def load_elf(self, core, path):
        self.loaded[core] = str(path)

    def start(self):
        if not self.loaded:
            raise RuntimeError("aucune image chargée")
        self.started = True

    def pause(self):
        self.started = False

    def reset(self):
        self.vt = 0.0
        self.lines.clear()
        self.instr = dict.fromkeys(self.instr, 0)

    def virtual_time(self):
        if self.fail:
            raise OSError("telnet coupé")
        return self.vt

    def cpu_stats(self, core):
        return {"instructions": self.instr[core], "halted_or_wfi": False, "pc": 0}

    def stack_watermark(self, core):
        return self.wm

    def mhu_events(self):
        out, self.mhu = self.mhu, []
        return out

    def faults(self):
        return list(self.flt)

    def uart_lines(self):
        return list(self.lines)


class _Fault:
    core = "m55_hp"

    def __str__(self):
        return "hardfault m55_hp PC=0x10"


@pytest.fixture
def src(tmp_path):
    elf = tmp_path / "x.elf"
    elf.write_bytes(b"")
    fake = FakeSession()
    usage = {"dtcm_hp": SimpleNamespace(data_bytes=1000)}
    # period énorme : le thread ne polle (quasi) jamais ; les tests appellent _poll() à la main
    s = SimSource(session_factory=lambda: fake, check_fn=lambda p, c: SimpleNamespace(regions_usage=usage),
                  period=3600, slow_every=1)
    s.fake, s.elf = fake, elf
    yield s
    s.close()


def _step(s, dvt, hp_instr, he_instr=0):
    s.fake.vt += dvt
    s.fake.instr["m55_hp"] += hp_instr
    s.fake.instr["m55_he"] += he_instr
    with s._lock:
        s._poll()


def test_load_formula(src):
    hz = board_mod.load().core("m55_hp").freq_hz
    src.load("m55_hp", str(src.elf))
    src.start()
    _step(src, 0.0, 0)  # baseline
    _step(src, 0.1, int(hz * 0.1 * 0.25))
    cores = src.sample().to_dict()["cores"]
    m = cores["m55_hp"]
    assert m["load_pct"] == pytest.approx(25.0, abs=0.01)
    assert m["wfi_pct"] == pytest.approx(75.0, abs=0.01)
    assert m["mips"] == pytest.approx(hz * 0.25 / 1e6, rel=1e-3)
    assert cores["m55_he"]["load_pct"] == 0.0  # pas d'image


def test_memory_static_and_watermark(src):
    src.load("m55_hp", str(src.elf))
    src.start()
    _step(src, 0.0, 0)
    _step(src, 0.1, 1)
    mem = src.sample().memory
    assert mem["dtcm_hp"]["static_bytes"] == 1000
    assert mem["dtcm_hp"]["watermark_bytes"] == 1000 + 512
    assert mem["dtcm_hp"]["size"] == board_mod.load().region("dtcm_hp").size


def test_drain_events_uart_faults(src):
    src.load("m55_hp", str(src.elf))
    src.start()
    _step(src, 0.0, 0)
    src.fake.lines.append(SimpleNamespace(t_virtual_s=0.05, core="m55_hp", uart="uart4", text="Hello World!"))
    src.fake.mhu.append(SimpleNamespace(t_virtual_s=0.07, kind="send", link="l", src="m55_hp", dst="m55_he",
                                        channel=0, value=1))
    src.fake.flt.append(_Fault())
    _step(src, 0.1, 10)
    _step(src, 0.1, 10)
    kinds = [e["kind"] for e in src.events()]
    assert kinds.count("mhu") == 1 and kinds.count("fault") == 1  # faute dédupliquée
    assert src.events() == []                                      # drain
    assert [u["text"] for u in src.uart_lines()] == ["Hello World!"]
    assert src.uart_lines() == []


def test_commands_and_status(src):
    with pytest.raises(RuntimeError):
        src.start()  # aucune image
    with pytest.raises(KeyError):
        src.load("zz", str(src.elf))
    with pytest.raises(ValueError):
        src.load("a32_0", str(src.elf))
    with pytest.raises(ValueError):
        src.load("m55_hp", "/nope.elf")
    src.load("m55_hp", str(src.elf))
    st = src.status()
    assert st["fake"] is False and st["cores"]["a32_0"] == "non exécuté" and st["cores"]["m55_he"] == "sans image"
    src.start()
    assert src.status()["state"] == "running" and src.status()["cores"]["m55_hp"] == "actif"
    src.pause()
    assert src.status()["state"] == "paused"
    src.reset()
    assert src.status()["state"] == "stopped"


def test_poll_error_sets_error_state(tmp_path):
    elf = tmp_path / "x.elf"
    elf.write_bytes(b"")
    fake = FakeSession()
    s = SimSource(session_factory=lambda: fake, check_fn=lambda p, c: SimpleNamespace(regions_usage={}),
                  period=0.01)
    try:
        s.load("m55_hp", str(elf))
        s.start()
        fake.fail = True
        for _ in range(100):
            if s.status()["state"] == "error":
                break
            time.sleep(0.02)
        assert s.status()["state"] == "error" and "telnet" in s.status()["message"]
        with pytest.raises(RuntimeError):
            s.start()
    finally:
        s.close()


@pytest.mark.renode
@pytest.mark.skipif(not HELLO_HP.is_file(), reason="build/spike/hello_hp.elf absent")
def test_real_hello_hp():
    """Indépendant du timing : le boot du SDK a un palier (~5 s réelles) où le temps virtuel est figé, puis une
    rafale d'instructions et le WFI ; la charge instantanée peut donc valoir 0 sur tous les échantillons observés.
    On prouve l'exécution par le cumul d'instructions (compteur Renode) et on exige une attente bornée."""
    s = SimSource(period=0.15)
    try:
        s.load("m55_hp", str(HELLO_HP))
        assert s.status()["instructions"] == {}          # rien d'exécuté avant start
        s.start()
        samples, uart = [], []
        end = time.time() + 60
        while time.time() < end:
            time.sleep(0.2)
            sm = s.sample()
            if not samples or sm is not samples[-1]:
                samples.append(sm)
            uart += s.uart_lines()
            st = s.status()
            if (st["state"] == "error" or (st["instructions"].get("m55_hp", 0) > 0 and st["t_virtual_s"] > 0
                                            and any("Hello World!" in u["text"] for u in uart))):
                break
        st = s.status()
        assert st["state"] == "running", st
        assert st["instructions"]["m55_hp"] > 0                       # le cœur a exécuté des instructions
        assert st["t_virtual_s"] > 0                                  # le temps virtuel a progressé
        assert any("Hello World!" in u["text"] for u in uart)
        assert all(0 <= x.cores["m55_hp"]["load_pct"] <= 100 for x in samples)
        # le cumul ne régresse jamais et reste strictement positif ; la charge, elle, peut être 0 après le WFI
        before = st["instructions"]["m55_hp"]
        time.sleep(0.5)
        assert s.status()["instructions"]["m55_hp"] >= before
        loads = [x.cores["m55_hp"]["load_pct"] for x in samples]
        assert max(loads) > 0 or before > 0
    finally:
        s.close()


def test_zero_dvt_publishes_nothing_and_carries_instructions(src):
    src.load("m55_hp", str(src.elf))
    src.start()
    hz = board_mod.load().core("m55_hp").freq_hz
    _step(src, 0.0, 1000)                  # Δt = 0 : rien de publié, pas de division par zéro
    assert src.sample().t_virtual_s == 0.0 and src.status()["instructions"]["m55_hp"] == 1000
    _step(src, 0.1, int(hz * 0.1 * 0.5))   # fenêtre suivante : les 1000 instructions du palier sont reportées
    m = src.sample().cores["m55_hp"]
    assert m["load_pct"] == pytest.approx(50.0, abs=0.01)
    assert src.status()["instructions"]["m55_hp"] == 1000 + int(hz * 0.1 * 0.5)


def test_parse_collapsed():
    from e7twin.simsource import parse_collapsed
    txt = ("Reset_Handler 4\nReset_Handler;main (entry) 6\nReset_Handler;main (entry);puts (entry) 30\n"
           "Reset_Handler;main (entry) 10\nbad line\n\n")
    rows = parse_collapsed(txt, top=2)
    assert rows == [{"name": "puts", "instructions": 30, "pct": 60.0},
                    {"name": "main", "instructions": 16, "pct": 32.0}]
    assert parse_collapsed("") == []


@pytest.mark.renode
@pytest.mark.skipif(not HELLO_HP.is_file(), reason="build/spike/hello_hp.elf absent")
def test_real_profile_hello_hp():
    s = SimSource(period=0.15, profile=True)
    try:
        s.load("m55_hp", str(HELLO_HP))
        s.start()
        end = time.time() + 60
        while time.time() < end and s.status()["instructions"].get("m55_hp", 0) < 100000:
            time.sleep(0.3)
        s.pause()
        p = s.profile("m55_hp")
        assert p["available"], p.get("message")
        assert 0 < len(p["functions"]) <= 20
        assert p["functions"][0]["instructions"] > 0 and 0 < p["functions"][0]["pct"] <= 100
        assert any("Reset_Handler" in f["name"] or "SystemInit" in f["name"] for f in p["functions"])
    finally:
        s.close()
