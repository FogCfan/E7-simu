"""Modèle de la carte Alif Ensemble E7 (agent 01).

Charge `board/ensemble_e7.yaml` (source de vérité unique), le valide et génère la
plateforme Renode `e7twin/renode/e7.repl`.

Contrat partagé (docs/INTERFACES.md § e7twin.board) : `Region`, `Core`, `AtocSlot`,
`Peripheral`, `Board`, `load()`, `generate_repl()`, `BoardError`.
Les champs ajoutés au-delà du contrat ont tous une valeur par défaut (`source`, `soc_core_id`,
`visible_to`/`local`/`attrs` pour les périphériques, `layout` pour la carte) : ils ne changent
pas la construction positionnelle prévue par le contrat.

Régénérer le .repl :  python -m e7twin.board --repl
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

__all__ = [
    "AtocSlot",
    "Board",
    "BoardError",
    "Core",
    "Peripheral",
    "Region",
    "generate_repl",
    "load",
]

BOARD_FILENAME = "ensemble_e7.yaml"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPL_PATH = Path(__file__).resolve().parent / "renode" / "e7.repl"

# Fenêtre du modèle NS16550 de Renode (registres 16550 standard, stride 4). Au-delà (USR, TFL, SRR, DLF…),
# ce sont les registres DesignWare : stub Python de l'agent 04 (e7twin/renode/stubs.py).
NS16550_WINDOW = 0x40

REGION_KINDS = ("tcm", "sram", "mram", "xip")
SLOT_KINDS = ("mram", "ospi")
# Type de région qui doit contenir un slot ATOC de ce type.
SLOT_CONTAINER_KIND = {"mram": "mram", "ospi": "xip"}


class BoardError(ValueError):
    """Modèle de carte invalide (YAML illisible, champ manquant, chevauchement, alias…)."""


# ----------------------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class Region:
    name: str
    base: int
    size: int
    kind: str  # 'tcm' | 'sram' | 'mram' | 'xip'
    visible_to: tuple[str, ...]
    local: bool
    alias_of: str | None
    source: str = field(default="", compare=False)

    @property
    def end(self) -> int:
        """Fin exclusive."""
        return self.base + self.size

    def contains(self, addr: int) -> bool:
        return self.base <= addr < self.end

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "base": self.base,
            "size": self.size,
            "end": self.end,
            "kind": self.kind,
            "visible_to": list(self.visible_to),
            "local": self.local,
            "alias_of": self.alias_of,
            "source": self.source,
        }


@dataclass(frozen=True)
class Core:
    id: str
    type: str
    freq_hz: int
    fpu: str | None  # "dp" (double précision) ; "dp+neon" pour les A32 ; None si absente
    mve: bool
    npu: str | None  # ex. "ethos-u55-256"
    executable: bool
    soc_core_id: int | None = field(default=None, compare=False)
    source: str = field(default="", compare=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "freq_hz": self.freq_hz,
            "fpu": self.fpu,
            "mve": self.mve,
            "npu": self.npu,
            "executable": self.executable,
            "soc_core_id": self.soc_core_id,
            "source": self.source,
        }


@dataclass(frozen=True)
class AtocSlot:
    core: str
    name: str
    base: int
    size: int
    kind: str  # 'mram' | 'ospi'
    source: str = field(default="", compare=False)

    @property
    def end(self) -> int:
        return self.base + self.size

    def to_dict(self) -> dict:
        return {
            "core": self.core,
            "name": self.name,
            "base": self.base,
            "size": self.size,
            "end": self.end,
            "kind": self.kind,
            "source": self.source,
        }


@dataclass(frozen=True)
class Peripheral:
    name: str
    type: str
    base: int
    size: int
    irq: dict[str, int]  # id de cœur -> numéro d'IRQ NVIC sur ce cœur
    visible_to: tuple[str, ...] = ()  # cœurs qui voient le bloc (() = non précisé)
    local: bool = False  # True = fenêtre privée du cœur (même adresse, bloc différent)
    attrs: dict[str, Any] = field(default_factory=dict, compare=False)  # link, role, reg_width…
    source: str = field(default="", compare=False)

    @property
    def end(self) -> int:
        return self.base + self.size

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "base": self.base,
            "size": self.size,
            "end": self.end,
            "irq": dict(self.irq),
            "visible_to": list(self.visible_to),
            "local": self.local,
            "attrs": _jsonable(self.attrs),
            "source": self.source,
        }


@dataclass
class Board:
    cores: tuple[Core, ...]
    memories: tuple[Region, ...]
    atoc_slots: tuple[AtocSlot, ...]
    peripherals: tuple[Peripheral, ...]
    layout: dict[str, Any] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    # -- accès
    def core(self, id: str) -> Core:
        for c in self.cores:
            if c.id == id:
                return c
        raise KeyError(f"cœur inconnu : {id!r} (connus : {[c.id for c in self.cores]})")

    def region(self, name: str) -> Region:
        for r in self.memories:
            if r.name == name:
                return r
        raise KeyError(f"région inconnue : {name!r}")

    def peripheral(self, name: str) -> Peripheral:
        for p in self.peripherals:
            if p.name == name:
                return p
        raise KeyError(f"périphérique inconnu : {name!r}")

    def regions_for(self, core_id: str) -> list[Region]:
        self.core(core_id)  # KeyError si inconnu
        return sorted((r for r in self.memories if core_id in r.visible_to), key=lambda r: r.base)

    def region_at(self, addr: int, core_id: str) -> Region | None:
        for r in self.regions_for(core_id):
            if r.contains(addr):
                return r
        return None

    def peripherals_for(self, core_id: str) -> list[Peripheral]:
        self.core(core_id)
        return sorted(
            (p for p in self.peripherals if not p.visible_to or core_id in p.visible_to),
            key=lambda p: p.base,
        )

    def slot(self, core_id: str, kind: str = "mram") -> AtocSlot:
        for s in self.atoc_slots:
            if s.core == core_id and s.kind == kind:
                return s
        raise KeyError(f"pas de slot ATOC {kind!r} pour le cœur {core_id!r}")

    def to_dict(self) -> dict:
        return {
            "info": _jsonable(self.info),
            "cores": [c.to_dict() for c in self.cores],
            "memories": [r.to_dict() for r in self.memories],
            "atoc_slots": [s.to_dict() for s in self.atoc_slots],
            "peripherals": [p.to_dict() for p in self.peripherals],
            "layout": _jsonable(self.layout),
            "sources": dict(self.sources),
        }


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    return str(obj)


# ----------------------------------------------------------------------------- chargement


def _resolve_path(path: str | Path | None) -> Path:
    tried: list[str] = []
    if path is not None:
        p = Path(path)
        if p.is_file():
            return p
        raise FileNotFoundError(f"modèle de carte introuvable : {p}")
    env = os.environ.get("E7TWIN_BOARD")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        tried.append(f"$E7TWIN_BOARD={p}")
    p = REPO_ROOT / "board" / BOARD_FILENAME
    if p.is_file():
        return p
    tried.append(str(p))
    try:
        from importlib import resources

        res = resources.files("e7twin").joinpath("_board", BOARD_FILENAME)
        if res.is_file():
            return Path(str(res))
        tried.append(str(res))
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - paquet namespace
        tried.append("e7twin/_board/" + BOARD_FILENAME)
    raise FileNotFoundError("modèle de carte introuvable ; chemins essayés : " + ", ".join(tried))


def load(path: str | Path | None = None) -> Board:
    """Charge et valide le modèle de carte. Lève `BoardError` si le modèle est invalide."""
    p = _resolve_path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise BoardError(f"{p}: YAML invalide : {e}") from e
    board = _build(raw, where=str(p))
    validate(board)
    return board


def _req(d: dict, key: str, where: str) -> Any:
    if not isinstance(d, dict):
        raise BoardError(f"{where}: entrée attendue sous forme de mapping, reçu {type(d).__name__}")
    if key not in d:
        raise BoardError(f"{where}: champ obligatoire manquant : {key!r}")
    return d[key]


def _int(v: Any, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise BoardError(f"{where}: entier attendu, reçu {v!r}")
    if v < 0:
        raise BoardError(f"{where}: valeur négative {v!r}")
    return v


def _source(d: dict, where: str, sources: dict[str, str]) -> str:
    src = d.get("source")
    if not isinstance(src, str) or not src.strip():
        raise BoardError(f"{where}: champ 'source' obligatoire (règle : toute valeur est sourcée)")
    key = src.split()[0]
    if sources and key not in sources:
        raise BoardError(f"{where}: source {key!r} absente de la table 'sources'")
    return src


def _list(raw: dict, key: str, where: str) -> list:
    v = raw.get(key)
    if not isinstance(v, list) or not v:
        raise BoardError(f"{where}: section {key!r} absente ou vide")
    return v


def _build(raw: Any, where: str) -> Board:
    if not isinstance(raw, dict):
        raise BoardError(f"{where}: le document YAML doit être un mapping")
    sources = raw.get("sources") or {}
    if not isinstance(sources, dict):
        raise BoardError(f"{where}: 'sources' doit être un mapping clé -> URL")

    cores = []
    for i, c in enumerate(_list(raw, "cores", where)):
        w = f"{where}: cores[{i}]"
        cores.append(
            Core(
                id=str(_req(c, "id", w)),
                type=str(_req(c, "type", w)),
                freq_hz=_int(_req(c, "freq_hz", w), w + ".freq_hz"),
                fpu=_req(c, "fpu", w),
                mve=bool(_req(c, "mve", w)),
                npu=_req(c, "npu", w),
                executable=bool(_req(c, "executable", w)),
                soc_core_id=c.get("soc_core_id"),
                source=_source(c, w, sources),
            )
        )

    memories = []
    for i, m in enumerate(_list(raw, "memories", where)):
        w = f"{where}: memories[{i}]"
        memories.append(
            Region(
                name=str(_req(m, "name", w)),
                base=_int(_req(m, "base", w), w + ".base"),
                size=_int(_req(m, "size", w), w + ".size"),
                kind=str(_req(m, "kind", w)),
                visible_to=tuple(_req(m, "visible_to", w) or ()),
                local=bool(m.get("local", False)),
                alias_of=m.get("alias_of"),
                source=_source(m, w, sources),
            )
        )

    slots = []
    for i, s in enumerate(_list(raw, "atoc_slots", where)):
        w = f"{where}: atoc_slots[{i}]"
        slots.append(
            AtocSlot(
                core=str(_req(s, "core", w)),
                name=str(_req(s, "name", w)),
                base=_int(_req(s, "base", w), w + ".base"),
                size=_int(_req(s, "size", w), w + ".size"),
                kind=str(_req(s, "kind", w)),
                source=_source(s, w, sources),
            )
        )

    known = {"name", "type", "base", "size", "irq", "visible_to", "local", "source"}
    periphs = []
    for i, p in enumerate(raw.get("peripherals") or []):
        w = f"{where}: peripherals[{i}]"
        irq = p.get("irq") or {}
        if not isinstance(irq, dict):
            raise BoardError(f"{w}: 'irq' doit être un mapping cœur -> numéro")
        periphs.append(
            Peripheral(
                name=str(_req(p, "name", w)),
                type=str(_req(p, "type", w)),
                base=_int(_req(p, "base", w), w + ".base"),
                size=_int(_req(p, "size", w), w + ".size"),
                irq={str(k): _int(v, f"{w}.irq.{k}") for k, v in irq.items()},
                visible_to=tuple(p.get("visible_to") or ()),
                local=bool(p.get("local", False)),
                attrs={k: v for k, v in p.items() if k not in known},
                source=_source(p, w, sources),
            )
        )

    info = raw.get("board") or {}
    return Board(
        cores=tuple(cores),
        memories=tuple(memories),
        atoc_slots=tuple(slots),
        peripherals=tuple(periphs),
        layout=raw.get("layout") or {},
        info=info if isinstance(info, dict) else {},
        sources={str(k): str(v) for k, v in sources.items()},
    )


# ----------------------------------------------------------------------------- validation


def _overlap(a_base: int, a_end: int, b_base: int, b_end: int) -> bool:
    return a_base < b_end and b_base < a_end


def _dupes(names: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    out: set[str] = set()
    for n in names:
        (out if n in seen else seen).add(n)
    return out


def validate(board: Board) -> None:
    """Vérifie la cohérence du modèle ; lève `BoardError` à la première incohérence."""
    core_ids = [c.id for c in board.cores]
    errs: list[str] = []

    for label, names in (
        ("cœur", core_ids),
        ("région", [r.name for r in board.memories]),
        ("slot", [s.name for s in board.atoc_slots]),
        ("périphérique", [p.name for p in board.peripherals]),
    ):
        if d := _dupes(names):
            errs.append(f"{label}(s) en double : {sorted(d)}")

    for c in board.cores:
        if c.freq_hz <= 0:
            errs.append(f"cœur {c.id}: fréquence nulle")

    by_name = {r.name: r for r in board.memories}
    for r in board.memories:
        if r.kind not in REGION_KINDS:
            errs.append(f"région {r.name}: kind {r.kind!r} hors {REGION_KINDS}")
        if r.size <= 0:
            errs.append(f"région {r.name}: taille nulle")
        if r.end > 1 << 32:
            errs.append(f"région {r.name}: dépasse l'espace 32 bits")
        if not r.visible_to:
            errs.append(f"région {r.name}: visible_to vide")
        for cid in r.visible_to:
            if cid not in core_ids:
                errs.append(f"région {r.name}: cœur inconnu {cid!r} dans visible_to")
        if r.alias_of is not None:
            tgt = by_name.get(r.alias_of)
            if tgt is None:
                errs.append(f"région {r.name}: alias_of {r.alias_of!r} inconnue")
            else:
                if tgt.size != r.size:
                    errs.append(
                        f"région {r.name}: taille {r.size:#x} ≠ taille de {tgt.name} ({tgt.size:#x})"
                    )
                if tgt.alias_of is not None:
                    errs.append(f"région {r.name}: alias d'un alias ({tgt.name})")
                if tgt.kind != r.kind:
                    errs.append(f"région {r.name}: kind {r.kind} ≠ kind de {tgt.name} ({tgt.kind})")
                if r.local:
                    errs.append(f"région {r.name}: un alias global ne peut pas être local")

    # Chevauchements entre régions visibles d'un même cœur
    for cid in core_ids:
        regs = sorted((r for r in board.memories if cid in r.visible_to), key=lambda r: r.base)
        for a, b in zip(regs, regs[1:]):
            if a.end > b.base:
                errs.append(
                    f"chevauchement pour {cid}: {a.name} [{a.base:#010x},{a.end:#010x}) "
                    f"et {b.name} [{b.base:#010x},{b.end:#010x})"
                )

    # Slots ATOC
    for s in board.atoc_slots:
        if s.core not in core_ids:
            errs.append(f"slot {s.name}: cœur inconnu {s.core!r}")
        if s.kind not in SLOT_KINDS:
            errs.append(f"slot {s.name}: kind {s.kind!r} hors {SLOT_KINDS}")
            continue
        want = SLOT_CONTAINER_KIND[s.kind]
        if s.size <= 0 or not any(
            r.kind == want and r.base <= s.base and s.end <= r.end for r in board.memories
        ):
            errs.append(
                f"slot {s.name} [{s.base:#010x},{s.end:#010x}) hors de toute région {want} "
                f"(un slot {s.kind} doit être entièrement en {want.upper()})"
            )
    slots = sorted(board.atoc_slots, key=lambda s: s.base)
    for a, b in zip(slots, slots[1:]):
        if a.end > b.base:
            errs.append(f"slots ATOC qui se chevauchent : {a.name} et {b.name}")

    # Périphériques
    for p in board.peripherals:
        if p.size <= 0:
            errs.append(f"périphérique {p.name}: taille nulle")
        for cid in (*p.visible_to, *p.irq):
            if cid not in core_ids:
                errs.append(f"périphérique {p.name}: cœur inconnu {cid!r}")
        for cid in p.irq:
            if p.visible_to and cid not in p.visible_to:
                errs.append(f"périphérique {p.name}: IRQ vers {cid} qui ne voit pas le bloc")
    for cid in core_ids:
        per = sorted(
            (p for p in board.peripherals if not p.visible_to or cid in p.visible_to),
            key=lambda p: p.base,
        )
        for a, b in zip(per, per[1:]):
            if a.end > b.base:
                errs.append(f"chevauchement de périphériques pour {cid}: {a.name} et {b.name}")
        for p in per:
            for r in board.memories:
                if cid in r.visible_to and _overlap(p.base, p.end, r.base, r.end):
                    errs.append(f"périphérique {p.name} chevauche la région {r.name} pour {cid}")

    if errs:
        raise BoardError("modèle de carte invalide :\n  - " + "\n  - ".join(errs))


# ----------------------------------------------------------------------------- Renode


def renode_cpu_name(core_id: str) -> str:
    """m55_hp -> cpu_hp, m55_he -> cpu_he (noms attendus par sim.py / e7.resc)."""
    return "cpu_" + core_id.split("_", 1)[1]


def _nvic_name(core_id: str) -> str:
    return "nvic_" + core_id.split("_", 1)[1]


def _h(v: int) -> str:
    return f"0x{v:08x}"


def _point(addr: int, cpu: str) -> str:
    return f"sysbus new Bus.BusPointRegistration {{ address: {_h(addr)}; cpu: {cpu} }}"


def generate_repl(board: Board) -> str:
    """Produit le texte du .repl Renode (fonction pure, pas d'écriture disque)."""
    m55 = [c for c in board.cores if c.executable]
    absent = [c for c in board.cores if not c.executable]
    L: list[str] = []
    w = L.append

    w("// " + "=" * 76)
    w("// FICHIER GÉNÉRÉ — NE PAS ÉDITER.")
    w("// Source : board/ensemble_e7.yaml ; régénérer avec : python -m e7twin.board --repl")
    w(f"// Carte : {board.info.get('name', '?')} ({board.info.get('kit', '?')}), "
      f"SoC {board.info.get('soc', '?')}")
    w("// Point de départ : renode platforms/cpus/alif_ae822fa0e5597bs0.repl (E8 RTSS-HE).")
    w("// " + "=" * 76)
    w("")
    for c in absent:
        w(f"// {c.id} ({c.type}, {c.freq_hz // 1_000_000} MHz) : ABSENT — Renode n'a pas de modèle "
          "Cortex-A32 ; cœur inerte en v1.")
    w("")

    # --- CPU + NVIC
    for idx, c in enumerate(m55):
        cpu, nvic = renode_cpu_name(c.id), _nvic_name(c.id)
        scs = next((p for p in board.peripherals if p.type == "nvic" and c.id in p.visible_to), None)
        if scs is None:
            raise BoardError(f"pas de bloc 'nvic' (SCS) pour {c.id} dans le YAML")
        w(f"// --- {c.id} : {c.type} @ {c.freq_hz // 1_000_000} MHz, FPU {c.fpu}, "
          f"MVE {'oui' if c.mve else 'non'}, NPU {c.npu}")
        w(f"{cpu}: CPU.CortexM @ sysbus")
        w('    cpuType: "cortex-m55"')
        w(f"    cpuId: {idx}")
        w(f"    nvic: {nvic}")
        w("")
        w(f"{nvic}: IRQControllers.NVIC @ {{")
        w(f"    {_point(scs.base, cpu)}")
        w("}")
        w(f"    -> {cpu}@0")
        w(f"    systickFrequency: {c.freq_hz}")
        w("")
        for d in (p for p in board.peripherals if p.type == "dwt" and c.id in p.visible_to):
            w(f"{d.name}: Miscellaneous.DWT @ {{")
            w(f"    {_point(d.base, cpu)}")
            w("}")
            w(f"    frequency: {c.freq_hz}")
            w("")

    # --- Mémoires
    w("// --- Mémoires")
    aliases: dict[str, list[Region]] = {}
    for r in board.memories:
        if r.alias_of:
            aliases.setdefault(r.alias_of, []).append(r)
    m55_ids = {c.id for c in m55}
    for r in board.memories:
        if r.alias_of:
            continue  # enregistré avec sa région cible
        regs: list[str] = []
        if r.local:
            for cid in r.visible_to:
                if cid in m55_ids:
                    regs.append(_point(r.base, renode_cpu_name(cid)))
        elif any(cid in m55_ids for cid in r.visible_to):
            regs.append(f"sysbus {_h(r.base)}")
        for a in aliases.get(r.name, []):
            regs.append(f"sysbus {_h(a.base)}")
        if not regs:
            w(f"// {r.name} : non visible des cœurs simulés, omise")
            continue
        note = f"  // + alias global {', '.join(a.name for a in aliases.get(r.name, []))}" \
            if r.name in aliases else ""
        w(f"// {r.name} ({r.kind}, {r.size // 1024} Ko){note}")
        if len(regs) == 1:
            w(f"{r.name}: Memory.MappedMemory @ {regs[0]}")
        else:
            w(f"{r.name}: Memory.MappedMemory @ {{")
            w(";\n".join(f"    {x}" for x in regs))
            w("}")
        w(f"    size: {_h(r.size)}")
        w("")

    # --- UART
    w(f"// --- UART (DesignWare 16550, registres 32 bits) : 0x00-{NS16550_WINDOW - 1:#x} en NS16550, le reste en stub (<nom>_dw)")
    for p in board.peripherals:
        if p.type != "uart16550":
            continue
        role = f"  // {p.attrs['role']}" if p.attrs.get("role") else ""
        w(f"{p.name}: UART.NS16550 @ sysbus <{_h(p.base)}, +{NS16550_WINDOW:#x}>{role}")
        w(f"    wideRegisters: {'true' if p.attrs.get('reg_width', 4) == 4 else 'false'}")
        dests = [f"{_nvic_name(cid)}@{n}" for cid, n in p.irq.items() if cid in m55_ids]
        if dests:
            w(f"    -> {' | '.join(dests)}")
        w("")

    # --- Non modélisés : MHU, NPU, blocs système locaux
    w("// --- Non modélisés ici (stubs Python de l'agent 04) :")
    for p in board.peripherals:
        if p.type in ("mhu_rx", "mhu_tx", "ethos-u55") or (p.type == "sysctrl" and p.local):
            irq = ", ".join(f"{k}@{v}" for k, v in p.irq.items()) or "-"
            w(f"//   {p.name:<22} {p.type:<9} {_h(p.base)} +{p.size:#x}  "
              f"{','.join(p.visible_to)} local={str(p.local).lower()} irq {irq}")
    w("")

    # --- Blocs système globaux : Tag (lecture 0, journalisée) en attendant les stubs
    tags = [p for p in board.peripherals if p.type == "sysctrl" and not p.local]
    if tags:
        w("sysbus:")
        w("    init add:")
        for p in tags:
            w(f'        Tag <{_h(p.base)} {p.size:#x}> "{p.name}"')
        w("")
    return "\n".join(L).rstrip() + "\n"


def write_repl(board: Board | None = None, out: str | Path = DEFAULT_REPL_PATH) -> Path:
    board = board or load()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(generate_repl(board), encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m e7twin.board", description=__doc__.splitlines()[0])
    ap.add_argument("--board", help="chemin du YAML (défaut : résolution de load())")
    ap.add_argument("--repl", action="store_true", help="régénère e7twin/renode/e7.repl")
    ap.add_argument("--out", default=str(DEFAULT_REPL_PATH), help="chemin de sortie du .repl")
    ap.add_argument("--json", action="store_true", help="affiche Board.to_dict() en JSON")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : accents
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        board = load(args.board)
    except (BoardError, FileNotFoundError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 1
    if args.json:
        import json

        print(json.dumps(board.to_dict(), indent=2, ensure_ascii=False))
    if args.repl:
        print(f"écrit : {write_repl(board, args.out)}")
    if not (args.json or args.repl):
        print(f"OK : {len(board.cores)} cœurs, {len(board.memories)} régions, "
              f"{len(board.atoc_slots)} slots, {len(board.peripherals)} périphériques")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
