"""Pile pire cas (règle 4 du checker, agent 03).

Entrées : les fichiers ``.su`` de ``-fstack-usage`` (taille de trame par fonction) et le graphe
d'appels tiré de ``arm-none-eabi-objdump -d`` (``bl``, ``blx`` direct, ``b`` vers une autre
fonction = appel terminal, compté comme un appel par prudence).

Limites assumées (affichées dans le rapport) :

- ``blx rN`` (appel indirect), récursion et ``alloca``/VLA (``dynamic`` dans le .su) rendent la
  pile **non bornée** : le résultat est alors une borne inférieure et le verdict au mieux WARN ;
- une fonction atteignable sans entrée ``.su`` (bibliothèque, assembleur) compte pour 0 : borne
  inférieure aussi, donc WARN ;
- pile = trame du fil principal (depuis ``Reset_Handler``) + une exception (trame d'exception +
  pire handler de la table des vecteurs) ; pas d'imbrication d'interruptions.
"""

from __future__ import annotations

import bisect
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import toolchain

_SU_RE = re.compile(r"^(.*?):(\d+):(\d+):(.*)$")
_CLONE_RE = re.compile(r"(\.(constprop|isra|part|lto_priv)\.\d+|\.cold)+$")
_LABEL_RE = re.compile(r"^([0-9a-f]+) <(.+)>:$")
_INSN_RE = re.compile(r"^\s*([0-9a-f]+):\s+(\S+)\s*(.*)$")
_TARGET_RE = re.compile(r"(?:^|,\s*)([0-9a-f]{1,8})(?:\s|$)")
_JUMPS = {"b", "beq", "bne", "bcs", "bhs", "bcc", "blo", "bmi", "bpl", "bvs", "bvc", "bhi", "bls",
          "bge", "blt", "bgt", "ble", "bal", "cbz", "cbnz"}


def find_su(elf: Path, su_dir: str | Path | None = None) -> list[Path]:
    """Fichiers .su : ``su_dir`` si fourni, sinon le dossier de l'ELF (récursif)."""
    root = Path(su_dir) if su_dir else elf.parent
    return sorted(root.rglob("*.su")) if root.is_dir() else []


def read_su(paths: list[Path]) -> dict[str, tuple[int, bool]]:
    """nom de fonction -> (octets de trame, non borné) ; maximum si le nom apparaît plusieurs fois."""
    out: dict[str, tuple[int, bool]] = {}
    for p in paths:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split("\t")
            if len(parts) < 3 or not (m := _SU_RE.match(parts[0])) or not parts[1].strip().isdigit():
                continue
            name, size, dyn = m.group(4), int(parts[1]), parts[2].strip() == "dynamic"
            old = out.get(name)
            out[name] = (max(size, old[0]), dyn or old[1]) if old else (size, dyn)
    return out


def objdump_path() -> Path | None:
    p = toolchain.find("arm-none-eabi-objdump")
    if p is not None:
        return p
    gcc = toolchain.find("arm-none-eabi-gcc")
    if gcc is not None:
        cand = gcc.with_name(gcc.name.replace("gcc", "objdump"))
        if cand.is_file():
            return cand
    return None


@dataclass
class _Func:
    start: int
    name: str
    calls: set[int] = field(default_factory=set)
    indirect: bool = False
    thunk: bool = False  # veneer / thunk de l'éditeur de liens : saut pur, 0 octet de pile


_WORD_RE = re.compile(r"^\s*([0-9a-f]+):\s+\.word\s+(0x[0-9a-f]+)")
_LITERAL_RE = re.compile(r"@\s*\(?([0-9a-f]+)\b")
_MOVIP_RE = re.compile(r"^(?:ip|r12),\s*#(\S+)")


