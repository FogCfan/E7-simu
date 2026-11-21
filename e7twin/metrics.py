"""Métriques d'E7 Twin (agent 05) : `Sample`, protocole `MetricsSource`, `FakeSource`.

Contrat : docs/INTERFACES.md § `e7twin.metrics`.

Approximation CPI = 1
---------------------
Le simulateur compte des *instructions*, pas des cycles. La charge est calculée comme

    load_pct = 100 * instructions / (freq_hz * fenêtre_s)

ce qui suppose **1 instruction par cycle** (CPI = 1). C'est une approximation : sur un vrai
Cortex-M55 le CPI est > 1 (accès mémoire, branchements, MVE), donc la charge réelle est
généralement *supérieure* à la valeur affichée. À présenter comme telle dans l'interface.
`wfi_pct = 100 - load_pct`, `mips = instructions / fenêtre_s / 1e6`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from . import board as board_mod

STATES = ("stopped", "running", "paused", "error")


@dataclass
class Sample:
    t_virtual_s: float
    cores: dict[str, dict[str, float]] = field(default_factory=dict)
    memory: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "t_virtual_s": self.t_virtual_s,
            "cores": {k: dict(v) for k, v in self.cores.items()},
            "memory": {k: dict(v) for k, v in self.memory.items()},
            "events": [dict(e) for e in self.events],
        }


def make_core_metrics(instructions: float, freq_hz: int, window_s: float) -> dict[str, float]:
    """load/mips/wfi à partir d'un nombre d'instructions (CPI = 1, voir docstring du module)."""
    cap = freq_hz * window_s
    load = 0.0 if cap <= 0 else min(100.0, 100.0 * instructions / cap)
    return {
        "load_pct": round(load, 2),
        "mips": round(instructions / window_s / 1e6, 2) if window_s > 0 else 0.0,
        "wfi_pct": round(100.0 - load, 2),
    }


@runtime_checkable
class MetricsSource(Protocol):
    """Source de métriques (simulation réelle ou factice).

    `uart_lines()` et `events()` renvoient les éléments apparus depuis l'appel précédent (drain).
    `uart_lines()` : dicts `{core, uart, t, text}` ; `events()` : dicts `{t, kind, core, detail}`.
    """

    def sample(self) -> Sample: ...
    def uart_lines(self) -> list[dict]: ...
    def events(self) -> list[dict]: ...
    def start(self) -> None: ...
    def pause(self) -> None: ...
    def reset(self) -> None: ...
    def status(self) -> dict: ...
    def load(self, core: str, path: str) -> None: ...
    def profile(self, core: str) -> dict: ...


class FakeSource:
    """Simulation factice déterministe (graine) ; le temps virtuel avance de `dt` par `sample()`.

    Cœurs, fréquences et régions viennent de `board.load()`. Les cœurs non exécutables (A32)
    n'apparaissent pas dans `Sample.cores` (contrat v1) mais dans `status()["cores"]` avec
    l'état « non exécuté ».
    """

    def __init__(self, board: board_mod.Board | None = None, seed: int = 1234, dt: float = 0.1):
        self.board = board or board_mod.load()
        self.seed = seed
        self.dt = dt
        self._exec = [c for c in self.board.cores if c.executable]
        self._uart = str(self.board.info.get("printf_uart") or "uart")
        self._images: dict[str, str] = {}
        self._state = "stopped"
        self._init_run()

    # -- état
    def _init_run(self) -> None:
        self._rng = random.Random(self.seed)
        self._t = 0.0
        self._tick = 0
        self._counters = {c.id: 0 for c in self._exec}
        self._uart_q: list[dict] = []
        self._ev_q: list[dict] = []
        self._pending_pong: list[tuple[float, str, str]] = []
        self._phase = {c.id: self._rng.uniform(0, 2 * math.pi) for c in self._exec}
        self._period = {c.id: self._rng.uniform(4.0, 9.0) for c in self._exec}
        self._base = {c.id: self._rng.uniform(0.25, 0.55) for c in self._exec}
        self._static: dict[str, int] = {}
        for r in self.board.memories:
            if r.alias_of and r.alias_of in self._static:
                self._static[r.name] = self._static[r.alias_of]
            else:
                frac = self._rng.uniform(0.05, 0.35) if r.kind in ("mram", "xip") else self._rng.uniform(0.02, 0.2)
                self._static[r.name] = int(r.size * frac) // 16 * 16
        self._stack = {r.name: 256 for r in self.board.memories if r.kind in ("tcm", "sram")}
        self._started_uart = False

    def start(self) -> None:
        self._state = "running"

    def pause(self) -> None:
        if self._state == "running":
            self._state = "paused"

    def reset(self) -> None:
        self._init_run()
        self._state = "stopped"

    def load(self, core: str, path: str) -> None:
        c = self.board.core(core)  # KeyError si inconnu
        if not c.executable:
            raise ValueError(f"le cœur {core} n'est pas exécutable")
        self._images[core] = str(path)

    def status(self) -> dict:
        cores = {c.id: ("actif" if self._state == "running" else self._state) if c.executable
                 else "non exécuté" for c in self.board.cores}
        return {"state": self._state, "fake": True, "cores": cores,
                "images": dict(self._images), "t_virtual_s": round(self._t, 4)}

    # -- génération
    def _emit_uart(self, t: float, core: str, text: str) -> None:
        self._uart_q.append({"core": core, "uart": self._uart, "t": round(t, 4), "text": text})

    def _emit_event(self, t: float, kind: str, core: str, detail: str, out: list) -> None:
        ev = {"t": round(t, 4), "kind": kind, "core": core, "detail": detail}
        out.append(ev)
        self._ev_q.append(ev)

    def sample(self) -> Sample:
        if self._state != "running":
            return self._snapshot([], 0.0)
        rng, dt = self._rng, self.dt
        t0 = self._t
        self._t = t1 = t0 + dt
        self._tick += 1
        events: list[dict] = []

        # UART
        if not self._started_uart and self._exec:
            self._started_uart = True
            for c in self._exec:
                self._emit_uart(t0, c.id, "Hello World!")
        if self._tick % 10 == 0:
            for c in self._exec:
                self._counters[c.id] += 1
                self._emit_uart(t1, c.id, f"tick {self._counters[c.id]}")

        # MHU ping/pong entre les deux premiers cœurs exécutables
        if len(self._exec) >= 2:
            a, b = self._exec[0].id, self._exec[1].id
            if self._tick % 5 == 0:
                self._emit_event(t1, "mhu", a, f"doorbell ch0 -> {b} (ping)", events)
                self._pending_pong.append((t1 + 0.02, b, a))
            for item in [p for p in self._pending_pong if p[0] <= t1 + dt]:
                self._pending_pong.remove(item)
                self._emit_event(item[0], "mhu", item[1], f"doorbell ch0 -> {item[2]} (pong)", events)
        if self._exec and rng.random() < 0.02:
            c = rng.choice(self._exec)
            self._emit_event(t1, "unmapped", c.id,
                             f"lecture non mappée @0x{rng.randrange(0x4000_0000, 0x5000_0000) & ~3:08X}", events)

        # Charge : instructions par fenêtre
        cores: dict[str, dict[str, float]] = {}
        for c in self._exec:
            x = self._base[c.id] + 0.3 * math.sin(self._phase[c.id] + t1 * 2 * math.pi / self._period[c.id])
            x += rng.uniform(-0.04, 0.04)
            frac = min(1.0, max(0.0, x))
            cores[c.id] = make_core_metrics(frac * c.freq_hz * dt, c.freq_hz, dt)

        # Pile : watermark monotone
        for name in self._stack:
            if rng.random() < 0.3:
                self._stack[name] += 16 * rng.randint(1, 6)
        return self._snapshot(events, dt, cores)

    def _snapshot(self, events: list, window: float, cores: dict | None = None) -> Sample:
        if cores is None:
            cores = {c.id: make_core_metrics(0, c.freq_hz, self.dt) for c in self._exec}
        mem: dict[str, dict[str, Any]] = {}
        for r in self.board.memories:
            wm = None
            if r.name in self._stack:
                root = r.alias_of if r.alias_of in self._stack else r.name
                wm = min(r.size, self._static[r.name] + self._stack[root])
            mem[r.name] = {"static_bytes": self._static[r.name], "watermark_bytes": wm, "size": r.size}
        return Sample(round(self._t, 4), cores, mem, events)

    def uart_lines(self) -> list[dict]:
        out, self._uart_q = self._uart_q, []
        return out

    def events(self) -> list[dict]:
        out, self._ev_q = self._ev_q, []
        return out

    def profile(self, core: str) -> dict:
        c = self.board.core(core)
        if not c.executable:
            raise ValueError(f"le cœur {core} n'est pas exécutable")
        rng = random.Random(f"{self.seed}:{core}")
        names = ["main", "app_loop", "memcpy", "arm_conv_q15", "UART_Send", "MHU_send", "SysTick_Handler",
                 "arm_fir_f32", "printf"]
        weights = sorted((rng.random() for _ in names), reverse=True)
        tot = sum(weights)
        return {
            "fake": True,
            "core": core,
            "approximation": "CPI = 1 ; données factices",
            "functions": [{"name": n, "pct": round(100 * w / tot, 2),
                           "samples": int(1000 * w / tot)} for n, w in zip(names, weights)],
            "trace_url": None,
        }
