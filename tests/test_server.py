"""Tests du serveur et des métriques factices (agent 05 ; à signaler à l'agent 07)."""

import pytest
from fastapi.testclient import TestClient

from e7twin import board as board_mod
from e7twin.metrics import FakeSource, Sample, make_core_metrics
from e7twin.server import SPIKE_DIR, create_app, main


@pytest.fixture
def client():
    with TestClient(create_app(FakeSource(seed=7))) as c:
        yield c


def _run(src, n):
    samples, uart, events = [], [], []
    src.start()
    for _ in range(n):
        samples.append(src.sample().to_dict())
        uart += src.uart_lines()
        events += src.events()
    return samples, uart, events


def test_fake_deterministic():
    assert _run(FakeSource(seed=3), 60) == _run(FakeSource(seed=3), 60)
    assert _run(FakeSource(seed=3), 60) != _run(FakeSource(seed=4), 60)


def test_sample_schema_and_board_driven():
    b = board_mod.load()
    samples, uart, events = _run(FakeSource(seed=1), 120)
    execs = {c.id for c in b.cores if c.executable}
    for s in samples:
        assert set(s) == {"t_virtual_s", "cores", "memory", "events"}
        assert set(s["cores"]) == execs
        for m in s["cores"].values():
            assert set(m) == {"load_pct", "mips", "wfi_pct"}
            assert 0 <= m["load_pct"] <= 100
            assert m["load_pct"] + m["wfi_pct"] == pytest.approx(100, abs=0.02)
        assert set(s["memory"]) == {r.name for r in b.memories}
        for v in s["memory"].values():
            assert set(v) == {"static_bytes", "watermark_bytes", "size"}
    assert any(s["cores"]["m55_hp"]["load_pct"] > 1 for s in samples)
    # watermark monotone
    wm = [s["memory"]["dtcm_hp"]["watermark_bytes"] for s in samples if "dtcm_hp" in s["memory"]]
    assert wm and wm == sorted(wm) and wm[-1] > wm[0]
    assert any(u["text"] == "Hello World!" for u in uart)
    assert any(e["kind"] == "mhu" for e in events)
    assert any(e["kind"] == "unmapped" for e in events)


def test_load_formula_cpi1():
    m = make_core_metrics(40e6, 400_000_000, 0.1)  # 40M instr en 0,1 s à 400 MHz = 100 %
    assert m["load_pct"] == 100.0 and m["mips"] == 400.0
    assert make_core_metrics(0, 160_000_000, 0.1)["wfi_pct"] == 100.0


def test_a32_not_executed(client):
    st = client.get("/api/sim/status").json()
    assert st["fake"] is True
    assert st["cores"]["a32_0"] == "non exécuté"


def test_board_route(client):
    r = client.get("/api/board")
    assert r.status_code == 200
    assert {c["id"] for c in r.json()["cores"]} >= {"m55_hp", "m55_he"}


def test_check_route(client):
    elfs = sorted(SPIKE_DIR.glob("*_h[pe].elf"))
    if not elfs:
        pytest.skip("pas d'ELF dans build/spike")
    r = client.get(f"/api/check/{elfs[0].stem}")
    assert r.status_code == 200, r.text
    assert "verdict" in r.json()
    assert client.get("/api/check/inconnu_hp").status_code == 404
    assert client.get("/api/check/..%2Fx").status_code in (400, 404)


def test_sim_routes(client):
    assert client.get("/api/sim/status").json()["state"] == "stopped"
    assert client.post("/api/sim/start").json()["state"] == "running"
    assert client.post("/api/sim/pause").json()["state"] == "paused"
    assert client.post("/api/sim/reset").json()["state"] == "stopped"
    assert client.post("/api/sim/bogus").status_code == 404
    r = client.post("/api/sim/load", json={"core": "m55_hp", "path": "x.elf"})
    assert r.json() == {"ok": True}
    assert client.get("/api/sim/status").json()["images"]["m55_hp"] == "x.elf"
    assert client.post("/api/sim/load", json={"core": "zz", "path": "x"}).status_code == 404
    assert client.post("/api/sim/load", json={"core": "a32_0", "path": "x"}).status_code == 422


