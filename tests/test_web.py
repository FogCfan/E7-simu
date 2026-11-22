"""Tests du dashboard statique (agent 06 ; à signaler à l'agent 07)."""

import re

import pytest
from fastapi.testclient import TestClient

from e7twin.metrics import FakeSource
from e7twin.server import WEB_DIR, create_app

URL_RE = re.compile(r"https?://", re.I)


@pytest.fixture
def client():
    with TestClient(create_app(FakeSource(seed=7))) as c:
        yield c


def test_index_and_assets_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>E7 Twin" in r.text
    for path in ("/app.js", "/style.css", "/vendor/uPlot.iife.min.js", "/vendor/uPlot.min.css", "/vendor/LICENSE"):
        assert client.get(path).status_code == 200, path
    assert "MIT" in client.get("/vendor/LICENSE").text


def test_npu_view_present_and_wired(client):
    html = client.get("/").text
    assert 'id="view-npu"' in html and 'id="npu-select"' in html
    assert "non mesurée sur carte" in html
    js = client.get("/app.js").text
    assert "/api/npu" in js and '"/api/npu/"' in js
    assert "e7twin npu" in js  # état vide explicite


def test_every_local_reference_resolves(client):
    html = client.get("/").text
    refs = re.findall(r'(?:src|href)="([^"#][^"]*)"', html)
    assert refs
    for ref in refs:
        assert client.get("/" + ref).status_code == 200, ref


def test_no_external_url_in_own_files():
    for name in ("index.html", "app.js", "style.css"):
        text = (WEB_DIR / name).read_text(encoding="utf-8")
        assert not URL_RE.search(text), f"URL externe dans web/{name}"


def test_no_hardcoded_board_constants():
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    for forbidden in ("m55_hp", "m55_he", "a32_0", "sram0", "itcm", "0x8000"):
        assert forbidden not in js, forbidden


def test_ws_feeds_the_dashboard(client):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        client.post("/api/sim/start")
        kinds = set()
        for _ in range(60):
            kinds.add(ws.receive_json()["type"])
            if {"sample", "uart"} <= kinds:
                break
        assert {"sample", "uart"} <= kinds


def test_main_tabs_present(client):
    html = client.get("/").text
    for t in ("examples", "import", "verify", "sim"):
        assert f'data-tab="{t}"' in html and f'id="view-{t}"' in html, t
    for i in ("drop", "src-text", "btn-compile", "btn-start", "btn-pause", "btn-reset", "btn-lang"):
        assert f'id="{i}"' in html, i


def test_app_calls_routes(client):
    js = client.get("/app.js").text
    for route in ("/api/examples", "/api/upload", "/api/build/source", "/api/check/run", "/api/sim/load", '"/api/sim/"'):
        assert route in js, route
    assert "alert(" not in js


def _i18n_keys(js: str) -> dict[str, set[str]]:
    out = {}
    for lang in ("fr", "en"):
        m = re.search(rf"\n    {lang}: \{{(.*?)\n    \}},", js, re.S)
        assert m, lang
        out[lang] = set(re.findall(r"\b([a-z0-9_]+):\s*\"", m.group(1)))
    return out


def test_i18n_same_keys_in_both_languages(client):
    keys = _i18n_keys(client.get("/app.js").text)
    assert keys["fr"] and keys["fr"] == keys["en"], keys["fr"] ^ keys["en"]
    html = client.get("/").text
    used = set(re.findall(r'data-i18n(?:-aria)?="([a-z0-9_]+)"', html))
    assert used <= keys["fr"], used - keys["fr"]


def test_launcher_waits_for_port():
    from pathlib import Path
    cmd = (WEB_DIR.parent / "E7Twin.cmd").read_text(encoding="utf-8", errors="replace")
    assert "TcpClient" in cmd and "30" in cmd
    assert cmd.index("TcpClient") < cmd.index("e7twin.cli serve") or "start \"\" /b" in cmd


def test_display_view_present_and_wired(client):
    html = client.get("/").text
    assert 'id="view-display"' in html and 'id="display-img"' in html
    js = client.get("/app.js").text
    assert '"/api/display"' in js and "/api/display.png?t=" in js
    assert "CDC200" in js and "sans timing DSI" in html


def test_accelerated_checkbox_wired(client):
    html = client.get("/").text
    assert 'id="chk-accel"' in html and "Saute les périodes de veille" in html
    js = client.get("/app.js").text
    assert "/api/sim/speed" in js and "accelerated" in js and "Accelerated" in js


def test_quit_button_wired(client):
    html = client.get("/").text
    assert 'id="btn-quit"' in html
    js = client.get("/app.js").text
    assert "/api/shutdown" in js and "quitting = true" in js and "Stop E7 Twin" in js
