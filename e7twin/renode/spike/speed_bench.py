"""Spike écran, mesure de vitesse : rapport temps simulé / temps réel d'une `Session` (agent 04).

    py -3.14 e7twin/renode/spike/speed_bench.py [--window 8] [--only a,b,c,...] [--elf <elf>]

Chaque configuration lance sa propre `Session`, attend la première ligne UART (`DISPLAY frame 1`, hors palier de
boot d'environ 4,5 s), puis laisse tourner `--window` secondes RÉELLES en faisant le travail de la configuration.
Rapport = temps virtuel écoulé / temps réel (1,0 = temps réel ; < 1 = plus lent que le réel).
Chaque résultat est ajouté tout de suite à `build/spike/speed_results.jsonl` (une coupure ne perd rien).

Configurations : a = Session seule ; b = + read_framebuffer(coherent=True) à 5 Hz ; c = idem coherent=False ;
d = SimSource (période 0,25 s, comme `serve`), sans profiler ; dp = idem avec profiler ; df = d + framebuffer()
à 5 Hz ; ref = load_loop_hp en (a) ; py = coût du 1er appel `python` du monitor ; q = (a) avec options Renode
(variable --opt, ex. "emulation SetGlobalQuantum \"0.0001\"") ; pc = (a) + pause/start toutes les 200 ms (coût de
la pause seule, sans copie).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from e7twin import sim  # noqa: E402

KW: dict = {}  # options passées à sim.Session (--adv, --no-skip)
OUT = REPO / "build" / "spike" / "speed_results.jsonl"
DISPLAY = REPO / "build" / "examples" / "display_hp.elf"
LOOP = REPO / "build" / "examples" / "load_loop_hp.elf"


def record(cfg: str, **kw) -> None:
    row = {"cfg": cfg + ("+" + ",".join(KW) if KW else ""), "t": time.strftime("%H:%M:%S"), **kw}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)


def session_run(cfg: str, elf: Path, pattern: str, window: float, work=None, opt: str | None = None) -> None:
    with sim.Session(**KW) as s:
        if opt:
            s._open()
            out = s._mon.cmd(opt)
            print("opt ->", out.strip()[:100])
        s.load_elf("m55_hp", elf)
        t_boot = time.perf_counter()
        s.start()
        if not s.run_until(pattern, 90):
            record(cfg, error="pattern absent", uart=s.uart_text()[-200:])
            return
        boot_s = time.perf_counter() - t_boot
        v0, w0 = s.virtual_time(), time.perf_counter()
        durations: list[float] = []
        while time.perf_counter() - w0 < window:
            t = time.perf_counter()
            if work is not None:
                work(s, durations)
            else:
                time.sleep(0.2)
            rest = 0.2 - (time.perf_counter() - t)
            if rest > 0:
                time.sleep(rest)
        v1, w1 = s.virtual_time(), time.perf_counter()
        real, simt = w1 - w0, v1 - v0
        extra = {}
        if durations:
            extra = {"calls": len(durations), "call_ms_med": round(sorted(durations)[len(durations) // 2] * 1000, 1)}
        record(cfg, elf=elf.name, boot_s=round(boot_s, 1), real_s=round(real, 2), sim_s=round(simt, 3),
               ratio=round(simt / real, 4), **extra)


def w_fb(coherent: bool):
    def work(s, durations):
        t = time.perf_counter()
        s.read_framebuffer(coherent=coherent)
        durations.append(time.perf_counter() - t)
    return work


def w_pause(s, durations):
    t = time.perf_counter()
    s._mon.cmd("pause")
    s._mon.cmd("start")
    durations.append(time.perf_counter() - t)


def simsource_run(cfg: str, profile: bool, fb: bool, window: float) -> None:
    from e7twin.simsource import SimSource

    src = SimSource(profile=profile, session_factory=lambda: sim.Session(**KW))
    try:
        src.load("m55_hp", str(DISPLAY))
        t_boot = time.perf_counter()
        src.start()
        s = src._session
        if not s.run_until("DISPLAY frame 1", 90):
            record(cfg, error="pattern absent")
            return
        boot_s = time.perf_counter() - t_boot
        with src._lock:
            v0 = s.virtual_time()
        w0 = time.perf_counter()
        durations: list[float] = []
        while time.perf_counter() - w0 < window:
            time.sleep(0.2)
            if fb:
                t = time.perf_counter()
                src._fb_cache = None  # cadence imposée par ce banc, pas par le cache de 200 ms
                src.framebuffer()
                durations.append(time.perf_counter() - t)
        with src._lock:
            v1 = s.virtual_time()
        w1 = time.perf_counter()
        extra = {}
        if durations:
            extra = {"calls": len(durations), "call_ms_med": round(sorted(durations)[len(durations) // 2] * 1000, 1)}
        record(cfg, elf=DISPLAY.name, profile=profile, boot_s=round(boot_s, 1), real_s=round(w1 - w0, 2),
               sim_s=round(v1 - v0, 3), ratio=round((v1 - v0) / (w1 - w0), 4), poll_hz=round(src._poll_hz, 2), **extra)
    finally:
        src.close()


def python_cost() -> None:
    """Coût du 1er appel `python` du monitor (IronPython) : session neuve, avant puis pendant le boot."""
    with sim.Session() as s:
        s._open()
        res = {}
        for name in ("python_1", "python_2", "readdw_ref"):
            t = time.perf_counter()
            if name.startswith("python"):
                s._mon.cmd('python "1+1"')
            else:
                s._mon.cmd("sysbus ReadDoubleWord 0x49031000")
            res[name + "_ms"] = round((time.perf_counter() - t) * 1000, 1)
        record("py", **res)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window", type=float, default=8.0, help="durée réelle mesurée (s)")
    ap.add_argument("--only", default="a,b,c,d,dp,df,ref,py")
    ap.add_argument("--opt", default=None, help="commande monitor passée avant le chargement (config q)")
    ap.add_argument("--adv", action="store_true", help="Session(advance_immediately=True)")
    ap.add_argument("--no-skip", action="store_true", help="Session(skip_busy_loops=False) : état d'avant le cycle 11")
    args = ap.parse_args(argv)
    if args.adv:
        KW["advance_immediately"] = True
    if args.no_skip:
        KW["skip_busy_loops"] = False
    for cfg in args.only.split(","):
        try:
            if cfg == "a":
                session_run("a", DISPLAY, "DISPLAY frame 1", args.window)
            elif cfg == "b":
                session_run("b", DISPLAY, "DISPLAY frame 1", args.window, w_fb(True))
            elif cfg == "c":
                session_run("c", DISPLAY, "DISPLAY frame 1", args.window, w_fb(False))
            elif cfg == "pc":
                session_run("pc", DISPLAY, "DISPLAY frame 1", args.window, w_pause)
            elif cfg == "q":
                session_run("q:" + (args.opt or ""), DISPLAY, "DISPLAY frame 1", args.window, opt=args.opt)
            elif cfg == "ref":
                session_run("ref", LOOP, "load loop start", args.window)
            elif cfg == "d":
                simsource_run("d", False, False, args.window)
            elif cfg == "dp":
                simsource_run("dp", True, False, args.window)
            elif cfg == "df":
                simsource_run("df", False, True, args.window)
            elif cfg == "py":
                python_cost()
        except Exception as e:  # noqa: BLE001 — une config qui échoue ne doit pas perdre les autres
            record(cfg, error=f"{type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
