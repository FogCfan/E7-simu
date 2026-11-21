"""Stress de `sim.Session` : N sessions Renode en parallèle (processus distincts), K tours chacune.

    py -3.14 e7twin/renode/spike/stress_sessions.py [-n 3] [-k 2] [--elf build/spike/hello_hp.elf]
                                                    [--expect "Hello World!"] [--shared-history]

Code de sortie : 0 si TOUTES les sessions voient le texte attendu, 1 sinon. Sert de preuve de stabilité
(instances Renode concurrentes : ports, historique du monitor, nettoyage des processus).
`--shared-history` rétablit le comportement fautif d'origine (historique Renode partagé) pour reproduire le bug.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def worker(idx: int, rounds: int, elf: str, expect: str) -> int:
    from e7twin import sim

    bad = 0
    for k in range(rounds):
        t = time.time()
        try:
            with sim.Session() as s:
                s.load_elf("m55_hp", elf)
                s.start()
                ok = s.run_until(expect, 90)
        except sim.SimError as e:
            ok, err = False, str(e)[:120]
        else:
            err = "" if ok else "texte attendu absent"
        print(f"[worker {idx} tour {k}] {'OK' if ok else 'ECHEC ' + err} ({time.time() - t:.1f} s)", flush=True)
        bad += 0 if ok else 1
    return bad


def renode_count() -> int:
    cp = subprocess.run(["tasklist", "/FI", "IMAGENAME eq renode.exe", "/NH"], capture_output=True, text=True)
    return cp.stdout.lower().count("renode.exe")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, default=3, help="sessions parallèles")
    ap.add_argument("-k", type=int, default=2, help="tours par session")
    ap.add_argument("--elf", default="build/spike/hello_hp.elf")
    ap.add_argument("--expect", default="Hello World!")
    ap.add_argument("--shared-history", action="store_true")
    ap.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.worker is not None:
        return 1 if worker(a.worker, a.k, a.elf, a.expect) else 0
    env = dict(os.environ)
    if a.shared_history:
        env["E7TWIN_SHARED_HISTORY"] = "1"
    before = renode_count()
    t0 = time.time()
    procs = [subprocess.Popen([sys.executable, __file__, "--worker", str(i), "-k", str(a.k), "--elf", a.elf,
                               "--expect", a.expect], cwd=REPO, env=env) for i in range(a.n)]
    rcs = [p.wait() for p in procs]
    time.sleep(1.0)
    left = renode_count() - before
    print(f"\n{a.n} sessions x {a.k} tours en {time.time() - t0:.0f} s : "
          f"{sum(1 for r in rcs if r == 0)}/{a.n} workers sans échec ; processus Renode restants : {left}")
    return 0 if all(r == 0 for r in rcs) and left <= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
