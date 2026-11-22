"""Tests du serveur et des métriques factices (agent 05 ; à signaler à l'agent 07)."""

from pathlib import Path

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


# ---- GUI : exemples, import, vérification --------------------------------------------------------------
HELLO = Path(__file__).resolve().parent.parent / "build" / "examples" / "hello_hp.elf"
needs_elf = pytest.mark.skipif(not HELLO.is_file(), reason="build/examples/hello_hp.elf absent")


@pytest.fixture
def gui(tmp_path, monkeypatch):
    from e7twin import server
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "up")
    monkeypatch.setitem(server.IMAGE_DIRS, "uploads", tmp_path / "up")
    with TestClient(create_app(FakeSource(seed=7), reports_dir=tmp_path / "rep")) as c:
        yield c


def test_examples_route(gui):
    ex = gui.get("/api/examples").json()
    assert ex and all(set(e) == {"name", "cores", "description", "elf", "elfs", "built"} for e in ex)
    assert all(c in ("m55_hp", "m55_he") for e in ex for c in e["cores"])
    names = [e["name"] for e in ex]
    assert len(names) == len(set(names))
    if HELLO.is_file():
        hello = next(e for e in ex if e["name"] in ("hello", "hello_hp"))
        assert hello["built"] and "m55_hp" in hello["elfs"]


def test_upload_rejects(gui):
    r = gui.post("/api/upload?name=x.elf", content=b"not an elf at all, really", headers={"content-type": "application/octet-stream"})
    assert r.status_code == 400 and "ELF" in r.json()["detail"]
    arm64 = b"\x7fELF\x02\x01\x01" + b"\0" * 11 + (183).to_bytes(2, "little") + b"\0" * 40
    assert gui.post("/api/upload?name=x.elf", content=arm64).status_code == 400
    assert gui.post("/api/upload?name=", content=b"x").status_code == 400


def test_upload_too_big(gui, monkeypatch):
    from e7twin import server
    monkeypatch.setattr(server, "MAX_UPLOAD", 100)
    r = gui.post("/api/upload?name=x.elf", content=b"\x7fELF" + b"\0" * 200)
    assert r.status_code == 400 and "gros" in r.json()["detail"]


@needs_elf
def test_upload_check_run_and_report(gui, tmp_path):
    from e7twin import server
    r = gui.post("/api/upload?name=../evil name.elf", content=HELLO.read_bytes())
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["id"] == "uploads/evil_name" and up["core_guess"] == "m55_hp"
    assert (tmp_path / "up" / "evil_name.elf").is_file()
    # check/run n'accepte que build/ : on copie l'import sous build/ pour ce test (chemin réel du dépôt)
    res = gui.post("/api/check/run", json={"elf": "build/examples/hello_hp.elf", "core": "m55_hp"})
    assert res.status_code == 200, res.text
    j = res.json()
    assert j["verdict"] in ("PASS", "WARN") and j["rules"] and j["report_url"].endswith(".check.html")
    h = gui.get(j["report_url"])
    assert h.status_code == 200 and "<html" in h.text.lower()
    assert gui.get("/api/report/..%2Fx.check.html").status_code == 404
    assert gui.get("/api/report/absent.check.html").status_code == 404


def test_check_run_refuses_outside_build(gui):
    for bad in ("pyproject.toml", "../outside.elf", "C:/Windows/notepad.exe", "examples/README.md"):
        r = gui.post("/api/check/run", json={"elf": bad, "core": "m55_hp"})
        assert r.status_code == 400, (bad, r.status_code)
    assert gui.post("/api/check/run", json={"elf": "build/nope.elf", "core": "m55_hp"}).status_code == 404


@needs_elf
def test_check_run_bad_core_and_npu(gui):
    assert gui.post("/api/check/run", json={"elf": "build/examples/hello_hp.elf", "core": "zz"}).status_code == 404
    r = gui.post("/api/check/run", json={"elf": "build/examples/hello_hp.elf", "core": "m55_hp",
                                         "npu_report": "../secret.json"})
    assert r.status_code == 400


def test_simsource_inactive_starts_without_renode():
    from e7twin.simsource import SimSource
    s = SimSource()
    try:
        st = s.status()
        assert st["state"] == "stopped" and st["images"] == {} and s._session is None
        with TestClient(create_app(s)) as c:
            assert c.post("/api/sim/load", json={"core": "m55_hp", "path": "build/nope.elf"}).status_code == 422
            assert c.post("/api/sim/start").status_code == 409
    finally:
        s.close()