def call_graph(objdump: Path, elf: Path) -> dict[int, _Func]:
    cp = subprocess.run([str(objdump), "-d", "--no-show-raw-insn", str(elf)], capture_output=True,
                        text=True, encoding="utf-8", errors="replace", timeout=300)
    if cp.returncode != 0:
        raise RuntimeError(f"objdump a échoué : {cp.stderr.strip()[:200]}")
    funcs: dict[int, _Func] = {}
    cur: _Func | None = None
    pending: list[tuple[_Func, str, int]] = []  # (appelant, 'call'|'jump', cible)
    words: dict[int, int] = {}  # adresse -> mot littéral (.word)
    literal_jumps: list[tuple[_Func, int]] = []  # ldr pc, [pc, #n] : adresse du littéral
    movw: dict[int, int] = {}  # fonction -> valeur en cours de construction dans ip (movw/movt)
    for line in cp.stdout.splitlines():
        if m := _LABEL_RE.match(line):
            start = int(m.group(1), 16)
            cur = funcs.setdefault(start, _Func(start, m.group(2)))
            continue
        if w := _WORD_RE.match(line):
            words[int(w.group(1), 16)] = int(w.group(2), 16)
            continue
        if cur is None or not (m := _INSN_RE.match(line)):
            continue
        mnem = m.group(2).lower().split(".")[0]
        raw_ops = m.group(3)
        ops = raw_ops.split(";")[0].split("@")[0].strip()
        # Thunks de ld : ldr pc, [pc, #n] + adresse littérale ; movw/movt ip puis bx ip.
        if mnem == "ldr" and re.match(r"pc,\s*\[pc\b", ops) and (lit := _LITERAL_RE.search(raw_ops)):
            literal_jumps.append((cur, int(lit.group(1), 16)))
            continue
        if mnem in ("movw", "movt") and (mi := _MOVIP_RE.match(ops)):
            try:
                v = int(mi.group(1), 0)
            except ValueError:
                continue
            old = movw.get(cur.start, 0)
            movw[cur.start] = (v & 0xFFFF) if mnem == "movw" else (old & 0xFFFF) | ((v & 0xFFFF) << 16)
            continue
        if mnem == "bx" and ops in ("ip", "r12") and cur.start in movw:
            pending.append((cur, "thunk", movw[cur.start] & ~1))
            continue
        if mnem in ("bl", "blx") or mnem in _JUMPS:
            t = _TARGET_RE.search(ops)
            if t:
                pending.append((cur, "call" if mnem in ("bl", "blx") else "jump", int(t.group(1), 16)))
            elif mnem == "blx":
                cur.indirect = True
    starts = sorted(funcs)
    for caller, lit in literal_jumps:
        if lit in words:
            pending.append((caller, "thunk", words[lit] & ~1))
    for caller, kind, target in pending:
        if kind == "thunk":
            caller.thunk = True
        i = bisect.bisect_right(starts, target) - 1
        if i >= 0 and (starts[i] != caller.start or kind == "call"):  # bl sur soi-même = récursion
            caller.calls.add(starts[i])
    return funcs


# Liste blanche : fonctions crt0/newlib dont l'appel indirect est connu. Chacune appelle, via
# ``blx rN``, ``main`` et les entrées de .preinit_array / .init_array (lues dans l'ELF). Tout
# autre appel indirect reste « non borné ».
CRT0_INDIRECT_WHITELIST = ("_mainCRTStartup", "_start", "__libc_init_array")


def _lookup(su: dict[str, tuple[int, bool]], names: list[str]) -> tuple[int, bool] | None:
    for n in names:
        for cand in (n, _CLONE_RE.sub("", n)):
            if cand in su:
                return su[cand]
    return None