def test_profile_route(client):
    r = client.get("/api/profile/m55_hp")
    assert r.status_code == 200 and r.json()["fake"] is True and r.json()["functions"]
    assert client.get("/api/profile/zz").status_code == 404


def test_ws_stream(client):
    client.post("/api/sim/start")
    seen = {}
    with client.websocket_connect("/ws") as ws:
        for _ in range(200):
            msg = ws.receive_json()
            seen.setdefault(msg["type"], msg)
            if {"status", "sample", "uart", "event"} <= set(seen):
                break
    assert {"status", "sample", "uart", "event"} <= set(seen)
    assert set(seen["sample"]["data"]) == set(Sample(0).to_dict())
    assert set(seen["uart"]) >= {"core", "uart", "t", "text"}
    assert set(seen["event"]["data"]) >= {"t", "kind", "core", "detail"}
    assert seen["status"]["state"] in ("stopped", "running", "paused", "error")


def test_main_nonfake_bad_image_returns_2(tmp_path):
    assert main(fake=False, hp=str(tmp_path / "absent.elf")) == 2


def test_check_list(client):
    r = client.get("/api/check")
    assert r.status_code == 200
    imgs = r.json()["images"]
    for i in imgs:
        assert set(i) >= {"id", "name", "dir", "path", "core"}
    if "spike/hello_hp" in {i["id"] for i in imgs}:
        assert next(i for i in imgs if i["id"] == "spike/hello_hp")["core"] == "m55_hp"
        assert client.get("/api/check/spike/hello_hp").status_code == 200
    assert client.get("/api/check/nope/x").status_code == 404
    assert client.get("/api/check/a/b/c").status_code == 400


def test_uart_history(client):
    import time
    client.post("/api/sim/start")
    time.sleep(1.5)
    h = client.get("/api/uart?n=3").json()
    assert h["n"] == 3 and h["uarts"]
    for lines in h["uarts"].values():
        assert 0 < len(lines) <= 3 and set(lines[0]) >= {"core", "uart", "t", "text"}
    with client.websocket_connect("/ws") as ws:  # l'historique est rejoué à la connexion
        first = [ws.receive_json() for _ in range(3)]
    assert first[0]["type"] == "status" and any(m["type"] == "uart" for m in first[1:])


def test_start_without_image_is_409():
    class NoImg(FakeSource):
        def start(self):
            raise RuntimeError("aucune image chargée")

    with TestClient(create_app(NoImg())) as c:
        assert c.post("/api/sim/start").status_code == 409


def test_npu_routes(tmp_path):
    import json
    import os
    (tmp_path / "old.npu.json").write_text(json.dumps({"model": "old.tflite"}))
    (tmp_path / "new.npu.json").write_text(json.dumps({"model": "new.tflite", "fits": True}))
    (tmp_path / "other.txt").write_text("x")
    os.utime(tmp_path / "old.npu.json", (1000, 1000))
    os.utime(tmp_path / "new.npu.json", (2000, 2000))
    with TestClient(create_app(FakeSource(seed=7), reports_dir=tmp_path)) as c:
        lst = c.get("/api/npu").json()
        assert [e["model"] for e in lst] == ["new", "old"]
        assert lst[0] == {"model": "new", "file": "new.npu.json", "mtime": 2000.0}
        assert c.get("/api/npu/new").json() == {"model": "new.tflite", "fits": True}
        assert c.get("/api/npu/absent").status_code == 404
        assert c.get("/api/npu/..%2Fsecret").status_code == 404
        assert c.get("/api/npu/..%5Csecret").status_code == 404
        assert c.get("/api/npu/a..b").status_code == 404


def test_npu_list_no_dir(tmp_path):
    with TestClient(create_app(FakeSource(seed=7), reports_dir=tmp_path / "none")) as c:
        assert c.get("/api/npu").json() == []