def test_build_source_route(gui, tmp_path, monkeypatch):
    from e7twin import build
    seen = {}

    def fake_build(main_c, core, name=None, out_dir="build/uploads"):
        seen.update(src=Path(main_c).read_text(encoding="utf-8"), core=core, name=name, out=Path(out_dir))
        return {"ok": True, "elf": str(HELLO), "su_dir": None, "log": "ok", "errors": []}

    monkeypatch.setattr(build, "build_source", fake_build)
    r = gui.post("/api/build/source?core=m55_hp&name=Mon%20Test", content="int main(void){return 0;}\n".encode())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["log"] == "ok" and j["errors"] == []
    assert seen["name"] == "Mon_Test" and seen["core"] == "m55_hp" and "int main" in seen["src"]
    assert seen["out"] == tmp_path / "up" and (tmp_path / "up" / "Mon_Test.c").is_file()
    if HELLO.is_file():
        assert j["core_guess"] == "m55_hp"


def test_build_source_anonymous_names_are_unique(gui, tmp_path, monkeypatch):
    from e7twin import build
    monkeypatch.setattr(build, "build_source", lambda *a, **k: {"ok": False, "elf": None, "su_dir": None, "log": "", "errors": []})
    n = [gui.post("/api/build/source?core=m55_hp", content=b"int x;").json()["name"] for _ in range(2)]
    assert n[0] != n[1] and all(x.startswith("upload_") for x in n)
    assert all((tmp_path / "up" / f"{x}.c").is_file() for x in n)


@needs_elf
def test_upload_anonymous_names_are_unique(gui):
    ids = [gui.post("/api/upload", content=HELLO.read_bytes()).json() for _ in range(2)]
    assert ids[0]["name"] != ids[1]["name"] and ids[0]["id"] == f"uploads/{ids[0]['name']}"


def test_build_source_same_name_concurrent_409(gui, monkeypatch):
    from e7twin import build, server
    monkeypatch.setattr(build, "build_source", lambda *a, **k: {"ok": False, "elf": None, "su_dir": None, "log": "", "errors": []})
    server._building.add("busy")
    try:
        assert gui.post("/api/build/source?core=m55_hp&name=busy", content=b"int x;").status_code == 409
        assert gui.post("/api/build/source?core=m55_hp&name=other", content=b"int x;").status_code == 200
    finally:
        server._building.discard("busy")
    assert gui.post("/api/build/source?core=m55_hp&name=busy", content=b"int x;").status_code == 200
    assert "busy" not in server._building  # libéré après la compilation


def test_build_source_failure_releases_name(gui, monkeypatch):
    from e7twin import build, server

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(build, "build_source", boom)
    with pytest.raises(RuntimeError):
        gui.post("/api/build/source?core=m55_hp&name=boom", content=b"int x;")
    assert "boom" not in server._building


def test_build_source_route_failure_passthrough(gui, monkeypatch):
    from e7twin import build
    err = {"file": "main.c", "line": 3, "col": 1, "message": "oups"}
    monkeypatch.setattr(build, "build_source", lambda *a, **k: {"ok": False, "elf": None, "su_dir": None, "log": "x", "errors": [err]})
    j = gui.post("/api/build/source?core=m55_he&name=x", content=b"bad").json()
    assert j["ok"] is False and j["errors"] == [err] and "core_guess" not in j


def test_build_source_route_400(gui, monkeypatch):
    from e7twin import build, server
    monkeypatch.setattr(build, "build_source", lambda *a, **k: pytest.fail("ne doit pas être appelé"))
    assert gui.post("/api/build/source?core=a32_0&name=x", content=b"int x;").status_code == 400
    assert gui.post("/api/build/source?core=&name=x", content=b"int x;").status_code == 400
    assert gui.post("/api/build/source?core=m55_hp&name=x", content=b"  \n").status_code == 400
    assert gui.post("/api/build/source?core=m55_hp&name=x", content=b"\xff\xfe").status_code == 400
    monkeypatch.setattr(server, "MAX_SOURCE", 10)
    assert gui.post("/api/build/source?core=m55_hp&name=x", content=b"int x;" * 5).status_code == 400


def _png_ihdr(b: bytes):
    import struct
    assert b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR"
    return struct.unpack(">II", b[16:24])


