"""Tests du modèle de carte (e7twin.board) — agent 01, en coordination avec l'agent 07.

Les valeurs attendues reprennent la table de docs/E7_FAITS.md (sources DFP Alif).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from e7twin import board as B

KO = 1024
MO = 1024 * KO
YAML_PATH = B.REPO_ROOT / "board" / B.BOARD_FILENAME


@pytest.fixture(scope="module")
def brd() -> B.Board:
    return B.load()


@pytest.fixture()
def raw() -> dict:
    return yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))


def _load_mutated(tmp_path: Path, data: dict) -> B.Board:
    p = tmp_path / "board.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return B.load(p)


def _mem(data: dict, name: str) -> dict:
    return next(m for m in data["memories"] if m["name"] == name)


# ----------------------------------------------------------------------------- chargement


def test_load_default(brd):
    assert [c.id for c in brd.cores] == ["a32_0", "a32_1", "m55_hp", "m55_he"]
    assert isinstance(brd.memories[0], B.Region)


def test_load_env_var(monkeypatch, tmp_path, raw):
    p = tmp_path / "autre.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("E7TWIN_BOARD", str(p))
    assert B.load().core("m55_hp").freq_hz == 400_000_000


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        B.load(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    "cid, typ, freq, mve, npu, executable",
    [
        ("a32_0", "cortex-a32", 800_000_000, False, None, False),
        ("a32_1", "cortex-a32", 800_000_000, False, None, False),
        ("m55_hp", "cortex-m55", 400_000_000, True, "ethos-u55-256", True),
        ("m55_he", "cortex-m55", 160_000_000, True, "ethos-u55-128", True),
    ],
)
def test_cores(brd, cid, typ, freq, mve, npu, executable):
    c = brd.core(cid)
    assert (c.type, c.freq_hz, c.mve, c.npu, c.executable) == (typ, freq, mve, npu, executable)


def test_core_unknown(brd):
    with pytest.raises(KeyError):
        brd.core("m33")


# Table « Mémoire » de docs/E7_FAITS.md
@pytest.mark.parametrize(
    "name, base, size, kind, local, alias_of",
    [
        ("itcm_hp", 0x0000_0000, 256 * KO, "tcm", True, None),
        ("dtcm_hp", 0x2000_0000, 1 * MO, "tcm", True, None),
        ("itcm_he", 0x0000_0000, 256 * KO, "tcm", True, None),
        ("dtcm_he", 0x2000_0000, 256 * KO, "tcm", True, None),
        ("sram0", 0x0200_0000, 4 * MO, "sram", False, None),
        ("sram1", 0x0800_0000, 2560 * KO, "sram", False, None),
        ("sram2", 0x5000_0000, 256 * KO, "tcm", False, "itcm_hp"),
        ("sram3", 0x5080_0000, 1 * MO, "tcm", False, "dtcm_hp"),
        ("sram4", 0x5800_0000, 256 * KO, "tcm", False, "itcm_he"),
        ("sram5", 0x5880_0000, 256 * KO, "tcm", False, "dtcm_he"),
        ("mram", 0x8000_0000, 5632 * KO, "mram", False, None),
    ],
)
def test_memories_table(brd, name, base, size, kind, local, alias_of):
    r = brd.region(name)
    assert (r.base, r.size, r.kind, r.local, r.alias_of) == (base, size, kind, local, alias_of)
    assert r.end == base + size


def test_tcm_visibility(brd):
    assert brd.region("itcm_hp").visible_to == ("m55_hp",)
    assert brd.region("dtcm_he").visible_to == ("m55_he",)
    for alias in ("sram2", "sram3", "sram4", "sram5"):
        assert {"m55_hp", "m55_he"} <= set(brd.region(alias).visible_to)


@pytest.mark.parametrize(
    "core, kind, base, size",
    [
        ("m55_he", "mram", 0x8000_0000, 2 * MO),
        ("m55_hp", "mram", 0x8020_0000, 2 * MO),
        ("m55_he", "ospi", 0xC000_0000, 2 * MO),
        ("m55_hp", "ospi", 0xC020_0000, 2 * MO),
    ],
)
def test_atoc_slots(brd, core, kind, base, size):
    s = brd.slot(core, kind)
    assert (s.base, s.size, s.kind, s.core) == (base, size, kind, core)


def test_slot_default_kind_and_missing(brd):
    assert brd.slot("m55_hp").kind == "mram"
    with pytest.raises(KeyError):
        brd.slot("a32_0")


def test_mram_user_layout(brd):
    assert brd.layout["mram_user"]["base"] == 0x8040_0000
    assert brd.layout["mram_user"]["size"] == 0x0018_0000


# ----------------------------------------------------------------------------- region_at


@pytest.mark.parametrize(
    "addr, core, expected",
    [
        (0x2000_0000, "m55_hp", "dtcm_hp"),
        (0x2000_0000, "m55_he", "dtcm_he"),
        (0x2003_FFFF, "m55_he", "dtcm_he"),
        (0x2004_0000, "m55_he", None),  # HE au-delà de 256 Ko : non mappé
        (0x2004_0000, "m55_hp", "dtcm_hp"),  # HP : DTCM de 1 Mo
        (0x200F_FFFF, "m55_hp", "dtcm_hp"),
        (0x2010_0000, "m55_hp", None),
        (0x0000_0000, "m55_hp", "itcm_hp"),
        (0x0000_0000, "m55_he", "itcm_he"),
        (0x0000_0000, "a32_0", None),  # pas de TCM M55 pour les A32
        (0x5000_0000, "m55_he", "sram2"),  # alias global de l'ITCM HP vu du HE
        (0x0200_0000, "m55_hp", "sram0"),
        (0x8020_0000, "m55_hp", "mram"),
        (0xC020_0000, "m55_hp", "ospi_xip"),
        (0x4901_C000, "m55_hp", None),  # périphérique, pas une mémoire
    ],
)
def test_region_at(brd, addr, core, expected):
    r = brd.region_at(addr, core)
    assert (r.name if r else None) == expected


def test_regions_for_sorted_and_scoped(brd):
    regs = brd.regions_for("m55_he")
    assert [r.base for r in regs] == sorted(r.base for r in regs)
    names = {r.name for r in regs}
    assert "dtcm_he" in names and "dtcm_hp" not in names and "sram3" in names


# ----------------------------------------------------------------------------- périphériques


@pytest.mark.parametrize("n", range(8))
def test_uarts(brd, n):
    u = brd.peripheral(f"uart{n}")
    assert u.type == "uart16550"
    assert u.base == 0x4901_8000 + n * 0x1000
    assert u.irq == {"m55_hp": 124 + n, "m55_he": 124 + n}


def test_lpuart_and_printf(brd):
    lp = brd.peripheral("lpuart")
    assert lp.base == 0x4300_8000 and lp.irq == {"m55_he": 45}
    assert brd.info["printf_uart"] == "uart4"
    assert brd.peripheral("uart4").attrs.get("role") == "printf"


def test_mhu(brd):
    mhus = [p for p in brd.peripherals if p.type in ("mhu_rx", "mhu_tx")]
    assert len(mhus) == 24
    for p in mhus:
        assert p.local and len(p.visible_to) == 1 and 33 <= p.irq[p.visible_to[0]] <= 44
    # HP -> HE canal 0 : TX côté HP (0x40090000, IRQ 42) apparié au RX côté HE (0x40080000, IRQ 41)
    tx = brd.peripheral("mhu_m55hp_m55he_0_tx")
    rx = brd.peripheral("mhu_m55hp_m55he_0_rx")
    assert (tx.base, tx.irq, tx.visible_to) == (0x4009_0000, {"m55_hp": 42}, ("m55_hp",))
    assert (rx.base, rx.irq, rx.visible_to) == (0x4008_0000, {"m55_he": 41}, ("m55_he",))
    assert tx.attrs["link"] == rx.attrs["link"]


def test_sysctrl_blocks_for_stubs(brd):
    expected = {
        "cgu": 0x1A60_2000, "pinmux": 0x1A60_3000, "aon": 0x1A60_4000,
        "clkctl_per_slv": 0x4902_F000, "clkctl_per_mst": 0x4903_F000,
    }
    for name, base in expected.items():
        assert brd.peripheral(name).base == base


def test_everything_sourced(brd):
    items = [*brd.cores, *brd.memories, *brd.atoc_slots, *brd.peripherals]
    for it in items:
        assert it.source.split()[0] in brd.sources, it


def test_to_dict_json(brd):
    d = brd.to_dict()
    s = json.dumps(d)
    back = json.loads(s)
    assert {c["id"] for c in back["cores"]} == {"a32_0", "a32_1", "m55_hp", "m55_he"}
    dtcm = next(m for m in back["memories"] if m["name"] == "dtcm_hp")
    assert dtcm["base"] == 0x2000_0000 and dtcm["end"] == 0x2010_0000 and isinstance(dtcm["size"], int)


# ----------------------------------------------------------------------------- validation


def test_corrupt_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("cores: [ {id: m55_hp, \n  : : :\n", encoding="utf-8")
    with pytest.raises(B.BoardError):
        B.load(p)


def test_not_a_mapping(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- juste\n- une liste\n", encoding="utf-8")
    with pytest.raises(B.BoardError):
        B.load(p)


def test_missing_field(tmp_path, raw):
    del _mem(raw, "sram0")["size"]
    with pytest.raises(B.BoardError, match="size"):
        _load_mutated(tmp_path, raw)


def test_missing_source(tmp_path, raw):
    del _mem(raw, "sram0")["source"]
    with pytest.raises(B.BoardError, match="source"):
        _load_mutated(tmp_path, raw)


def test_overlap_same_core(tmp_path, raw):
    _mem(raw, "sram0")["size"] = 0x0700_0000  # déborde sur sram1 (0x08000000)
    with pytest.raises(B.BoardError, match="chevauchement"):
        _load_mutated(tmp_path, raw)


def test_local_tcm_same_address_different_cores_ok(raw, tmp_path):
    # itcm_hp et itcm_he sont tous deux à 0x0 : légal car privés à des cœurs différents
    assert _load_mutated(tmp_path, raw).region_at(0, "m55_he").name == "itcm_he"


def test_local_tcm_shared_core_overlap(tmp_path, raw):
    _mem(raw, "itcm_he")["visible_to"] = ["m55_he", "m55_hp"]
    with pytest.raises(B.BoardError, match="chevauchement"):
        _load_mutated(tmp_path, raw)


def test_alias_size_mismatch(tmp_path, raw):
    _mem(raw, "sram5")["size"] = 0x0008_0000
    with pytest.raises(B.BoardError, match="sram5"):
        _load_mutated(tmp_path, raw)


def test_alias_unknown_target(tmp_path, raw):
    _mem(raw, "sram5")["alias_of"] = "dtcm_xx"
    with pytest.raises(B.BoardError, match="alias_of"):
        _load_mutated(tmp_path, raw)


def test_slot_outside_mram(tmp_path, raw):
    raw["atoc_slots"][1]["base"] = 0x0200_0000  # slot HP en SRAM0
    with pytest.raises(B.BoardError, match="slot"):
        _load_mutated(tmp_path, raw)


def test_slot_overflows_mram(tmp_path, raw):
    raw["atoc_slots"][1]["size"] = 0x0040_0000  # 0x80200000 + 4 Mo > fin MRAM (0x80580000)
    with pytest.raises(B.BoardError):
        _load_mutated(tmp_path, raw)


def test_slot_bad_kind(tmp_path, raw):
    raw["atoc_slots"][0]["kind"] = "sram"
    with pytest.raises(B.BoardError, match="kind"):
        _load_mutated(tmp_path, raw)


def test_unknown_core_reference(tmp_path, raw):
    _mem(raw, "sram0")["visible_to"].append("m33")
    with pytest.raises(B.BoardError, match="m33"):
        _load_mutated(tmp_path, raw)


# ----------------------------------------------------------------------------- .repl


@pytest.fixture(scope="module")
def repl(brd) -> str:
    return B.generate_repl(brd)


def test_repl_header(repl):
    assert "NE PAS ÉDITER" in repl.splitlines()[1]


def test_repl_two_cpus(repl):
    cpus = re.findall(r"^(\w+): CPU\.CortexM @ sysbus$", repl, re.M)
    assert cpus == ["cpu_hp", "cpu_he"]
    assert repl.count('cpuType: "cortex-m55"') == 2
    assert "CortexA" not in repl and "a32_0" in repl  # A32 absents, mentionnés en commentaire


def test_repl_nvic_per_core(repl):
    for core, freq in (("hp", 400_000_000), ("he", 160_000_000)):
        assert f"nvic: nvic_{core}" in repl
        assert re.search(
            rf"nvic_{core}: IRQControllers\.NVIC @ \{{\s*sysbus new Bus\.BusPointRegistration "
            rf"\{{ address: 0xe000e000; cpu: cpu_{core} \}}\s*\}}\s*-> cpu_{core}@0\s*"
            rf"systickFrequency: {freq}",
            repl,
        )


@pytest.mark.parametrize(
    "mem, addr, cpu, alias",
    [
        ("itcm_hp", "0x00000000", "cpu_hp", "0x50000000"),
        ("dtcm_hp", "0x20000000", "cpu_hp", "0x50800000"),
        ("itcm_he", "0x00000000", "cpu_he", "0x58000000"),
        ("dtcm_he", "0x20000000", "cpu_he", "0x58800000"),
    ],
)
def test_repl_tcm_per_cpu(repl, mem, addr, cpu, alias):
    m = re.search(rf"^{mem}: Memory\.MappedMemory @ \{{(.*?)\}}\n    size: (0x[0-9a-f]+)", repl, re.M | re.S)
    assert m, mem
    body = m.group(1)
    assert f"sysbus new Bus.BusPointRegistration {{ address: {addr}; cpu: {cpu} }}" in body
    assert f"sysbus {alias}" in body


def test_repl_shared_memories_once(repl):
    for name, base in (("sram0", "0x02000000"), ("sram1", "0x08000000"), ("mram", "0x80000000")):
        assert repl.count(f"{name}: Memory.MappedMemory") == 1
        assert f"{name}: Memory.MappedMemory @ sysbus {base}" in repl
    assert "size: 0x00580000" in repl


def test_repl_uarts(repl):
    assert repl.count("UART.NS16550") == 9
    assert repl.count("wideRegisters: true") == 9
    # fenêtre NS16550 limitée à 0x40 : le reste (registres DesignWare) est un stub Python (agent 04)
    assert re.search(r"uart4: UART\.NS16550 @ sysbus <0x4901c000, \+0x40>.*\n\s+wideRegisters: true\n\s+-> nvic_hp@128 \| nvic_he@128", repl)
    assert re.search(r"lpuart: UART\.NS16550 @ sysbus <0x43008000, \+0x40>\n\s+wideRegisters: true\n\s+-> nvic_he@45\n", repl)


def test_repl_file_up_to_date(repl):
    """e7twin/renode/e7.repl doit être régénéré après toute modification du YAML."""
    on_disk = B.DEFAULT_REPL_PATH.read_text(encoding="utf-8")
    assert on_disk == repl, "e7.repl périmé : lancer `python -m e7twin.board --repl`"


def test_main_writes_repl(tmp_path, repl):
    out = tmp_path / "e7.repl"
    assert B.main(["--repl", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == repl
