"""Tests de e7twin.npu (agent 08 ; fichier à relire par l'agent 07).

- Parsing sur sortie Vela enregistrée (tests/fixtures/npu/) : aucun outil requis.
- Tests réels : sautés si `ethos-u-vela` est absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from e7twin import board, npu

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "tests" / "fixtures" / "npu"
MODEL = ROOT / "examples" / "ml" / "ds_cnn_s_quantized.tflite"

needs_vela = pytest.mark.skipif(npu.vela_python() is None, reason="ethos-u-vela absent")


def _sample(key: str, ops: dict | None = None) -> dict:
    return npu.parse_vela_output(
        (FX / f"ds_cnn_{key}.stdout.txt").read_text(encoding="utf-8"),
        (FX / f"ds_cnn_{key}.summary.csv").read_text(encoding="utf-8"),
        ops,
    )


def test_parse_recorded_u55_256():
    d = _sample("u55_256", {"ethos-u": 1})
    assert d["clock_hz"] == 400_000_000
    assert d["cycles_total"] >= d["cycles_npu"] > 0
    assert d["macs"] == 2_664_792
    assert 20 * 1024 < d["sram_bytes"] < 24 * 1024          # 21,69 KiB
    # poids encodés NPU (avec biais/échelles) : 24 176 o pour 22 016 o d'origine dans l'échantillon
    assert d["weights_bytes"] == 24176 and d["weights_original_bytes"] == 22016
    assert (d["npu_operators"], d["cpu_operators"]) == (39, 0)
    assert d["cpu_op_types"] == {}
    assert d["inference_time_s"] == pytest.approx(d["cycles_total"] / d["clock_hz"], rel=0.01)


def test_u55_128_is_slower_than_256():
    a, b = _sample("u55_256"), _sample("u55_128")
    assert b["clock_hz"] == 160_000_000
    assert b["cycles_total"] > a["cycles_total"]
    assert b["inference_time_s"] > a["inference_time_s"]
    assert b["macs"] == a["macs"]


def test_cpu_fallback_ops_are_reported():
    d = _sample("u55_256", {"ethos-u": 2, "SOFTMAX": 1, "TRANSPOSE": 3})
    assert d["cpu_op_types"] == {"SOFTMAX": 1, "TRANSPOSE": 3}


def test_parse_rejects_bad_csv():
    with pytest.raises(npu.NpuError):
        npu.parse_vela_output("", "")
    with pytest.raises(npu.NpuError):
        npu.parse_vela_output("", "a,b\n1,2\n")


def test_vela_ini_matches_board():
    brd = board.load()
    ini = npu.vela_ini(brd)
    assert f"core_clock={brd.core('m55_hp').freq_hz}" in ini
    assert f"core_clock={brd.core('m55_he').freq_hz}" in ini
    assert "[System_Config.E7_U55_256_MRAM]" in ini and "[System_Config.E7_U55_128_MRAM]" in ini
    assert "axi1_port=OnChipFlash" in ini
    assert "axi1_port=OffChipFlash" in npu.vela_ini(brd, "ospi")
    with pytest.raises(npu.NpuError):
        npu.vela_ini(brd, "dram")
    # le fichier enregistré doit rester synchrone avec le générateur
    assert (FX / "vela_e7.ini").read_text(encoding="utf-8") == ini


def test_sram_budget_from_board():
    brd = board.load()
    sram0 = brd.region("sram0").size
    assert npu.sram_budget_bytes(brd, "m55_hp") == sram0
    assert npu.sram_budget_bytes(brd, "m55_he") == sram0
    assert npu.sram_budget_bytes(brd, "a32_0") >= sram0


def test_main_missing_model_returns_2(tmp_path):
    assert npu.main(str(tmp_path / "nope.tflite"), out_dir=str(tmp_path)) == 2


def test_main_unknown_npu_returns_2(tmp_path):
    assert npu.main(str(MODEL), npu="u55_512", out_dir=str(tmp_path)) == 2


def test_main_without_vela_returns_2(monkeypatch, tmp_path):
    monkeypatch.setattr(npu, "vela_python", lambda: None)
    assert npu.main(str(MODEL), out_dir=str(tmp_path)) == 2


def _fake_report(sram: int, budget: int) -> npu.NpuReport:
    res = {k: npu.NpuResult(npu=k, accelerator=a, core=c, ok=True, sram_bytes=sram, sram_budget_bytes=budget,
                            fits=sram <= budget, clock_hz=400_000_000)
           for k, (a, c) in npu.NPUS.items()}
    return npu.NpuReport(model="m.tflite", model_bytes=1, model_sha256="0", results=res)


@pytest.mark.parametrize("sram,budget,expected", [(100, 200, 0), (300, 200, 1)])
def test_main_exit_code_on_arena_overflow(monkeypatch, tmp_path, sram, budget, expected):
    monkeypatch.setattr(npu, "run", lambda model, npu_=None: _fake_report(sram, budget))
    assert npu.main("m.tflite", out_dir=str(tmp_path)) == expected
    data = json.loads((tmp_path / "m.npu.json").read_text(encoding="utf-8"))
    assert data["fits"] is (expected == 0)


@needs_vela
def test_real_vela_both_npus(tmp_path):
    rep = npu.run(MODEL)
    assert rep.ok and rep.fits
    r256, r128 = rep.results["u55_256"], rep.results["u55_128"]
    assert r256.cycles_total < r128.cycles_total
    assert r256.cpu_operators == 0
    d = rep.to_dict()
    assert set(d["results"]) == {"u55_256", "u55_128"} and d["fidelity"]
    assert npu.main(str(MODEL), out_dir=str(tmp_path)) == 0
    assert (tmp_path / "ds_cnn_s_quantized.npu.json").exists()


@needs_vela
def test_real_vela_single_npu():
    rep = npu.run(MODEL, "u55_128")
    assert list(rep.results) == ["u55_128"] and rep.ok