def test_display_fake_png(client):
    j = client.get("/api/display").json()
    assert j["enabled"] is True and (j["width"], j["height"], j["format"]) == (480, 800, "RGB565")
    r = client.get("/api/display.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    assert _png_ihdr(r.content) == (480, 800)


def test_display_inactive_404():
    class NoFb(FakeSource):
        def framebuffer(self):
            return None
    with TestClient(create_app(NoFb(seed=1))) as c:
        assert c.get("/api/display").json()["enabled"] is False
        assert c.get("/api/display.png").status_code == 404


def test_simsource_framebuffer_none_and_cache():
    from e7twin.simsource import SimSource
    s = SimSource()
    try:
        assert s.framebuffer() is None  # pas de session
        calls = []

        class Sess:
            def read_framebuffer(self):
                calls.append(1)
                return {"width": 1}
        s._session = Sess()
        assert s.framebuffer() == {"width": 1} and s.framebuffer() == {"width": 1}
        assert len(calls) == 1  # cache 200 ms
    finally:
        s._session = None
        s.close()


def test_to_png_pitch_and_colors():
    import struct
    import zlib
    from e7twin.framebuffer import test_pattern, to_png
    # 2x1 px, pitch 8 (octets de bourrage) : rouge pur, vert pur
    png = to_png({"width": 2, "height": 1, "format": "RGB565", "pitch": 8,
                  "data": struct.pack("<HH", 0xF800, 0x07E0) + b"\xaa" * 4})
    assert _png_ihdr(png) == (2, 1)
    i = png.index(b"IDAT")
    n = struct.unpack(">I", png[i - 4:i])[0]
    raw = zlib.decompress(png[i + 4:i + 4 + n])
    assert raw == b"\x00" + bytes([255, 0, 0, 0, 255, 0])
    # bleu pur et blanc, 2 lignes avec pitch > largeur
    fb = {"width": 2, "height": 2, "format": "RGB565", "pitch": 6,
          "data": struct.pack("<HH", 0x001F, 0xFFFF) + b"\0\0" + struct.pack("<HH", 0, 0) + b"\0\0"}
    png = to_png(fb)
    i = png.index(b"IDAT")
    raw = zlib.decompress(png[i + 4:i + 4 + struct.unpack(">I", png[i - 4:i])[0]])
    assert raw[1:7] == bytes([0, 0, 255, 255, 255, 255]) and raw[7:] == b"\x00" + b"\0" * 6
    assert len(test_pattern(8, 4, 0.0)["data"]) == 8 * 4 * 2
    with pytest.raises(ValueError):
        to_png({"width": 4, "height": 1, "format": "RGB565", "pitch": 2, "data": b""})


def test_speed_fake_409_then_accelerated(client, tmp_path):
    assert client.get("/api/sim/status").json()["accelerated"] is False
    assert client.post("/api/sim/speed", json={"accelerated": True}).status_code == 409  # rien de chargé
    assert client.post("/api/sim/speed", json={}).status_code == 422
    elf = tmp_path / "x.elf"
    elf.write_bytes(b"x")
    assert client.post("/api/sim/load", json={"core": "m55_hp", "path": str(elf)}).status_code == 200
    src = client.app.state.source
    src.start()
    t0 = src.sample().t_virtual_s
    d_real = src.sample().t_virtual_s - t0
    j = client.post("/api/sim/speed", json={"accelerated": True}).json()
    assert j["accelerated"] is True
    t1 = src.sample().t_virtual_s
    d_acc = src.sample().t_virtual_s - t1
    assert d_acc > 30 * d_real > 0
    # la charge reste bornée (calculée sur le temps virtuel)
    assert all(0 <= c["load_pct"] <= 100 for c in src.sample().cores.values())
    assert client.post("/api/sim/speed", json={"accelerated": False}).json()["accelerated"] is False


def test_simsource_speed_and_bounded_queues():
    from e7twin.simsource import QUEUE_MAX, SimSource
    s = SimSource()
    try:
        with pytest.raises(RuntimeError):
            s.set_speed(True)
        calls = []

        class Sess:
            def set_advance_immediately(self, on):
                calls.append(on)
        s._session = Sess()
        s.set_speed(True)
        assert calls == [True] and s.status()["accelerated"] is True
        s._publish(s._latest, uart=[{"core": "m55_hp", "uart": "uart4", "t": 0, "text": str(i)} for i in range(QUEUE_MAX + 50)])
        out = s.uart_lines()
        assert len(out) == QUEUE_MAX and out[-1]["text"] == str(QUEUE_MAX + 49)  # les plus récentes
        s._session = object()
        with pytest.raises(RuntimeError):
            s.set_speed(False)  # méthode absente -> 409
    finally:
        s._session = None
        s.close()


def test_shutdown_route_ok_and_non_loopback_403():
    with TestClient(create_app(FakeSource(seed=3))) as c:
        assert c.post("/api/shutdown").json() == {"ok": True}  # sans uvicorn.Server : ne plante pas
    with TestClient(create_app(FakeSource(seed=3)), client=("10.0.0.5", 5000)) as c:
        assert c.post("/api/shutdown").status_code == 403


def test_shutdown_route_sets_should_exit():
    app = create_app(FakeSource(seed=3))

    class Srv:
        should_exit = False
    app.state.server = Srv()
    with TestClient(app) as c:
        assert c.post("/api/shutdown").json() == {"ok": True}
        import time
        time.sleep(0.8)
    assert app.state.server.should_exit is True


def test_ws_disconnect_is_clean(client):
    from e7twin.server import create_app as _  # noqa
    hub_clients = client.app.state.source  # source vivante ; le hub est fermé sur la route
    with client.websocket_connect("/ws") as w:
        assert w.receive_json()["type"] == "status"
    with client.websocket_connect("/ws") as w:  # reconnexion possible : le premier client a été retiré proprement
        assert w.receive_json()["type"] == "status"
    assert hub_clients is not None


def test_static_no_cache(client):
    for p in ("/", "/app.js"):
        r = client.get(p)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
    assert "no-cache" not in client.get("/api/board").headers.get("cache-control", "")
