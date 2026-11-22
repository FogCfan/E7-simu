"""Tests de e7twin.check / e7twin.report (agent 03).

Les ELF sont générés par gcc dans un dossier temporaire de session (jamais versionnés) ; les tests
qui en ont besoin sont marqués ``toolchain`` et sautés proprement si arm-none-eabi-gcc est absent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e7twin import board as board_mod
from e7twin import check, report, toolchain

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
import elfgen  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
B = board_mod.load()
HP, HE = "m55_hp", "m55_he"


def _rule(rep: check.Report, rid: str) -> check.RuleResult:
    return next(r for r in rep.rules if r.id == rid)


@pytest.fixture(scope="session")
def elfs(gcc, tmp_path_factory):
    d = tmp_path_factory.mktemp("elfs")
    hp_slot, he_slot = B.slot(HP), B.slot(HE)
    dtcm_hp, dtcm_he = B.region("dtcm_hp"), B.region("dtcm_he")
    return {
        "ok_hp": elfgen.build_elf(gcc, d, "ok_hp", code_base=hp_slot.base, ram_base=dtcm_hp.base,
                                  sp=dtcm_hp.end),
        "m4": elfgen.build_elf(gcc, d, "m4", code_base=hp_slot.base, ram_base=dtcm_hp.base,
                               sp=dtcm_hp.end, cpu="cortex-m4"),
        "he_dtcm_overflow": elfgen.build_elf(gcc, d, "he_overflow", code_base=he_slot.base,
                                             ram_base=dtcm_he.base, sp=dtcm_he.end,
                                             bss_bytes=300 * 1024),
        "hp_in_he_slot": elfgen.build_elf(gcc, d, "hp_in_he_slot", code_base=he_slot.base,
                                          ram_base=dtcm_hp.base, sp=dtcm_hp.end),
        "he_90pct": elfgen.build_elf(gcc, d, "he_90pct", code_base=he_slot.base,
                                     ram_base=dtcm_he.base, sp=dtcm_he.end,
                                     bss_bytes=int(dtcm_he.size * 0.90)),
        "bad_sp": elfgen.build_elf(gcc, d, "bad_sp", code_base=hp_slot.base, ram_base=dtcm_hp.base,
                                   sp=0x70000000),
        "stack_ok": elfgen.build_elf(gcc, d, "stack_ok", code_base=hp_slot.base, ram_base=dtcm_hp.base,
                                     sp=dtcm_hp.end, variant=1, frame=64),
        "stack_over": elfgen.build_elf(gcc, d, "stack_over", code_base=hp_slot.base,
                                       ram_base=dtcm_hp.base, sp=dtcm_hp.end, variant=1, frame=3000,
                                       stack_size=0x400),
        "stack_warn": elfgen.build_elf(gcc, d, "stack_warn", code_base=hp_slot.base,
                                       ram_base=dtcm_hp.base, sp=dtcm_hp.end, variant=1, frame=900,
                                       stack_size=0x400),
        "recursion": elfgen.build_elf(gcc, d, "recursion", code_base=hp_slot.base,
                                      ram_base=dtcm_hp.base, sp=dtcm_hp.end, variant=2),
        "veneer": elfgen.build_elf(gcc, d, "veneer", code_base=0, ram_base=dtcm_hp.base,
                                   sp=dtcm_hp.end, variant=4, frame=200, far_base=hp_slot.base),
        "indirect": elfgen.build_elf(gcc, d, "indirect", code_base=hp_slot.base,
                                     ram_base=dtcm_hp.base, sp=dtcm_hp.end, variant=3),
        "crt0": elfgen.build_elf(gcc, d, "crt0", code_base=hp_slot.base, ram_base=dtcm_hp.base,
                                 sp=dtcm_hp.end, variant=5, frame=300),
    }


def _atoc(tmp_path, hp=None, he=None, name="atoc.json"):
    """JSON au format SETOOLS (voir docs/E7_FAITS.md, Packaging ATOC) ; adresses par défaut = slots."""
    hp = B.slot(HP).base if hp is None else hp
    he = B.slot(HE).base if he is None else he
    data = {
        "HP_APP": {"binary": "M55_HP_mram_stub.bin", "mramAddress": f"{hp:#x}", "cpu_id": "M55_HP",
                   "flags": ["boot"], "signed": False},
        "HE_APP": {"binary": "M55_HE_mram_stub.bin", "mramAddress": f"{he:#x}", "cpu_id": "M55_HE",
                   "flags": ["boot"], "signed": False},
        "A32_STUB": {"binary": "a32_stub_0.bin", "loadAddress": "0x02000000", "cpu_id": "A32_0",
                     "flags": ["load", "boot"], "signed": True},
    }
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.toolchain
def test_ok_hp_pass(elfs):
    rep = check.run(elfs["ok_hp"], HP)
    assert rep.verdict == "PASS", rep.to_dict()
    for rid in ("arch", "placement", "usage", "boot"):
        assert _rule(rep, rid).verdict == "PASS", _rule(rep, rid)
    assert _rule(rep, "stack").verdict == "PASS", _rule(rep, "stack")  # .su générés par elfgen
    assert _rule(rep, "atoc").verdict == "SKIP"  # pas de JSON fourni
    assert rep.regions_usage["mram"].size_bytes == B.slot(HP).size  # taille de référence = slot


@pytest.mark.toolchain
def test_cortex_m4_fails_arch(elfs):
    rep = check.run(elfs["m4"], HP)
    assert rep.verdict == "FAIL"
    assert _rule(rep, "arch").verdict == "FAIL"
    assert "v7E-M" in _rule(rep, "arch").message


@pytest.mark.toolchain
def test_he_dtcm_300k_fails_placement(elfs):
    rep = check.run(elfs["he_dtcm_overflow"], HE)
    assert rep.verdict == "FAIL"
    r = _rule(rep, "placement")
    assert r.verdict == "FAIL" and "dtcm_he" in r.message
    assert _rule(rep, "arch").verdict == "PASS"


@pytest.mark.toolchain
def test_hp_image_in_he_slot_fails(elfs):
    rep = check.run(elfs["hp_in_he_slot"], HP)
    assert rep.verdict == "FAIL"
    r = _rule(rep, "placement")
    assert r.verdict == "FAIL" and B.slot(HP).name in r.message
    # Le même binaire est bien placé pour HE (HE voit sa RAM locale à une autre adresse : seul le slot compte).
    assert "hors du slot ATOC" in r.message


@pytest.mark.toolchain
def test_occupancy_90pct_warns(elfs):
    rep = check.run(elfs["he_90pct"], HE)
    u = _rule(rep, "usage")
    assert u.verdict == "WARN", u
    assert 89 < rep.regions_usage["dtcm_he"].data_pct < 91  # la réserve de pile (8 Ko) est à part
    assert rep.verdict == "WARN"
    assert _rule(rep, "placement").verdict == "PASS"


@pytest.mark.toolchain
def test_overflow_percentage_is_not_capped(elfs):
    """Un dépassement doit afficher son vrai pourcentage (> 100 %), pas 100 %."""
    rep = check.run(elfs["he_dtcm_overflow"], HE)
    u = rep.regions_usage["dtcm_he"]
    assert u.pct > 100.0 and u.data_pct > 100.0
    assert 117.0 < u.pct < 118.0  # 300 Kio de bss (307 204 o) dans une DTCM de 256 Kio ; ancien code : 100 %
    assert _rule(rep, "usage").verdict == "FAIL"
    assert f"{u.data_pct:.1f} %" in _rule(rep, "usage").message
    assert rep.verdict == "FAIL"


@pytest.mark.toolchain
def test_sp_outside_ram_fails_boot(elfs):
    rep = check.run(elfs["bad_sp"], HP)
    b = _rule(rep, "boot")
    assert b.verdict == "FAIL" and "SP initial" in b.message
    assert rep.verdict == "FAIL"


# --- règle 4 : pile -----------------------------------------------------------------------------


@pytest.mark.toolchain
def test_stack_ok_pass(elfs):
    r = _rule(check.run(elfs["stack_ok"], HP), "stack")
    assert r.verdict == "PASS", r
    assert "deep" in r.message and r.measured >= 64


@pytest.mark.toolchain
def test_stack_exceeded_fails(elfs):
    rep = check.run(elfs["stack_over"], HP)
    r = _rule(rep, "stack")
    assert r.verdict == "FAIL" and "dépassée" in r.message, r
    assert r.measured > 0x400 and rep.verdict == "FAIL"


@pytest.mark.toolchain
def test_stack_over_85pct_warns(elfs):
    r = _rule(check.run(elfs["stack_warn"], HP), "stack")  # 900 + 104 (exception) sur 1024 = 98 %
    assert r.verdict == "WARN" and "non borné" not in r.message, r


@pytest.mark.toolchain
def test_stack_recursion_warns_unbounded(elfs):
    r = _rule(check.run(elfs["recursion"], HP), "stack")
    assert r.verdict == "WARN", r
    assert "non borné" in r.message and "rec" in r.message and "récursion" in r.message


@pytest.mark.toolchain
def test_stack_indirect_call_warns_unbounded(elfs):
    r = _rule(check.run(elfs["indirect"], HP), "stack")
    assert r.verdict == "WARN", r
    assert "non borné" in r.message and "Reset_Handler" in r.message and "blx" in r.message


@pytest.mark.toolchain
def test_stack_crt0_indirect_call_is_modelled(elfs):
    """_mainCRTStartup (liste blanche) : blx → main + .init_array modélisés, PASS, raison dans le détail."""
    r = _rule(check.run(elfs["crt0"], HP), "stack")
    assert r.verdict == "PASS", r
    assert "connu, modélisé" in r.message and "_mainCRTStartup" in r.message
    assert "non borné" not in r.message
    assert r.measured >= 300  # la trame de main (300 o) est bien comptée via l'arête crt0 → main


@pytest.mark.toolchain
def test_stack_non_whitelisted_indirect_still_warns(elfs):
    r = _rule(check.run(elfs["indirect"], HP), "stack")
    assert r.verdict == "WARN" and "connu, modélisé" not in r.message


@pytest.mark.toolchain
def test_stack_follows_linker_veneer(elfs):
    """Code en ITCM (0x0) qui appelle une fonction en MRAM (> 16 Mo) : ld passe par un veneer."""
    from e7twin import stackcheck

    elf = elfs["veneer"]
    graph = stackcheck.call_graph(stackcheck.objdump_path(), elf)
    by_name = {f.name: f for f in graph.values()}
    veneers = [f for f in graph.values() if "veneer" in f.name]
    assert veneers and all(v.thunk for v in veneers), [f.name for f in graph.values()]
    far = by_name["far_fn"]
    assert far.start in veneers[0].calls                      # la cible réelle du veneer est résolue
    assert veneers[0].start in by_name["Reset_Handler"].calls
    r = _rule(check.run(elf, HP), "stack")
    assert "far_fn" in r.message and r.measured >= 200, r     # trame de far_fn comptée via le veneer


@pytest.mark.toolchain
def test_stack_skips_without_su(elfs, tmp_path):
    empty = tmp_path / "vide"
    empty.mkdir()
    r = _rule(check.run(elfs["ok_hp"], HP, su_dir=empty), "stack")
    assert r.verdict == "SKIP" and "-fstack-usage" in r.message


# --- règle 6 : ATOC -----------------------------------------------------------------------------


@pytest.mark.toolchain
def test_atoc_ok_pass(elfs, tmp_path):
    rep = check.run(elfs["ok_hp"], HP, _atoc(tmp_path))
    assert _rule(rep, "atoc").verdict == "PASS", _rule(rep, "atoc")
    assert rep.verdict == "PASS"


@pytest.mark.toolchain
def test_atoc_wrong_mram_address_fails(elfs, tmp_path):
    r = _rule(check.run(elfs["ok_hp"], HP, _atoc(tmp_path, hp=B.slot(HP).base + 0x1000)), "atoc")
    assert r.verdict == "FAIL" and "≠ début de l'image" in r.message, r


@pytest.mark.toolchain
def test_atoc_hp_he_overlap_fails(elfs, tmp_path):
    r = _rule(check.run(elfs["ok_hp"], HP, _atoc(tmp_path, he=B.slot(HP).base + 4)), "atoc")
    assert r.verdict == "FAIL" and "chevauchement" in r.message, r


@pytest.mark.toolchain
def test_atoc_missing_core_entry_fails(elfs, tmp_path):
    p = _atoc(tmp_path)
    p.write_text(json.dumps({k: v for k, v in json.loads(p.read_text()).items() if k != "HP_APP"}))
    r = _rule(check.run(elfs["ok_hp"], HP, p), "atoc")
    assert r.verdict == "FAIL" and "M55_HP" in r.message


@pytest.mark.toolchain
def test_atoc_bad_json_fails(elfs, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{pas du json")
    assert _rule(check.run(elfs["ok_hp"], HP, p), "atoc").verdict == "FAIL"


@pytest.mark.toolchain
def test_atoc_image_in_other_slot_fails(elfs, tmp_path):
    # Image HP placée dans le slot HE, JSON qui annonce HP à l'adresse du slot HE et HE au même endroit.
    r = _rule(check.run(elfs["hp_in_he_slot"], HP, _atoc(tmp_path, hp=B.slot(HE).base)), "atoc")
    assert r.verdict == "FAIL" and "chevauchement" in r.message, r


# --- règle 3 : alias de TCM et réserve pile/tas --------------------------------------------------


def _seg(addr, size):
    return check._Seg(0, addr, addr, 0, size, 6, b"")


def test_usage_merges_tcm_alias_without_double_count():
    dtcm, sram3 = B.region("dtcm_hp"), B.region("sram3")
    assert sram3.alias_of == "dtcm_hp"
    # 1000 o via l'adresse locale, 1000 o via l'alias, dont 500 o en commun (même stockage physique).
    segs = [_seg(dtcm.base, 1000), _seg(sram3.base + 500, 1000)]
    u = check._usage(B, HP, segs)
    assert "sram3" not in u and u["dtcm_hp"].used_bytes == 1500


def test_usage_alias_with_invisible_target_stays_separate():
    sram3 = B.region("sram3")  # dtcm_hp n'est pas visible de HE : l'alias reste sa propre région
    u = check._usage(B, HE, [_seg(sram3.base, 100)])
    assert u["sram3"].used_bytes == 100 and "dtcm_hp" not in u


def test_usage_separates_stack_heap_reserve():
    dtcm = B.region("dtcm_he")
    reserve = [(dtcm.base + 4096, dtcm.end)]  # pile/tas jusqu'à la fin de la DTCM
    u = check._usage(B, HE, [_seg(dtcm.base, dtcm.size)], reserve)["dtcm_he"]
    assert u.pct == 100.0 and u.reserved_bytes == dtcm.size - 4096 and u.data_bytes == 4096
    r = check._rule_usage({"dtcm_he": u})
    assert r.verdict == "PASS" and "réserve pile/tas" in r.message  # ni FAIL ni faux WARN
    over = check.RegionUsage(dtcm.size * 11 // 10, dtcm.size, 110.0, 0)
    assert check._rule_usage({"dtcm_he": over}).verdict == "FAIL"


@pytest.mark.toolchain
def test_main_writes_reports_and_exit_codes(elfs, tmp_path):
    out = tmp_path / "reports"
    assert check.main(elfs["ok_hp"], HP, out_dir=out) == 0
    assert check.main(elfs["m4"], HP, out_dir=out) == 1
    data = json.loads((out / "ok_hp.check.json").read_text(encoding="utf-8"))
    assert data["verdict"] == "PASS" and data["core"] == HP
    assert {r["id"] for r in data["rules"]} == {"arch", "placement", "usage", "stack", "boot", "atoc", "npu"}
    html = (out / "m4.check.html").read_text(encoding="utf-8")
    assert "<html" in html and "FAIL" in html and "<script" not in html


@pytest.mark.toolchain
def test_main_accepts_project_directory(elfs, tmp_path):
    assert check.main(elfs["ok_hp"].parent, HP, out_dir=tmp_path / "r") in (0, 1)


@pytest.mark.parametrize("name,core", [("hello_hp.elf", HP), ("hello_he.elf", HE)])
def test_sdk_spike_elf_passes(name, core):
    elf = ROOT / "build" / "spike" / name
    if not elf.is_file():
        pytest.skip(f"{elf} absent (compilé par l'agent 02)")
    rep = check.run(elf, core)
    assert rep.verdict == "PASS", rep.to_dict()
    dtcm = rep.regions_usage["dtcm_hp" if core == HP else "dtcm_he"]
    assert dtcm.reserved_bytes > 0 and dtcm.data_bytes < dtcm.used_bytes  # pile/tas isolés des données
    assert _rule(rep, "usage").verdict == "PASS"
    assert _rule(rep, "stack").verdict == "SKIP"  # le SDK n'est pas compilé avec -fstack-usage
    assert _rule(rep, "atoc").verdict == "SKIP"


@pytest.mark.parametrize("name,core", [("hello_hp.elf", HP), ("hello_he.elf", HE)])
def test_sdk_spike_elf_atoc_template(name, core, tmp_path):
    elf = ROOT / "build" / "spike" / name
    if not elf.is_file():
        pytest.skip(f"{elf} absent")
    # JSON équivalent à celui du template Alif : mramAddress = base des slots HP / HE.
    rep = check.run(elf, core, _atoc(tmp_path))
    assert _rule(rep, "atoc").verdict == "PASS", _rule(rep, "atoc")


# --- sans toolchain ------------------------------------------------------------------------------


def test_main_missing_target_and_bad_core(tmp_path):
    assert check.main(tmp_path / "nope.elf", HP, out_dir=tmp_path) == 2
    f = tmp_path / "x.elf"
    f.write_bytes(b"pas un elf")
    assert check.main(f, HP, out_dir=tmp_path) == 2
    with pytest.raises(ValueError):
        check.run(f, HP)
    with pytest.raises(KeyError):
        check.run(f, "m55_xx")


def test_report_rendering_is_self_contained_and_escaped(tmp_path):
    rep = check.Report(
        "x/<evil>.elf", HP, "WARN",
        [check.RuleResult("arch", "Arch", "WARN", "a < b & c", 1, 2),
         check.RuleResult("stack", "Pile", "SKIP", "non implémentée")],
        {"mram": check.RegionUsage(900, 1000, 90.0)},
    )
    d = rep.to_dict()
    assert json.loads(json.dumps(d)) == d
    html = report.to_html(rep)
    assert "&lt;evil&gt;" in html and "a &lt; b &amp; c" in html
    assert "http" not in html and "<script" not in html
    jp, hp = report.write(rep, tmp_path, name="demo")
    assert jp.name == "demo.check.json" and hp.name == "demo.check.html"


# ----------------------------------------------------------------------------- règle 7 : arena NPU

def _npu_report(tmp_path, sram_bytes, name="m.npu.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"results": {"u55_256": {"sram_bytes": sram_bytes}}}), encoding="utf-8")
    return p


def _npu_budget(core=HP):
    from e7twin import npu
    return npu.sram_budget_bytes(board_mod.load(), core)


@pytest.mark.toolchain
def test_npu_pass_within_budget(elfs, tmp_path):
    rep = check.run(elfs["ok_hp"], HP, npu_report=_npu_report(tmp_path, _npu_budget()))
    assert _rule(rep, "npu").verdict == "PASS"


@pytest.mark.toolchain
def test_npu_fail_over_budget(elfs, tmp_path):
    p = _npu_report(tmp_path, _npu_budget() + 1)
    assert _rule(check.run(elfs["ok_hp"], HP, npu_report=p), "npu").verdict == "FAIL"
    assert check.main(elfs["ok_hp"], HP, out_dir=tmp_path / "r", npu_report=p) == 1


@pytest.mark.toolchain
def test_npu_skip_without_report(elfs):
    assert _rule(check.run(elfs["ok_hp"], HP), "npu").verdict == "SKIP"


@pytest.mark.toolchain
def test_npu_unreadable_report_exits_2(elfs, tmp_path):
    bad = tmp_path / "bad.npu.json"
    bad.write_text("{pas du json", encoding="utf-8")
    assert check.main(elfs["ok_hp"], HP, out_dir=tmp_path / "r", npu_report=bad) == 2
    assert check.main(elfs["ok_hp"], HP, out_dir=tmp_path / "r", npu_report=tmp_path / "absent.json") == 2
