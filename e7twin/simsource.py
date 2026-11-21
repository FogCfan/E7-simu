"""`SimSource` : `MetricsSource` branchée sur une `sim.Session` Renode (agent 05).

Cadence
-------
Un thread unique interroge la session (monitor telnet, un aller-retour par commande) :
- chaque poll coûte ~4 commandes par cœur chargé (`ExecutedInstructions`, `PC`, `IsHalted`, lecture du
  mot à PC) + 1 pour le temps virtuel ; l'ordre de grandeur est de 10 à 50 ms par poll ;
- cadence visée : un poll toutes les `period` s (0,25 s par défaut = 4 Hz) ; si un poll dure plus longtemps,
  le suivant part tout de suite (la cadence réelle est donc ≤ 4 Hz, publiée dans `status()["poll_hz"]`) ;
- les fautes (CFSR/HFSR) et le watermark de pile (lecture de toute la pile) sont plus chers : ils ne sont
  relevés que tous les `slow_every` polls (4 → ~1 Hz).
Le serveur diffuse à 10 Hz mais ne republie un `sample` que lorsqu'un nouveau a été calculé.

Charge : load_pct = 100 × Δinstructions / (fréquence × Δtemps virtuel), hypothèse **CPI = 1**
(approximation : voir metrics.py). Session.open règle `PerformanceInMips` à la fréquence du cœur,
donc le temps virtuel est cohérent avec cette hypothèse.

Δt virtuel nul : voir `_poll` (aucun échantillon publié, instructions reportées sur la fenêtre suivante).
`status()["instructions"]` expose le cumul brut d'instructions par cœur (preuve d'exécution indépendante du temps).

La `Session` n'est pas thread-safe : tous les accès passent par `self._lock`.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import board as board_mod
from .metrics import Sample, make_core_metrics

PROFILE_TOP = 20
_FRAME_SUFFIX = re.compile(r"\s*\((?:entry|exit)\)$")


def parse_collapsed(text: str, top: int = PROFILE_TOP) -> list[dict]:
    """Fichier CollapsedStack de Renode (`a;b;c N`, N = instructions exécutées avec cette pile) → top des
    fonctions par instructions **exclusives** (feuille de la pile), `{name, instructions, pct}`."""
    acc: dict[str, int] = {}
    for line in text.splitlines():
        stack, _, n = line.strip().rpartition(" ")
        if not stack or not n.isdigit():
            continue
        leaf = _FRAME_SUFFIX.sub("", stack.rsplit(";", 1)[-1]).strip()
        acc[leaf] = acc.get(leaf, 0) + int(n)
    total = sum(acc.values())
    rows = sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [{"name": k, "instructions": v, "pct": round(100.0 * v / total, 2)} for k, v in rows]


class SimSource:
    def __init__(self, board: board_mod.Board | None = None, *, session_factory: Callable[[], Any] | None = None,
                 check_fn: Callable[..., Any] | None = None, period: float = 0.25, slow_every: int = 4,
                 profile: bool = False):
        self.board = board or board_mod.load()
        self._factory = session_factory or self._default_factory
        self._check_fn = check_fn
        self.period = period
        self.profile_on_load = profile       # True : profiler activé dès le chargement de l'image
        self._profiled: set[str] = set()
        self.slow_every = max(1, slow_every)
        self._exec = [c for c in self.board.cores if c.executable]
        self._lock = threading.RLock()       # accès session + état
        self._slock = threading.Lock()       # dernier sample / files de sortie
        self._session: Any = None
        self._state = "stopped"
        self._message: str | None = None
        self._images: dict[str, str] = {}
        self._static: dict[str, dict[str, int]] = {}   # core -> {région: octets de données}
        self._stack_region: dict[str, str] = {}
        self._stack_wm: dict[str, int | None] = {}
        self._poll_hz = 0.0
        self._reset_run()
        self._latest = self._empty_sample()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="simsource", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ construction
    def _default_factory(self):
        from . import sim
        return sim.Session(self.board)

    def _ensure(self):
        if self._session is None:
            s = self._factory()
            s.__enter__()
            self._session = s
        return self._session

    def _reset_run(self) -> None:
        self._last_vt: float | None = None
        self._last_instr: dict[str, int] = {}
        self._instr_total: dict[str, int] = {}
        self._uart_idx = 0
        self._seen_faults: set[str] = set()
        self._polls = 0
        self._uart_q: list[dict] = []
        self._ev_q: list[dict] = []

    def _empty_sample(self, t: float = 0.0, cores: dict | None = None, events: list | None = None) -> Sample:
        cores = cores if cores is not None else {c.id: make_core_metrics(0, c.freq_hz, 1.0) for c in self._exec}
        return Sample(round(t, 4), cores, self._memory(), events or [])

    # ------------------------------------------------------------ mémoire
    def _memory(self) -> dict[str, dict[str, Any]]:
        static: dict[str, int] = {}
        for usage in self._static.values():
            for name, n in usage.items():
                static[name] = static.get(name, 0) + n
        wm_by_region: dict[str, int] = {}
        for core, wm in self._stack_wm.items():
            reg = self._stack_region.get(core)
            if wm is not None and reg:
                wm_by_region[reg] = wm_by_region.get(reg, 0) + wm
        out: dict[str, dict[str, Any]] = {}
        for r in self.board.memories:
            name = r.alias_of or r.name
            st = static.get(r.name, static.get(name, 0))
            wm = wm_by_region.get(r.name, wm_by_region.get(name))
            out[r.name] = {"static_bytes": st,
                           "watermark_bytes": None if wm is None else min(r.size, st + wm),
                           "size": r.size}
        return out

    def _compute_static(self, core: str, path: str) -> None:
        fn = self._check_fn
        if fn is None:
            from . import check
            fn = check.run
        try:
            rep = fn(path, core)
            self._static[core] = {n: u.data_bytes for n, u in rep.regions_usage.items()}
        except Exception:  # un ELF non analysable ne doit pas empêcher la simulation
            self._static[core] = {}

    # ------------------------------------------------------------ MetricsSource : commandes
    def load(self, core: str, path: str) -> None:
        c = self.board.core(core)  # KeyError
        if not c.executable:
            raise ValueError(f"le cœur {core} n'est pas exécutable")
        p = Path(path)
        if not p.is_file():
            raise ValueError(f"image introuvable : {path}")
        from .sim import SimError
        with self._lock:
            try:
                s = self._ensure()
                if self._state == "running":
                    s.pause()
                s.load_elf(core, p)
            except SimError as e:
                raise ValueError(str(e)) from e
            self._images[core] = str(p)
            self._profiled.discard(core)
            if self.profile_on_load:
                self._enable_profile(core)
            self._compute_static(core, str(p))
            painted = getattr(s, "_painted", {}).get(core)
            self._stack_wm[core] = None
            self._stack_region.pop(core, None)
            if painted:
                reg = self.board.region_at(painted[0], core)
                if reg:
                    self._stack_region[core] = reg.name
            self._state = "stopped"
            self._message = None
            self._reset_run()
            self._publish(self._empty_sample())

    def start(self) -> None:
        with self._lock:
            if self._state == "error":
                raise RuntimeError(self._message or "simulation en erreur : reset requis")
            s = self._ensure()
            s.start()  # SimError (RuntimeError) si aucune image
            self._state = "running"

    def pause(self) -> None:
        with self._lock:
            if self._state == "running":
                self._session.pause()
                self._state = "paused"

    def reset(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.reset()
                self._profiled.clear()  # les profilers sont perdus avec la remise à zéro
            self._state = "stopped"
            self._message = None
            self._stack_wm = {c: None for c in self._stack_wm}
            self._reset_run()
            self._publish(self._empty_sample())

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        with self._lock:
            if self._session is not None:
                try:
                    self._session.__exit__(None, None, None)
                finally:
                    self._session = None

    def status(self) -> dict:
        with self._slock:
            t = self._latest.t_virtual_s
        cores = {}
        for c in self.board.cores:
            if not c.executable:
                cores[c.id] = "non exécuté"
            elif c.id not in self._images:
                cores[c.id] = "sans image"
            else:
                cores[c.id] = "actif" if self._state == "running" else self._state
        st = {"state": self._state, "fake": False, "cores": cores, "images": dict(self._images),
              "t_virtual_s": t, "poll_hz": round(self._poll_hz, 2),
              "instructions": dict(self._instr_total)}
        if self._message:
            st["message"] = self._message
        return st

    def _enable_profile(self, core: str) -> str | None:
        """Active le profiler Renode du cœur ; renvoie un message d'erreur, ou None si actif."""
        if core in self._profiled:
            return None
        try:
            self._session.enable_profiler(core)
        except Exception as e:
            return f"activation du profiler impossible : {e}"
        self._profiled.add(core)
        return None

    def profile(self, core: str) -> dict:
        """Top 20 des fonctions par instructions exclusives (profiler Renode CollapsedStack, symboles de l'ELF
        résolus par Renode). Activé à la demande (ou au `load` avec `profile=True`) : la collecte commence alors,
        donc une activation tardive ne voit pas le boot."""
        c = self.board.core(core)
        if not c.executable:
            raise ValueError(f"le cœur {core} n'est pas exécutable")
        out = {"fake": False, "core": core, "available": False, "functions": [], "trace_url": None}
        with self._lock:
            if core not in self._images or self._session is None:
                out["message"] = f"aucune image chargée sur {core}"
                return out
            err = self._enable_profile(core)
            if err:
                out["message"] = err
                return out
            try:
                path = self._session.flush_profiler(core)
                text = path.read_text(encoding="utf-8", errors="replace") if path else ""
            except Exception as e:
                out["message"] = f"lecture du profil impossible : {e}"
                return out
        fns = parse_collapsed(text)
        out["functions"] = fns
        out["available"] = bool(fns)
        if not fns:
            out["message"] = "profiler actif, aucune donnée collectée (simulation pas démarrée ?)"
        return out

    # ------------------------------------------------------------ MetricsSource : lecture
    def sample(self) -> Sample:
        with self._slock:
            return self._latest

    def uart_lines(self) -> list[dict]:
        with self._slock:
            out, self._uart_q = self._uart_q, []
        return out

    def events(self) -> list[dict]:
        with self._slock:
            out, self._ev_q = self._ev_q, []
        return out

    def _publish(self, sample: Sample, uart: list | None = None, events: list | None = None) -> None:
        with self._slock:
            self._latest = sample
            if uart:
                self._uart_q += uart
            if events:
                self._ev_q += events

    # ------------------------------------------------------------ thread d'échantillonnage
    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            with self._lock:
                if self._state == "running" and self._session is not None:
                    try:
                        self._poll()
                    except Exception as e:  # Renode mort, telnet coupé…
                        self._state = "error"
                        self._message = f"{type(e).__name__}: {e}"
            elapsed = time.monotonic() - t0
            self._poll_hz = 1.0 / max(elapsed, self.period)
            self._stop.wait(max(0.0, self.period - elapsed))

    def _poll(self) -> None:
        s = self._session
        vt = s.virtual_time()
        active = [c for c in self._exec if c.id in self._images]
        if self._last_vt is None:
            # départ à froid : l'origine du temps virtuel et des compteurs est le dé-halt (0 s, 0 instruction),
            # la rafale du boot est donc comptée dans la première fenêtre au lieu d'être perdue.
            self._last_vt = 0.0
            self._last_instr = {c.id: 0 for c in active}
        raw = {c.id: s.cpu_stats(c.id)["instructions"] for c in active}
        self._instr_total = dict(raw)  # cumul brut, toujours à jour (même sans nouvel échantillon)
        dt = vt - self._last_vt
        if dt <= 1e-9:
            # Δt virtuel nul (palier du boot : le temps virtuel est figé alors que le cœur s'exécute) : pas de
            # division par zéro, aucun échantillon n'est publié (le dernier reste valable) et les références
            # (_last_vt, _last_instr) ne bougent pas : les instructions exécutées pendant le palier sont
            # comptées dans la fenêtre suivante dont le Δt est > 0.
            return
        self._polls += 1
        cores: dict[str, dict[str, float]] = {}
        for c in self._exec:
            if c.id in raw:
                d = max(0, raw[c.id] - self._last_instr.get(c.id, 0))
                self._last_instr[c.id] = raw[c.id]
                cores[c.id] = make_core_metrics(d, c.freq_hz, dt)
            else:
                cores[c.id] = make_core_metrics(0, c.freq_hz, dt)
        self._last_vt = vt
        events: list[dict] = []
        for m in s.mhu_events():
            events.append({"t": round(m.t_virtual_s, 4), "kind": "mhu", "core": m.src,
                           "detail": f"{m.kind} {m.link} ch{m.channel} {m.src} -> {m.dst} val={m.value:#x}"})
        slow = self._polls % self.slow_every == 1 or self.slow_every == 1
        if slow:
            for f in s.faults():
                key = str(f)
                if key not in self._seen_faults:
                    self._seen_faults.add(key)
                    events.append({"t": round(vt, 4), "kind": "fault", "core": f.core, "detail": key})
            for c in active:
                self._stack_wm[c.id] = s.stack_watermark(c.id)
        lines = s.uart_lines()
        if len(lines) < self._uart_idx:  # session remise à zéro
            self._uart_idx = 0
        uart = [{"core": ln.core, "uart": ln.uart, "t": round(ln.t_virtual_s, 4), "text": ln.text}
                for ln in lines[self._uart_idx:]]
        self._uart_idx = len(lines)
        self._publish(Sample(round(vt, 4), cores, self._memory(), events), uart, events)