def analyze(elf: Path, *, su_dir: str | Path | None, func_syms: dict[int, list[str]],
            thread_root: int | None, isr_roots: list[int], stack_size: int | None,
            frame_bytes: int, warn_pct: float,
            crt0_targets: list[int] | None = None) -> tuple[str, str, int | float | str | None, int | float | str | None]:
    """Renvoie (verdict, message, mesuré, limite) ; verdict dans PASS/WARN/FAIL/SKIP."""
    hint = ("compiler avec -fstack-usage (les .su sont écrits à côté des .o) puis les laisser dans le "
            "dossier de build de l'ELF ou donner leur dossier (option su_dir / --su-dir)")
    su_files = find_su(elf, su_dir)
    if not su_files:
        return "SKIP", "aucun fichier .su trouvé : " + hint + ".", None, None
    su = read_su(su_files)
    if not su:
        return "SKIP", f"{len(su_files)} fichier(s) .su sans entrée exploitable : " + hint + ".", None, None
    od = objdump_path()
    if od is None:
        return "SKIP", "arm-none-eabi-objdump introuvable (graphe d'appels impossible).", None, None
    if stack_size is None:
        return "SKIP", "taille de pile réservée inconnue (ni __StackLimit/__StackTop, ni section .stack).", None, None
    if thread_root is None:
        return "SKIP", "Reset_Handler introuvable : point d'entrée du graphe d'appels inconnu.", None, None
    try:
        graph = call_graph(od, elf)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        return "SKIP", f"graphe d'appels indisponible : {e}", None, None

    modelled: list[str] = []
    if crt0_targets:
        tg = [t for t in crt0_targets if t in graph]
        for f in graph.values():
            if f.indirect and f.name in CRT0_INDIRECT_WHITELIST:
                f.indirect = False
                f.calls.update(t for t in tg if t != f.start)
                modelled.append(f.name)

    def frame(f: _Func) -> tuple[int, bool, bool]:
        """(octets, dynamique, trouvé dans les .su)"""
        if f.thunk:
            return 0, False, True  # veneer : saut pur, pas de trame
        hit = _lookup(su, func_syms.get(f.start, []) + [f.name])
        if hit is None:
            return 0, False, "veneer" in f.name
        return hit[0], hit[1], True

    memo: dict[int, tuple[int, int | None]] = {}
    reach: set[int] = set()
    cycles: list[list[str]] = []

    def depth(a: int, path: list[int]) -> int:
        reach.add(a)
        if a in memo:
            return memo[a][0]
        f = graph.get(a)
        if f is None:
            return 0
        own, _, _ = frame(f)
        best, nxt = 0, None
        path.append(a)
        for c in sorted(f.calls):
            if c in path:
                cyc = [graph[x].name for x in path[path.index(c):]]
                if cyc not in cycles:
                    cycles.append(cyc)
                continue
            d = depth(c, path)
            if d > best:
                best, nxt = d, c
        path.pop()
        memo[a] = (own + best, nxt)
        return own + best

    def chain(a: int) -> str:
        names = []
        while a is not None and len(names) < 8:
            names.append(graph[a].name if a in graph else hex(a))
            a = memo.get(a, (0, None))[1]
        return " → ".join(names)

    if thread_root not in graph:
        return "SKIP", "Reset_Handler absent du désassemblage.", None, None
    thread = depth(thread_root, [])
    isr_best, isr_name = 0, None
    for r in isr_roots:
        if r in graph:
            d = depth(r, [])
            if d >= isr_best:
                isr_best, isr_name = d, graph[r].name
    worst = thread + frame_bytes + isr_best
    pct = 100.0 * worst / stack_size

    unbounded: list[str] = []
    for a in sorted(reach):
        f = graph.get(a)
        if f is None:
            continue
        _, dyn, found = frame(f)
        if f.indirect:
            unbounded.append(f"{f.name} (appel indirect blx rN)")
        if dyn:
            unbounded.append(f"{f.name} (alloca/VLA)")
    unbounded += [" → ".join(c + [c[0]]) + " (récursion)" for c in cycles]
    missing = sorted(graph[a].name for a in reach if a in graph and not frame(graph[a])[2])

    core = (f"pile pire cas {worst} o sur {stack_size} o réservés ({pct:.1f} %) : fil principal {thread} o "
            f"[{chain(thread_root)}] + trame d'exception {frame_bytes} o + handler le plus gourmand "
            f"{isr_best} o" + (f" ({isr_name})" if isr_name else "") + ".")
    measured: int | float | str = worst
    limit = f"{stack_size} (WARN > {warn_pct:g} %, FAIL > 100 %)"
    notes = []
    reached_model = [n for n in modelled if any(graph[a].name == n for a in reach if a in graph)]
    if reached_model:
        core += (f" Appel indirect connu, modélisé (crt0 newlib : {', '.join(sorted(set(reached_model)))} "
                 f"appellent main et .preinit_array/.init_array : {len(crt0_targets or [])} cible(s)).")
    if unbounded:
        notes.append("non borné : " + " ; ".join(unbounded))
    if missing:
        notes.append(f"{len(missing)} fonction(s) atteignable(s) sans .su comptée(s) à 0 : "
                     + ", ".join(missing[:6]) + (", …" if len(missing) > 6 else ""))
    if notes:
        measured = f"≥ {worst}"
        core += " Borne inférieure — " + " | ".join(notes) + "."
    if worst > stack_size:
        return "FAIL", core.replace("pile pire cas", "pile dépassée, pire cas", 1), measured, limit
    if notes or pct > warn_pct:
        return "WARN", core, measured, limit
    return "PASS", core, measured, limit
