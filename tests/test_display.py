"""Tests de l'écran simulé (CDC200 + DSI, agent 04) : `Session.read_framebuffer()`.

Le test Renode exécute le vrai firmware `build/examples/display_hp.elf` (démo CDC200 du SDK, barre blanche
qui descend toutes les 500 ms) ; il est sauté proprement si Renode ou l'ELF sont absents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from e7twin import board as B
from e7twin import sim, toolchain
from e7twin.renode import stubs

REPO = Path(__file__).resolve().parents[1]
DISPLAY_HP = REPO / "build" / "examples" / "display_hp.elf"
WHITE = b"\xff\xff"  # RGB565 blanc


def test_display_overlay_from_board(tmp_path):
    b = B.load()
    text = stubs.generate(b, tmp_path)
    d = b.display
    assert f"cdc200: Python.PythonPeripheral @ sysbus 0x{d['controller']['base']:08x}" in text
    assert f"dsi: Python.PythonPeripheral @ sysbus 0x{d['dsi']['base']:08x}" in text
    def params(name):  # les paramètres sont les lignes `NOM = repr` en tête du script généré
        ns: dict = {}
        for ln in (tmp_path / f"{name}.py").read_text(encoding="utf-8").splitlines():
            if ln[:1].isupper() and " = " in ln:
                exec(ln, ns)
        return ns

    assert params("dsi")["READY"] == {0xB0: 0x95}  # DSI_PHY_STATUS : PHY_LOCK | stop states
    fixed = params("cdc200")["FIXED"]
    assert fixed[4] == 2 and fixed[0] != 0  # CDC_LCNT = 2 calques ; CDC_HW_VER non nul


@pytest.mark.renode
@pytest.mark.skipif(toolchain.find("renode") is None, reason="Renode absent")
@pytest.mark.skipif(not DISPLAY_HP.is_file(), reason="build/examples/display_hp.elf absent (agent 07)")
def test_read_framebuffer_display_hp():
    with sim.Session() as s:
        assert s.read_framebuffer() is None  # écran inactif avant le boot
        s.load_elf("m55_hp", DISPLAY_HP)
        s.start()
        assert s.run_until("DISPLAY frame 2", 90), s.uart_text()[-400:]
        # Le temps virtuel court bien plus vite que le temps réel pendant le WFI : une capture peut tomber pendant
        # l'effacement/redessin de la barre (≈ la moitié du temps réel). On reprend donc jusqu'à voir la barre.
        for _ in range(12):
            fb = s.read_framebuffer()
            assert fb is not None
            assert (fb["width"], fb["height"], fb["format"]) == (480, 800, "RGB565")
            assert fb["pitch"] == 480 * 2 and len(fb["data"]) == 480 * 800 * 2
            assert 0x02000000 <= fb["address"] < 0x02000000 + 0x400000 * 2
            rows = [fb["data"][y * fb["pitch"]:(y + 1) * fb["pitch"]] for y in range(fb["height"])]
            white = [r == WHITE * 480 for r in rows]
            if any(white):
                break
        else:
            pytest.fail("aucune ligne entièrement blanche en 12 captures (barre absente)")
        assert not all(white) and any(r != WHITE * 480 for r in rows)
        assert 0 < sum(white) <= 40  # la barre fait 40 lignes
