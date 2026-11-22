"""Reproduction sans navigateur des appels de la page (onglet Simulation) : create_app réel + SimSource réel.

Scénario A : load + start + speed(accéléré) + /api/display.png à 5 Hz + WebSocket, ~5 s, pour chaque ELF.
Scénario B : charge / démarre / charge une autre image / redémarre, 3 fois.
Usage : py -3.14 e7twin/renode/spike/repro_ui.py [--passes 3]. Code retour 0 si tout passe, sans renode.exe orphelin.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from e7twin.server import create_app  # noqa: E402
from e7twin.simsource import SimSource  # noqa: E402

EX = ROOT / "build" / "examples"
HP = {"display": EX / "display_hp.elf", "loop": EX / "load_loop_hp.elf", "hello": EX / "hello_hp.elf"}


def renode_pids() -> set[str]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq renode.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {ln.split(",")[1].strip('"') for ln in out.splitlines() if "renode" in ln.lower()}


def check(r, what):
    assert r.status_code == 200, f"{what}: {r.status_code} {r.text}"
    return r.json()


def run_sim(c, name: str, accelerated: bool, secs: float = 8.0, pre_speed: bool = False) -> None:
    check(c.post("/api/sim/load", json={"core": "m55_hp", "path": str(HP[name])}), "load")
    if pre_speed:
        check(c.post("/api/sim/speed", json={"accelerated": True}), "speed")
    st = check(c.post("/api/sim/start"), "start")
    stop = threading.Event()
    errs: list[str] = []

    def png():
        c2 = TestClient(c.app)
        while not stop.is_set():
            r = c2.get("/api/display.png")
            if r.status_code not in (200, 404):
                errs.append(f"png {r.status_code}")
            if int(time.time() * 5) % 10 == 0:  # profil toutes les ~2 s (la page le fait toutes les 5 s)
                r = c2.get("/api/profile/m55_hp")
                if r.status_code != 200:
                    errs.append(f"profile {r.status_code}")
            time.sleep(0.2)

    t = threading.Thread(target=png, daemon=True)
    t.start()
    try:
        if accelerated:
            check(c.post("/api/sim/speed", json={"accelerated": True}), "speed")
        nmsg = [0]

        def wsloop():
            with TestClient(c.app).websocket_connect("/ws") as ws:
                while not stop.is_set():
                    ws.receive_json()
                    nmsg[0] += 1

        wt = threading.Thread(target=wsloop, daemon=True)
        wt.start()
        end = time.time() + secs
        while time.time() < end:
            time.sleep(0.5)
            st = c.get("/api/sim/status").json()
            assert st["state"] == "running", f"{name} accel={accelerated}: {st}"
        assert nmsg[0] >= 1, f"websocket muet ({nmsg[0]})"
    finally:
        stop.set()
        t.join()
    assert not errs, errs
    print(f"  ok {name} accéléré={accelerated} t_virtual={st['t_virtual_s']}", flush=True)


def main() -> int:
    passes = int(sys.argv[sys.argv.index("--passes") + 1]) if "--passes" in sys.argv else 1
    before = renode_pids()
    for p in range(passes):
        print(f"passe {p + 1}", flush=True)
        src = SimSource()
        try:
            c = TestClient(create_app(src))
            for name in ("loop", "display"):
                for acc in (False, True):
                    run_sim(c, name, acc, 8.0)
                    check(c.post("/api/sim/reset"), "reset")
            run_sim(c, "display", True, 8.0, pre_speed=True)
            check(c.post("/api/sim/speed", json={"accelerated": False}), "speed")
            check(c.post("/api/sim/reset"), "reset")
            # B : chargements successifs sur la même session
            for i, name in enumerate(("display", "loop", "display")):
                check(c.post("/api/sim/load", json={"core": "m55_hp", "path": str(HP[name])}), "load")
                check(c.post("/api/sim/start"), "start")
                time.sleep(9)
                st = c.get("/api/sim/status").json()
                assert st["state"] == "running" and st["t_virtual_s"] > 0.2, f"chargement {i} {name}: {st}"
                print(f"  ok rechargement {i} {name} t={st['t_virtual_s']}", flush=True)
        finally:
            src.close()
    time.sleep(1)
    orphans = renode_pids() - before
    assert not orphans, f"renode.exe orphelins : {orphans}"
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
