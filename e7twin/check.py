"""Barrière de compatibilité : analyse statique d'un ELF pour un cœur M55 de l'Ensemble E7 (agent 03).

Contrat : ``docs/INTERFACES.md`` § ``e7twin.check``. Règles implémentées au cycle 1 :

1. ``arch``      — attributs ARM (``.ARM.attributes``) : v8.1-M.Mainline, MVE, FPU ;
2. ``placement`` — chaque PT_LOAD (VMA et LMA) dans une région visible du cœur ; MRAM/XIP dans le
   slot ATOC du cœur ;
3. ``usage``     — taux d'occupation par région (WARN > 85 %, FAIL > 100 %) ;
4. ``stack``     — pile pire cas (.su + graphe d'appels objdump) face à la pile réservée ;
5. ``boot``      — table des vecteurs au début de l'image, SP initial en RAM, Reset_Handler Thumb ;
6. ``atoc``      — JSON SETOOLS : adresse = début de l'image, image dans son slot, pas de chevauchement.

Les règles 4 (``stack``) et 6 (``atoc``) renvoient ``SKIP`` quand leur entrée manque (.su, JSON ATOC).
Aucune adresse ni taille n'est écrite ici : tout vient de ``board.load()``.

Fidélité : analyse **statique** de l'ELF (en-têtes de programme, symboles, attributs). Rien n'est
exécuté ; un PASS garantit le placement et le démarrage, pas le comportement à l'exécution.
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from elftools.common.exceptions import ELFError
from elftools.elf.constants import P_FLAGS
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import ARMAttributesSection, SymbolTableSection

from . import board as board_mod

__all__ = ["RegionUsage", "Report", "RuleResult", "main", "run"]

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
_SEVERITY = {PASS: 0, WARN: 1, FAIL: 2}  # SKIP ne compte pas dans le verdict global

USAGE_WARN_PCT = 85.0  # règle 3 de la fiche 03 : WARN au-delà, FAIL au-delà de 100 %

# Tag_CPU_arch (ABI ARM, addenda) attendu par type de cœur ; 21 = v8.1-M.Mainline.
_EXPECTED_ARCH = {"cortex-m55": (21, "v8.1-M.Mainline")}
_ARCH_NAMES = {
    10: "v7", 11: "v6-M", 12: "v6S-M", 13: "v7E-M", 14: "v8-A", 15: "v8-R",
    16: "v8-M.Baseline", 17: "v8-M.Mainline", 21: "v8.1-M.Mainline",
}
_MVE_NAMES = {0: "aucun", 1: "entier", 2: "entier + flottant"}
_VECTOR_SYMBOLS = ("__Vectors", "__vector_table", "g_pfnVectors", "__isr_vector", "_vectors")
_RAM_KINDS = ("tcm", "sram")
EM_ARM = "EM_ARM"


# ----------------------------------------------------------------------------- contrat


@dataclass
class RuleResult:
    id: str  # "arch" | "placement" | "usage" | "stack" | "boot" | "atoc"
    title: str
    verdict: str  # "PASS" | "WARN" | "FAIL" | "SKIP" (SKIP : ajouté au contrat, cycle 1)
    message: str
    measured: int | float | str | None = None
    limit: int | float | str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "verdict": self.verdict,
            "message": self.message,
            "measured": self.measured,
            "limit": self.limit,
        }


@dataclass
class RegionUsage:
    used_bytes: int  # total = données + réserve pile/tas
    size_bytes: int
    pct: float  # used_bytes / size_bytes
    reserved_bytes: int = 0  # dont réserve pile/tas (extension du contrat, cycle 2)

    @property
    def data_bytes(self) -> int:
        return self.used_bytes - self.reserved_bytes

    @property
    def data_pct(self) -> float:
        return round(100.0 * self.data_bytes / self.size_bytes, 2)

    def to_dict(self) -> dict:
        return {"used_bytes": self.used_bytes, "size_bytes": self.size_bytes, "pct": self.pct,
                "reserved_bytes": self.reserved_bytes, "data_bytes": self.data_bytes}


@dataclass
class Report:
    image: str
    core: str
    verdict: str
    rules: list[RuleResult] = field(default_factory=list)
    regions_usage: dict[str, RegionUsage] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "image": self.image,
            "core": self.core,
            "verdict": self.verdict,
            "rules": [r.to_dict() for r in self.rules],
            "regions_usage": {k: v.to_dict() for k, v in self.regions_usage.items()},
        }


def _worst(verdicts: list[str]) -> str:
    real = [v for v in verdicts if v in _SEVERITY]
    return max(real, key=_SEVERITY.__getitem__) if real else PASS


def _hex(v: int) -> str:
    return f"0x{v:08X}"


# ----------------------------------------------------------------------------- lecture ELF


@dataclass
class _Seg:
    index: int
    vaddr: int
    paddr: int
    filesz: int
    memsz: int
    flags: int
    data: bytes

    @property
    def executable(self) -> bool:
        return bool(self.flags & P_FLAGS.PF_X)


def _load_segments(elf: ELFFile) -> list[_Seg]:
    segs = []
    for i, s in enumerate(elf.iter_segments()):
        h = s.header
        if h.p_type != "PT_LOAD" or h.p_memsz == 0:
            continue
        segs.append(_Seg(i, h.p_vaddr, h.p_paddr, h.p_filesz, h.p_memsz, h.p_flags,
                         s.data() if h.p_filesz else b""))
    return segs


def _arm_attributes(elf: ELFFile) -> dict[str, Any] | None:
    sec = elf.get_section_by_name(".ARM.attributes")
    if not isinstance(sec, ARMAttributesSection):
        return None
    attrs: dict[str, Any] = {}
    for sub in sec.iter_subsections():
        for subsub in sub.iter_subsubsections():
            for a in subsub.iter_attributes():
                attrs[a.tag] = a.value
    return attrs


def _symbols(elf: ELFFile) -> dict[str, int]:
    out: dict[str, int] = {}
    for sec in elf.iter_sections():
        if isinstance(sec, SymbolTableSection):
            for sym in sec.iter_symbols():
                if sym.name and sym.name not in out:
                    out[sym.name] = sym["st_value"]
    return out


def _symbol_sizes(elf: ELFFile) -> dict[str, int]:
    out: dict[str, int] = {}
    for sec in elf.iter_sections():
        if isinstance(sec, SymbolTableSection):
            for sym in sec.iter_symbols():
                if sym.name and sym.name not in out:
                    out[sym.name] = sym["st_size"]
    return out


def _func_symbols(elf: ELFFile) -> dict[int, list[str]]:
    """Adresse (bit Thumb retiré) -> noms des symboles de fonction."""
    out: dict[int, list[str]] = {}
    for sec in elf.iter_sections():
        if isinstance(sec, SymbolTableSection):
            for sym in sec.iter_symbols():
                if sym.name and sym["st_info"]["type"] == "STT_FUNC":
                    out.setdefault(sym["st_value"] & ~1, []).append(sym.name)
    return out


_HEAP_SECTIONS = (".heap", ".__app_heap")


def _stack_range(elf: ELFFile, syms: dict[str, int]) -> tuple[int, int] | None:
    """Pile réservée [bas, haut) : ``__StackLimit``/``__StackTop``, sinon la section ``.stack``."""
    if "__StackLimit" in syms and "__StackTop" in syms and syms["__StackTop"] > syms["__StackLimit"]:
        return syms["__StackLimit"], syms["__StackTop"]
    sec = elf.get_section_by_name(".stack")
    if sec is not None and sec["sh_size"]:
        return sec["sh_addr"], sec["sh_addr"] + sec["sh_size"]
    return None


def _reserves(elf: ELFFile, syms: dict[str, int]) -> list[tuple[int, int]]:
    """Réserve pile/tas du script de lien (adresses d'exécution)."""
    out = []
    if (st := _stack_range(elf, syms)) is not None:
        out.append(st)
    if "__HeapBase" in syms and "__HeapLimit" in syms and syms["__HeapLimit"] > syms["__HeapBase"]:
        out.append((syms["__HeapBase"], syms["__HeapLimit"]))
    else:
        for name in _HEAP_SECTIONS:
            sec = elf.get_section_by_name(name)
            if sec is not None and sec["sh_size"]:
                out.append((sec["sh_addr"], sec["sh_addr"] + sec["sh_size"]))
    return out


# ----------------------------------------------------------------------------- règle 1


def _rule_arch(elf: ELFFile, core: board_mod.Core) -> RuleResult:
    title = "Architecture (attributs ARM)"
    if elf["e_machine"] != EM_ARM:
        return RuleResult("arch", title, FAIL, f"l'ELF n'est pas ARM (e_machine={elf['e_machine']}).",
                          str(elf["e_machine"]), EM_ARM)
    attrs = _arm_attributes(elf)
    if attrs is None or "TAG_CPU_ARCH" not in attrs:
        return RuleResult("arch", title, WARN,
                          "section .ARM.attributes absente ou sans Tag_CPU_arch (ELF strippé ?) : "
                          "architecture non vérifiable.", None, None)
    want = _EXPECTED_ARCH.get(core.type)
    if want is None:
        return RuleResult("arch", title, WARN, f"type de cœur {core.type!r} sans architecture attendue "
                          "connue du checker.", attrs["TAG_CPU_ARCH"], None)
    want_val, want_name = want
    arch = attrs["TAG_CPU_ARCH"]
    arch_name = _ARCH_NAMES.get(arch, f"arch #{arch}")
    mve = attrs.get("TAG_MVE_ARCH", 0)
    fp = attrs.get("TAG_FP_ARCH", 0)
    measured = f"{arch_name} (cpu {attrs.get('TAG_CPU_NAME', '?')}), MVE={_MVE_NAMES.get(mve, mve)}, FP_arch={fp}"

    problems = []
    if arch != want_val:
        problems.append(f"architecture {arch_name} au lieu de {want_name}")
    if mve and not core.mve:
        problems.append("code MVE (Helium) sur un cœur sans MVE")
    if fp and core.fpu is None:
        problems.append("code à FPU matérielle sur un cœur sans FPU")
    if problems:
        return RuleResult("arch", title, FAIL,
                          f"ELF incompatible avec {core.id} ({core.type}) : " + " ; ".join(problems) + ".",
                          measured, f"{want_name}, MVE={'oui' if core.mve else 'non'}, FPU={core.fpu}")
    return RuleResult("arch", title, PASS,
                      f"ELF compilé pour {arch_name}, compatible avec {core.id} ({core.type}).",
                      measured, f"{want_name}, MVE={'oui' if core.mve else 'non'}, FPU={core.fpu}")


# ----------------------------------------------------------------------------- règle 2


def _check_range(b: board_mod.Board, core_id: str, addr: int, size: int, what: str) -> tuple[str | None, board_mod.Region | None]:
    """Renvoie (erreur | None, région) pour l'intervalle [addr, addr+size) vu de ``core_id``."""
    reg = b.region_at(addr, core_id)
    if reg is None:
        return f"{what} {_hex(addr)}+{size:#x} : adresse non mappée pour {core_id}", None
    if addr + size > reg.end:
        return (f"{what} {_hex(addr)}+{size:#x} déborde de la région {reg.name} "
                f"(fin {_hex(reg.end)}, dépassement de {addr + size - reg.end} octets)"), reg
    return None, reg


def _slot_for(b: board_mod.Board, core_id: str, reg: board_mod.Region) -> board_mod.AtocSlot | None:
    kind = {"mram": "mram", "xip": "ospi"}.get(reg.kind)
    if kind is None:
        return None
    try:
        return b.slot(core_id, kind)
    except KeyError:
        return None


def _rule_placement(b: board_mod.Board, core_id: str, segs: list[_Seg]) -> RuleResult:
    title = "Placement des segments"
    if not segs:
        return RuleResult("placement", title, FAIL, "aucun segment PT_LOAD dans l'ELF.", 0, ">= 1")
    errors: list[str] = []
    for s in segs:
        ranges = [("VMA", s.vaddr, s.memsz)]
        if s.filesz:
            ranges.append(("LMA", s.paddr, s.filesz))
        for what, addr, size in ranges:
            err, reg = _check_range(b, core_id, addr, size, f"segment #{s.index} {what}")
            if err:
                errors.append(err)
                continue
            slot = _slot_for(b, core_id, reg) if reg else None
            if reg is not None and reg.kind in ("mram", "xip"):
                if slot is None:
                    errors.append(f"segment #{s.index} {what} {_hex(addr)} en {reg.name} : "
                                  f"{core_id} n'a pas de slot ATOC pour cette mémoire")
                elif not (slot.base <= addr and addr + size <= slot.end):
                    errors.append(
                        f"segment #{s.index} {what} {_hex(addr)}+{size:#x} hors du slot ATOC "
                        f"{slot.name} de {core_id} [{_hex(slot.base)}, {_hex(slot.end)})")
    measured = f"{len(segs)} segment(s) PT_LOAD"
    if errors:
        return RuleResult("placement", title, FAIL, "; ".join(errors) + ".", measured,
                          f"régions visibles de {core_id}")
    return RuleResult("placement", title, PASS,
                      f"les {len(segs)} segment(s) PT_LOAD (VMA et LMA) tombent dans des régions visibles de "
                      f"{core_id} ; l'image en MRAM/XIP reste dans le slot ATOC du cœur.",
                      measured, f"régions visibles de {core_id}")


# ----------------------------------------------------------------------------- règle 3


def _merge(ivals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(ivals):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _subtract(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """a moins b, deux listes d'intervalles déjà fusionnés."""
    out = []
    for lo, hi in a:
        cur = lo
        for blo, bhi in b:
            if bhi <= cur or blo >= hi:
                continue
            if blo > cur:
                out.append((cur, blo))
            cur = max(cur, bhi)
        if cur < hi:
            out.append((cur, hi))
    return out


def _total(ivals: list[tuple[int, int]]) -> int:
    return sum(hi - lo for lo, hi in ivals)


def _usage(b: board_mod.Board, core_id: str, segs: list[_Seg],
           reserves: list[tuple[int, int]] | None = None) -> dict[str, RegionUsage]:
    """Octets occupés par région (union des VMA et LMA, sans double comptage VMA == LMA).

    - Un alias global dont la région cible est visible du cœur (ex. sram3 -> dtcm_hp) est fusionné
      avec elle : les adresses sont ramenées dans la région cible puis dédoublonnées ; l'alias
      n'apparaît pas dans le résultat. Un alias dont la cible n'est pas visible reste séparé.
    - ``reserves`` (pile/tas du script de lien) est comptée à part dans ``reserved_bytes`` ;
      ``data_bytes`` = le reste (données réelles).
    - Pour une région MRAM/XIP, la taille de référence est celle du slot ATOC du cœur : seul ce
      slot est utilisable par l'image (les octets hors slot donnent donc > 100 %).
    """
    regions = b.regions_for(core_id)
    by_name = {r.name: r for r in regions}

    def target(r: board_mod.Region) -> board_mod.Region:
        return by_name.get(r.alias_of, r) if r.alias_of else r

    def project(addr: int, size: int):
        for r in regions:
            lo, hi = max(addr, r.base), min(addr + size, r.end)
            if lo < hi:
                t = target(r)
                yield t.name, lo - r.base + t.base, hi - r.base + t.base

    canon = [r for r in regions if target(r) is r]
    data: dict[str, list[tuple[int, int]]] = {r.name: [] for r in canon}
    resv: dict[str, list[tuple[int, int]]] = {r.name: [] for r in canon}
    for s in segs:
        for addr, size in [(s.vaddr, s.memsz)] + ([(s.paddr, s.filesz)] if s.filesz else []):
            for name, lo, hi in project(addr, size):
                data[name].append((lo, hi))
    for lo, hi in reserves or []:
        for name, a, z in project(lo, hi - lo):
            resv[name].append((a, z))

    usage: dict[str, RegionUsage] = {}
    for r in canon:
        reserve = _merge(resv[r.name])
        real = _subtract(_merge(data[r.name]), reserve)
        used, reserved = _total(real) + _total(reserve), _total(reserve)
        slot = _slot_for(b, core_id, r)
        size = slot.size if slot is not None else r.size
        usage[r.name] = RegionUsage(used, size, round(100.0 * used / size, 2), reserved)
    return usage


def _rule_usage(usage: dict[str, RegionUsage]) -> RuleResult:
    """Verdict sur les **données réelles** ; la réserve pile/tas est affichée à part.

    Le script de lien Alif réserve pile et tas dans la DTCM : les compter comme occupation ferait
    un faux WARN. Le total (données + réserve) > 100 % reste un FAIL.
    """
    title = "Occupation mémoire"
    used = {k: u for k, u in usage.items() if u.used_bytes}
    if not used:
        return RuleResult("usage", title, FAIL, "aucune région occupée (image vide ?).", 0, None)

    def level(u: RegionUsage) -> float:  # pourcentage jugé : total s'il dépasse la capacité, sinon données
        return u.pct if u.pct > 100.0 else u.data_pct

    worst_name, worst = max(used.items(), key=lambda kv: level(kv[1]))
    lv = level(worst)

    def fmt(k: str, u: RegionUsage) -> str:
        s = f"{k} données {u.data_pct:.1f} % ({u.data_bytes}/{u.size_bytes} o)"
        return s + (f" + réserve pile/tas {u.reserved_bytes} o (total {u.pct:.1f} %)" if u.reserved_bytes else "")

    detail = " ; ".join(fmt(k, u) for k, u in used.items())
    if lv > 100.0:
        verdict, why = FAIL, f"{worst_name} dépasse sa capacité (> 100 %)"
    elif lv > USAGE_WARN_PCT:
        verdict, why = WARN, f"{worst_name} : données au-delà de {USAGE_WARN_PCT:g} %"
    else:
        verdict, why = PASS, f"occupation maximale des données {lv:.1f} % ({worst_name})"
    return RuleResult("usage", title, verdict, f"{why}. Détail : {detail}.",
                      f"{lv:.1f} % ({worst_name})", f"WARN > {USAGE_WARN_PCT:g} %, FAIL > 100 %")


# ----------------------------------------------------------------------------- règle 5


def _image_extent(b: board_mod.Board, core_id: str, segs: list[_Seg]) -> tuple[int, int]:
    """[début, fin) de l'image flashée, d'après les LMA des segments chargés.

    Début = plus petit LMA en mémoire non volatile (MRAM/XIP : ce qui est flashé). Un segment à LMA
    en RAM (ex. section de travail) n'en fait pas partie ; si rien n'est en NVM (image chargée
    directement en TCM/SRAM), on prend tous les segments. Taille calculée depuis les segments : le
    ``.bin`` n'existe pas forcément.
    """
    loaded = [s for s in segs if s.filesz]

    def nvm(s: _Seg) -> bool:
        r = b.region_at(s.paddr, core_id)
        return r is not None and r.kind in ("mram", "xip")

    cand = [s for s in loaded if nvm(s)] or loaded
    return min(s.paddr for s in cand), max(s.paddr + s.filesz for s in cand)


def _read_at_lma(segs: list[_Seg], lma: int, size: int) -> bytes | None:
    for s in segs:
        if s.filesz and s.paddr <= lma and lma + size <= s.paddr + s.filesz:
            off = lma - s.paddr
            return s.data[off:off + size]
    return None


def _rule_boot(b: board_mod.Board, core_id: str, segs: list[_Seg], syms: dict[str, int]) -> RuleResult:
    title = "Démarrage (table des vecteurs)"
    loaded = [s for s in segs if s.filesz]
    if not loaded:
        return RuleResult("boot", title, FAIL, "aucune donnée chargée : pas de table des vecteurs.", None, None)
    start, _ = _image_extent(b, core_id, segs)
    fails: list[str] = []
    warns: list[str] = []

    # Position de la table des vecteurs : symbole connu sinon début de l'image.
    vec_lma = start
    sym_name = next((n for n in _VECTOR_SYMBOLS if n in syms), None)
    if sym_name is not None:
        vma = syms[sym_name]
        seg = next((s for s in loaded if s.vaddr <= vma < s.vaddr + s.filesz), None)
        if seg is None:
            warns.append(f"symbole {sym_name} ({_hex(vma)}) hors des données chargées")
        else:
            vec_lma = vma - seg.vaddr + seg.paddr
            if vec_lma != start:
                fails.append(f"la table des vecteurs ({sym_name} @ {_hex(vec_lma)}) n'est pas au début "
                             f"de l'image ({_hex(start)})")
    if vec_lma % 128:
        warns.append(f"table des vecteurs non alignée sur 128 octets ({_hex(vec_lma)}, VTOR)")

    raw = _read_at_lma(segs, vec_lma, 8)
    if raw is None:
        return RuleResult("boot", title, FAIL, f"impossible de lire 8 octets à {_hex(vec_lma)}.", None, None)
    sp, reset = struct.unpack("<II", raw)

    # SP initial : pile pleine-descendante, donc SP == fin de région est valide (sp - 1 dans la région).
    ram = next((r for r in b.regions_for(core_id) if r.kind in _RAM_KINDS and r.base < sp <= r.end), None)
    if ram is None:
        fails.append(f"SP initial {_hex(sp)} hors de toute RAM visible de {core_id}")
    elif sp % 8:
        warns.append(f"SP initial {_hex(sp)} non aligné sur 8 octets (AAPCS)")

    # Reset : Thumb obligatoire, cible dans un segment exécutable, cohérent avec le symbole Reset_Handler.
    if not reset & 1:
        fails.append(f"vecteur de reset {_hex(reset)} sans bit Thumb (bit 0 = 0) : fault au démarrage")
    target = reset & ~1
    if not any(s.executable and s.vaddr <= target < s.vaddr + s.memsz for s in segs):
        fails.append(f"vecteur de reset {_hex(reset)} hors de tout segment exécutable")
    if "Reset_Handler" not in syms:
        warns.append("symbole Reset_Handler absent (ELF strippé ?)")
    else:
        rh = syms["Reset_Handler"]
        if not rh & 1:
            fails.append(f"Reset_Handler {_hex(rh)} n'est pas une fonction Thumb (bit 0 = 0)")
        elif rh != reset:
            warns.append(f"vecteur de reset {_hex(reset)} ≠ Reset_Handler {_hex(rh)}")

    measured = f"SP={_hex(sp)}, Reset={_hex(reset)}, vecteurs@{_hex(vec_lma)}"
    limit = f"vecteurs @ début de l'image ({_hex(start)}), SP en RAM de {core_id}, Reset Thumb"
    if fails:
        return RuleResult("boot", title, FAIL, "; ".join(fails + warns) + ".", measured, limit)
    if warns:
        return RuleResult("boot", title, WARN, "; ".join(warns) + ".", measured, limit)
    return RuleResult("boot", title, PASS,
                      f"table des vecteurs au début de l'image, SP initial {_hex(sp)} dans {ram.name}, "
                      f"reset {_hex(reset)} en Thumb.", measured, limit)


# ----------------------------------------------------------------------------- règle 4


# Trame d'exception empilée par le matériel (ARMv8-M ARM) : 8 mots, ou 26 mots avec contexte FP.
EXC_FRAME_BYTES = 32
EXC_FRAME_FP_BYTES = 104


def _isr_roots(segs: list[_Seg], syms: dict[str, int], sizes: dict[str, int], start: int,
               funcs: dict[int, list[str]]) -> list[int]:
    """Adresses des handlers (entrées 2.. de la table des vecteurs) qui sont des fonctions connues."""
    lma = start
    name = next((n for n in _VECTOR_SYMBOLS if n in syms), None)
    if name is not None:
        vma = syms[name]
        seg = next((s for s in segs if s.filesz and s.vaddr <= vma < s.vaddr + s.filesz), None)
        if seg is not None:
            lma = vma - seg.vaddr + seg.paddr
    n = min(max(sizes.get(name, 64) // 4, 16), 512) if name else 16
    raw = None
    while n > 2:
        raw = _read_at_lma(segs, lma, 4 * n)
        if raw is not None:
            break
        n -= 1
    if raw is None:
        return []
    words = struct.unpack(f"<{n}I", raw)
    return sorted({w & ~1 for w in words[2:] if w & 1 and (w & ~1) in funcs})


def _crt0_targets(elf: ELFFile, syms: dict[str, int], funcs: dict[int, list[str]]) -> list[int]:
    """``main`` + fonctions de .preinit_array / .init_array (cibles des ``blx`` de crt0)."""
    out: set[int] = set()
    if "main" in syms:
        out.add(syms["main"] & ~1)
    for name in (".preinit_array", ".init_array"):
        sec = elf.get_section_by_name(name)
        if sec is None or sec["sh_type"] == "SHT_NOBITS":
            continue
        data = sec.data()
        for (w,) in struct.iter_unpack("<I", data[: len(data) // 4 * 4]):
            if (w & ~1) in funcs:
                out.add(w & ~1)
    return sorted(out)


def _rule_stack(path: Path, c: board_mod.Core, elf: ELFFile, segs: list[_Seg], syms: dict[str, int],
                sizes: dict[str, int], b: board_mod.Board, su_dir: str | Path | None) -> RuleResult:
    title = "Pile (pire cas)"
    from . import stackcheck

    funcs = _func_symbols(elf)
    st = _stack_range(elf, syms)
    start = _image_extent(b, c.id, segs)[0] if any(s.filesz for s in segs) else 0
    rh = syms.get("Reset_Handler")
    verdict, msg, measured, limit = stackcheck.analyze(
        path, su_dir=su_dir, func_syms=funcs, thread_root=None if rh is None else rh & ~1,
        isr_roots=_isr_roots(segs, syms, sizes, start, funcs),
        stack_size=None if st is None else st[1] - st[0],
        frame_bytes=EXC_FRAME_FP_BYTES if c.fpu else EXC_FRAME_BYTES, warn_pct=USAGE_WARN_PCT,
        crt0_targets=_crt0_targets(elf, syms, funcs))
    return RuleResult("stack", title, verdict, msg, measured, limit)


# ----------------------------------------------------------------------------- règle 6


def _addr(v: Any) -> int | None:
    try:
        return v if isinstance(v, int) and not isinstance(v, bool) else int(str(v), 0)
    except (TypeError, ValueError):
        return None


def _atoc_entries(data: Any, key: str = "") -> list[tuple[str, dict]]:
    """Entrées d'un JSON SETOOLS : tout objet portant ``cpu_id`` (quelle que soit sa profondeur)."""
    out: list[tuple[str, dict]] = []
    if isinstance(data, dict):
        if "cpu_id" in data:
            out.append((key, data))
        else:
            for k, v in data.items():
                out += _atoc_entries(v, str(k))
    elif isinstance(data, list):
        for v in data:
            out += _atoc_entries(v, key)
    return out


def _rule_atoc(b: board_mod.Board, core_id: str, segs: list[_Seg], atoc: str | Path | None) -> RuleResult:
    title = "Cohérence ATOC"
    if atoc is None:
        return RuleResult("atoc", title, SKIP, "aucun JSON ATOC fourni (--atoc) : règle non évaluée.", None, None)
    path = Path(atoc)
    try:
        entries = _atoc_entries(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        return RuleResult("atoc", title, FAIL, f"JSON ATOC illisible ({path}) : {e}.", None, None)
    mine = [(n, e) for n, e in entries if str(e["cpu_id"]).lower() == core_id]
    if not mine:
        return RuleResult("atoc", title, FAIL,
                          f"aucune entrée cpu_id={core_id.upper()} dans {path.name} "
                          f"(cpu_id présents : {sorted({str(e['cpu_id']) for _, e in entries})}).", None, None)
    if not any(s.filesz for s in segs):
        return RuleResult("atoc", title, FAIL, "image sans donnée chargée.", None, None)
    start, end = _image_extent(b, core_id, segs)
    size = end - start
    problems: list[str] = []

    for name, e in mine:
        addr = _addr(e.get("mramAddress", e.get("loadAddress")))
        if addr is None:
            problems.append(f"{name} : ni mramAddress ni loadAddress exploitable")
        elif addr != start:
            problems.append(f"{name} : mramAddress {_hex(addr)} ≠ début de l'image {_hex(start)}")

    reg = b.region_at(start, core_id)
    slot = None
    if reg is not None and reg.kind in ("mram", "xip"):
        slot = _slot_for(b, core_id, reg)
    if slot is None:
        problems.append(f"début de l'image {_hex(start)} hors MRAM/XIP : pas de slot ATOC pour {core_id}")
    elif not (slot.base <= start and end <= slot.end):
        problems.append(f"image {_hex(start)}..{_hex(end)} ({size} o) ne tient pas dans le slot "
                        f"{slot.name} [{_hex(slot.base)}, {_hex(slot.end)})")

    for name, e in entries:
        cid = str(e["cpu_id"]).lower()
        if cid == core_id:
            continue
        addr = _addr(e.get("mramAddress", e.get("loadAddress")))
        if addr is None:
            continue
        # Taille de l'autre image : son .bin s'il existe à côté du JSON, sinon seul son début est connu.
        other_end = addr + 1
        binary = e.get("binary")
        if binary and (path.parent / str(binary)).is_file():
            other_end = addr + max((path.parent / str(binary)).stat().st_size, 1)
        if addr < end and start < other_end:
            problems.append(f"chevauchement avec {name} ({cid}, {_hex(addr)}..{_hex(other_end)}) : "
                            f"image {_hex(start)}..{_hex(end)}")
        try:
            b.core(cid)
        except KeyError:
            continue
        oreg = b.region_at(addr, cid)
        if oreg is not None and oreg.kind in ("mram", "xip"):
            oslot = _slot_for(b, cid, oreg)
            if oslot is not None and not (oslot.base <= addr and other_end <= oslot.end):
                problems.append(f"{name} ({cid}) @ {_hex(addr)} hors de son slot {oslot.name}")

    measured = f"image {_hex(start)}..{_hex(end)} ({size} o)"
    limit = f"slot {slot.name} [{_hex(slot.base)}, {_hex(slot.end)})" if slot else None
    if problems:
        return RuleResult("atoc", title, FAIL, "; ".join(problems) + ".", measured, limit)
    return RuleResult("atoc", title, PASS,
                      f"{', '.join(n for n, _ in mine)} : adresse = début de l'image ({_hex(start)}), image "
                      f"({size} o) dans son slot {slot.name}, aucun chevauchement avec les autres entrées "
                      f"({len(entries) - len(mine)}).", measured, limit)


# ----------------------------------------------------------------------------- orchestration


def _rule_npu(b: board_mod.Board, core_id: str, npu_report: str | Path | None) -> RuleResult:
    """Règle 7 : le pic d'arena NPU (``reports/<stem>.npu.json``) tient dans la SRAM du cœur.

    Lève ``ValueError`` si le rapport est illisible ou mal formé (→ code 2 dans ``main``)."""
    title = "Arena NPU"
    if npu_report is None:
        return RuleResult("npu", title, SKIP, "aucun rapport NPU fourni (--npu-report) : règle non évaluée.",
                          None, None)
    from . import npu as npu_mod  # import tardif : npu.py est optionnel pour le reste du checker

    path = Path(npu_report)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        peaks = {str(k): int(v["sram_bytes"]) for k, v in data["results"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"rapport NPU illisible ({path}) : {e!r}") from e
    if not peaks:
        raise ValueError(f"rapport NPU sans résultat ({path})")
    budget = npu_mod.sram_budget_bytes(b, core_id)
    name, peak = max(peaks.items(), key=lambda kv: kv[1])
    if peak > budget:
        return RuleResult("npu", title, FAIL,
                          f"arena NPU {name} = {peak} o > budget SRAM de {core_id} = {budget} o.", peak, budget)
    return RuleResult("npu", title, PASS,
                      f"arena NPU max ({name}) = {peak} o <= budget SRAM de {core_id} = {budget} o.", peak, budget)


def run(elf_path: str | Path, core: str, atoc: str | Path | None = None, *,
        su_dir: str | Path | None = None, npu_report: str | Path | None = None) -> Report:
    """Analyse statique de ``elf_path`` pour le cœur ``core`` (``m55_hp`` ou ``m55_he``).

    ``atoc`` : JSON au format SETOOLS (règle 6). ``su_dir`` : dossier des ``.su`` (règle 4), par
    défaut le dossier de l'ELF. Lève ``KeyError`` (cœur inconnu), ``ValueError`` (cœur non
    exécutable ou fichier non ELF), ``FileNotFoundError``.
    """
    b = board_mod.load()
    c = b.core(core)
    if not c.executable:
        raise ValueError(f"le cœur {core} n'est pas exécutable en v1 (cibles : "
                         f"{[x.id for x in b.cores if x.executable]})")
    path = Path(elf_path)
    with path.open("rb") as f:
        try:
            elf = ELFFile(f)
            segs = _load_segments(elf)
            syms = _symbols(elf)
            sizes = _symbol_sizes(elf)
            arch_rule = _rule_arch(elf, c)
            usage = _usage(b, core, segs, _reserves(elf, syms))
            stack_rule = _rule_stack(path, c, elf, segs, syms, sizes, b, su_dir)
        except ELFError as e:
            raise ValueError(f"{path} n'est pas un ELF valide : {e}") from e

    rules = [
        arch_rule,
        _rule_placement(b, core, segs),
        _rule_usage(usage),
        stack_rule,
        _rule_boot(b, core, segs, syms),
        _rule_atoc(b, core, segs, atoc),
        _rule_npu(b, core, npu_report),
    ]
    return Report(str(path), core, _worst([r.verdict for r in rules]), rules, usage)


def _find_elf(target: Path) -> Path | None:
    if target.is_file():
        return target
    if target.is_dir():
        elfs = [p for p in target.rglob("*.elf") if p.is_file()]
        if elfs:
            return max(elfs, key=lambda p: p.stat().st_mtime)
    return None


def main(target: str | Path, core: str, atoc: str | Path | None = None,
         out_dir: str | Path = "reports", su_dir: str | Path | None = None,
         npu_report: str | Path | None = None) -> int:
    """Point d'entrée CLI : ``e7twin check <projet|elf> --core ... [--atoc ...]``.

    ``target`` : un ELF, ou un dossier de projet (l'ELF ``*.elf`` le plus récent est retenu).
    Écrit ``<out_dir>/<nom>.check.json`` et ``.html``. Code de sortie : 0 (PASS/WARN), 1 (une règle
    en FAIL), 2 (usage : cible introuvable, cœur invalide, ELF illisible).
    """
    from . import report as report_mod

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    elf = _find_elf(Path(target))
    if elf is None:
        print(f"erreur : aucun ELF trouvé pour {target}", file=sys.stderr)
        return 2
    try:
        rep = run(elf, core, atoc, su_dir=su_dir, npu_report=npu_report)
    except (KeyError, ValueError, OSError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 2
    json_path, html_path = report_mod.write(rep, out_dir)
    print(f"{elf} → {core} : {rep.verdict}")
    for r in rep.rules:
        print(f"  [{r.verdict:<4}] {r.id:<9} {r.message}")
    print(f"rapport : {json_path} ; {html_path}")
    return 1 if rep.verdict == FAIL else 0
