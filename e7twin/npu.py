"""Estimation NPU Ethos-U55 de l'Alif Ensemble E7 avec Vela (agent 08).

Renode n'émule pas l'Ethos-U55 : tout ce que produit ce module est une **estimation statique** du
compilateur Vela (modèle de performance), pas une exécution. Voir docs/NPU.md pour les configurations
choisies, leurs sources et leurs limites.

    e7twin npu model.tflite            # les deux NPU
    npu.run(model, "u55_256")          # -> NpuReport
    npu.main(model, npu=None, out_dir="reports") -> code retour (0 ok, 1 arena > SRAM, 2 erreur)

Les paramètres du système mémoire (`vela.ini`) sont générés à partir de `board.load()` (fréquences,
tailles SRAM). Ceux qui ne sont pas sourcés sont listés dans `ASSUMPTIONS` et recopiés dans chaque rapport.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from e7twin import board as board_mod

__all__ = [
    "ASSUMPTIONS",
    "NPUS",
    "NpuError",
    "NpuReport",
    "NpuResult",
    "main",
    "parse_vela_output",
    "run",
    "vela_ini",
    "vela_python",
]

REPO_ROOT = Path(__file__).resolve().parents[1]

# clé publique -> (accélérateur Vela, cœur de la carte)
NPUS: dict[str, tuple[str, str]] = {
    "u55_256": ("ethos-u55-256", "m55_hp"),
    "u55_128": ("ethos-u55-128", "m55_he"),
}

# Paramètres de l'interconnexion NON sourcés (le HRM ne les donne pas dans les fichiers dont on dispose) :
# ce sont des hypothèses, reprises des valeurs « High_End_Embedded » de Vela, sauf mention contraire.
SRAM_PORT = {"clock_scale": 1.0, "burst_length": 32, "read_latency": 32, "write_latency": 32,
             "max_reads": 4, "max_writes": 4}
MRAM_PORT = {"clock_scale": 0.25, "burst_length": 128, "read_latency": 64, "write_latency": 0,
             "max_reads": 2, "max_writes": 0}
OSPI_PORT = {"clock_scale": 0.0625, "burst_length": 128, "read_latency": 128, "write_latency": 0,
             "max_reads": 2, "max_writes": 0}

ASSUMPTIONS = [
    "Largeur AXI 64 bits (R : page série Alif, non lue dans un fichier sourcé) ; le port SRAM est modélisé "
    "à clock_scale=1.0 (1 transfert 64 bits par cycle NPU).",
    "Horloge NPU = fréquence du cœur associé dans board/ensemble_e7.yaml (HP 400 MHz = ACLK, V ; HE 160 MHz). "
    "La page série donne 46 GOPS pour l'U55-128, ce qui suggère ~180 MHz : écart non tranché (R).",
    "Latences/burst/profondeur de file du port SRAM : valeurs Vela 'Ethos_U55_High_End_Embedded' (hypothèse).",
    "Débit de lecture MRAM : clock_scale=0.25 (~0,8 Go/s à 400 MHz), latence 64 : hypothèse, à mesurer sur la carte.",
    "Débit OSPI (poids en XIP) : clock_scale=0.0625, latence 128 : hypothèse.",
    "Les opérateurs hors NPU ne sont pas chronométrés : leur coût CPU (CMSIS-NN / TFLM) s'ajoute au temps estimé.",
    "Aucune contention modélisée (A32, autre M55, DMA) sur la SRAM ni sur la MRAM.",
]

FIDELITY = (
    "Estimation statique Vela (modèle de performance), non exécutée : Renode n'émule pas l'Ethos-U55 et rien "
    "n'est vérifié sur la carte réelle."
)

# Mode mémoire Vela : poids en lecture seule hors SRAM (MRAM/OSPI), arena + entrées/sorties en SRAM partagée.
MEMORY_MODE = "E7_Shared_Sram"
_WEIGHTS_AREAS = {"mram": "OnChipFlash", "ospi": "OffChipFlash"}


class NpuError(RuntimeError):
    """Erreur d'environnement ou de compilation (vela absent, modèle illisible, échec de vela)."""


# ----------------------------------------------------------------------------- rapport


@dataclass
class NpuResult:
    npu: str                      # 'u55_256' | 'u55_128'
    accelerator: str              # 'ethos-u55-256'
    core: str                     # 'm55_hp' | 'm55_he'
    ok: bool = False
    error: str | None = None
    clock_hz: int = 0
    cycles_total: int = 0         # cycles NPU incluant les attentes mémoire (goulot réel estimé)
    cycles_npu: int = 0           # cycles de calcul pur
    inference_time_s: float = 0.0
    inferences_per_s: float = 0.0
    macs: int = 0
    sram_bytes: int = 0           # pic arena (SRAM) estimé
    sram_budget_bytes: int = 0    # plus grande région SRAM visible du cœur (board)
    fits: bool = False
    weights_bytes: int = 0        # poids encodés pour l'NPU, stockés en MRAM/OSPI
    weights_original_bytes: int = 0
    weights_region: str = "mram"
    npu_operators: int = 0        # 'passes' Vela exécutées sur l'NPU
    cpu_operators: int = 0        # 'passes' Vela retombant sur le CPU
    cpu_op_types: dict[str, int] = field(default_factory=dict)  # opérateurs TFLite restant sur CPU
    vela_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NpuReport:
    model: str
    model_bytes: int
    model_sha256: str
    results: dict[str, NpuResult]
    vela_version: str | None = None
    weights_region: str = "mram"
    assumptions: list[str] = field(default_factory=lambda: list(ASSUMPTIONS))
    fidelity: str = FIDELITY

    @property
    def fits(self) -> bool:
        return all(r.fits for r in self.results.values() if r.ok)

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results.values())

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "model_bytes": self.model_bytes,
            "model_sha256": self.model_sha256,
            "vela_version": self.vela_version,
            "weights_region": self.weights_region,
            "ok": self.ok,
            "fits": self.fits,
            "results": {k: r.to_dict() for k, r in self.results.items()},
            "assumptions": list(self.assumptions),
            "fidelity": self.fidelity,
        }


# ----------------------------------------------------------------------------- vela.ini


def _clock_hz(brd: board_mod.Board, core_id: str) -> int:
    return int(brd.core(core_id).freq_hz)


def sram_budget_bytes(brd: board_mod.Board, core_id: str) -> int:
    """Plus grande région SRAM (kind 'sram') visible de ce cœur : l'arena doit être contiguë.

    Les TCM ne sont pas comptées (réservées au code/pile du cœur, voir check)."""
    sizes = [m.size for m in brd.memories if m.kind == "sram" and core_id in m.visible_to]
    return max(sizes, default=0)


def _port_lines(prefix: str, p: dict) -> list[str]:
    return [f"{prefix}_{k}={v}" for k, v in p.items()]


def vela_ini(brd: board_mod.Board | None = None, weights: str = "mram") -> str:
    """Génère le vela.ini E7 : une System_Config par NPU + un Memory_Mode partagé."""
    if weights not in _WEIGHTS_AREAS:
        raise NpuError(f"emplacement des poids inconnu : {weights!r} (mram | ospi)")
    brd = brd or board_mod.load()
    area = _WEIGHTS_AREAS[weights]
    wport = MRAM_PORT if weights == "mram" else OSPI_PORT
    out = [
        "; Généré par e7twin.npu.vela_ini() à partir de board/ensemble_e7.yaml. Ne pas éditer à la main.",
        "; Valeurs non sourcées = hypothèses : voir docs/NPU.md et e7twin.npu.ASSUMPTIONS.",
        "",
    ]
    for key, (acc, core) in NPUS.items():
        out += [
            f"; {acc} associé à {core} ; SRAM (AXI0) + {weights.upper()} lecture seule (AXI1)",
            f"[System_Config.E7_{key.upper()}_{weights.upper()}]",
            f"core_clock={_clock_hz(brd, core)}",
            "axi0_port=Sram",
            f"axi1_port={area}",
            *_port_lines("Sram", SRAM_PORT),
            *_port_lines(area, wport),
            "",
        ]
    out += [
        "; Poids en lecture seule (MRAM/OSPI, Axi1) ; arena et entrées/sorties en SRAM (Axi0).",
        f"[Memory_Mode.{MEMORY_MODE}]",
        "const_mem_area=Axi1",
        "arena_mem_area=Axi0",
        "cache_mem_area=Axi0",
        "",
    ]
    return "\n".join(out)


def _system_config_name(npu: str, weights: str) -> str:
    return f"E7_{npu.upper()}_{weights.upper()}"


# ----------------------------------------------------------------------------- environnement vela


def vela_python() -> str | None:
    """Interpréteur Python qui sait lancer Vela, ou None.

    Ordre : $E7TWIN_VELA_PYTHON, venv dédié tools/vela-venv, interpréteur courant (pip install ethos-u-vela)."""
    env = os.environ.get("E7TWIN_VELA_PYTHON")
    if env and Path(env).exists():
        return env
    for rel in ("Scripts/python.exe", "bin/python"):
        cand = REPO_ROOT / "tools" / "vela-venv" / rel
        if cand.exists():
            return str(cand)
    try:
        # find_spec("ethosu.vela") importe le paquet parent : sans `ethosu` installé, il lève
        # ModuleNotFoundError au lieu de rendre None (vu sous macOS, Python 3.14).
        if importlib.util.find_spec("ethosu.vela") is not None:
            return sys.executable
    except (ModuleNotFoundError, ValueError):
        pass
    return None


def _neutral_cwd() -> str:
    # Ne jamais lancer un interpréteur depuis un dossier contenant des fichiers non fiables (modèles, sorties).
    return tempfile.gettempdir()


def _run_py(python: str, args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [python, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=_neutral_cwd(),
    )


def vela_version(python: str) -> str | None:
    try:
        cp = _run_py(python, ["-I", "-c", "from importlib.metadata import version; print(version('ethos-u-vela'))"], 60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return cp.stdout.strip() or None if cp.returncode == 0 else None


# Lit le .tflite produit par Vela avec le lecteur flatbuffers de Vela lui-même : les opérateurs encore
# « builtin » après compilation sont ceux qui n'ont pas été offloadés (le reste est un seul 'ethos-u' custom).
_OPS_SCRIPT = r"""
import sys, json
from ethosu.vela.tflite.Model import Model
from ethosu.vela.tflite.BuiltinOperator import BuiltinOperator
names = {v: k for k, v in vars(BuiltinOperator).items() if isinstance(v, int)}
m = Model.GetRootAsModel(open(sys.argv[1], 'rb').read(), 0)
codes = []
for i in range(m.OperatorCodesLength()):
    c = m.OperatorCodes(i)
    bc = max(c.BuiltinCode(), c.DeprecatedBuiltinCode())
    cc = c.CustomCode()
    codes.append((names.get(bc, str(bc)), cc.decode() if cc else None))
ops = {}
for s in range(m.SubgraphsLength()):
    sg = m.Subgraphs(s)
    for j in range(sg.OperatorsLength()):
        b, c = codes[sg.Operators(j).OpcodeIndex()]
        k = c if b == 'CUSTOM' else b
        ops[k] = ops.get(k, 0) + 1
print(json.dumps(ops))
"""


def list_operators(python: str, tflite: Path) -> dict[str, int]:
    cp = _run_py(python, ["-I", "-c", _OPS_SCRIPT, str(tflite)], 120)
    if cp.returncode != 0:
        raise NpuError(f"lecture des opérateurs impossible : {cp.stderr.strip()[-300:]}")
    return json.loads(cp.stdout.strip().splitlines()[-1])


# ----------------------------------------------------------------------------- parsing (sans vela)

_RE_CPU = re.compile(r"CPU operators\s*=\s*(\d+)")
_RE_NPU = re.compile(r"NPU operators\s*=\s*(\d+)")
_RE_WARN = re.compile(r"^(Warning|Unsupported|Info):.*$", re.M)
_RE_DYN = re.compile(r"has a dynamic signature in axis")


def parse_vela_output(stdout: str, summary_csv: str, ops_after: dict[str, int] | None = None) -> dict:
    """Extrait les mesures d'une exécution de Vela : sortie standard + CSV 'summary'.

    Pur (aucun accès à vela) : testable sur un échantillon enregistré. Les unités du CSV : `*_memory_used`
    en KiB, `*_weights` en octets, `inference_time` en secondes, `core_clock` en Hz."""
    rows = list(csv.DictReader(io.StringIO(summary_csv.strip())))
    if not rows:
        raise NpuError("CSV de synthèse Vela vide")
    r = rows[-1]

    def f(key: str) -> float:
        try:
            return float(r[key])
        except (KeyError, ValueError):
            raise NpuError(f"colonne '{key}' absente ou invalide dans le CSV Vela") from None

    sram_kib = f("sram_memory_used")
    flash_kib = max(f("on_chip_flash_memory_used"), f("off_chip_flash_memory_used"))
    m_cpu, m_npu = _RE_CPU.search(stdout), _RE_NPU.search(stdout)
    cpu_types = {k: v for k, v in (ops_after or {}).items() if k != "ethos-u"}
    warnings = [w.group(0).strip() for w in _RE_WARN.finditer(stdout)
                if "No configuration file" not in w.group(0) and "No system configuration" not in w.group(0)
                and "No memory mode" not in w.group(0)]
    return {
        "clock_hz": int(f("core_clock")),
        "cycles_total": int(f("cycles_total")),
        "cycles_npu": int(f("cycles_npu")),
        "inference_time_s": f("inference_time"),
        "inferences_per_s": f("inferences_per_second"),
        "macs": int(f("nn_macs")),
        "sram_bytes": int(round(sram_kib * 1024)),
        "weights_bytes": int(f("total_npu_encoded_weights")),
        "weights_original_bytes": int(f("total_original_weights")),
        "weights_flash_bytes": int(round(flash_kib * 1024)),
        "npu_operators": int(m_npu.group(1)) if m_npu else 0,
        "cpu_operators": int(m_cpu.group(1)) if m_cpu else 0,
        "cpu_op_types": cpu_types,
        "vela_warnings": warnings,
    }


# ----------------------------------------------------------------------------- exécution


def _resolve_model(model: str | Path) -> Path:
    p = Path(model)
    if not p.is_absolute() and not p.exists() and (REPO_ROOT / p).exists():
        p = REPO_ROOT / p
    if not p.is_file():
        raise NpuError(f"modèle introuvable : {model}")
    return p.resolve()


def _run_one(python: str, model: Path, key: str, brd: board_mod.Board, weights: str, work: Path) -> NpuResult:
    acc, core = NPUS[key]
    res = NpuResult(npu=key, accelerator=acc, core=core, weights_region=weights,
                    clock_hz=_clock_hz(brd, core), sram_budget_bytes=sram_budget_bytes(brd, core))
    ini = work / "vela_e7.ini"
    ini.write_text(vela_ini(brd, weights), encoding="utf-8")
    outd = work / key
    outd.mkdir(parents=True, exist_ok=True)
    args = ["-I", "-m", "ethosu.vela", str(model), "--accelerator-config", acc,
            "--config", str(ini), "--system-config", _system_config_name(key, weights),
            "--memory-mode", MEMORY_MODE, "--optimise", "Performance", "--output-dir", str(outd)]
    try:
        cp = _run_py(python, args)
    except subprocess.TimeoutExpired:
        res.error = "vela : délai dépassé"
        return res
    if cp.returncode != 0:
        tail = (cp.stdout + cp.stderr).strip()[-400:]
        res.error = f"vela a échoué (code {cp.returncode}) : {tail}"
        return res
    csvs = sorted(outd.glob("*_summary_*.csv"))
    if not csvs:
        res.error = "vela n'a produit aucun CSV de synthèse"
        return res
    try:
        optimised = next(outd.glob("*_vela.tflite"), None)
        ops = list_operators(python, optimised) if optimised else None
        data = parse_vela_output(cp.stdout, csvs[0].read_text(encoding="utf-8"), ops)
    except NpuError as e:
        res.error = str(e)
        return res
    data.pop("weights_flash_bytes", None)
    for k, v in data.items():
        setattr(res, k, v)
    res.fits = res.sram_bytes <= res.sram_budget_bytes
    res.ok = True
    return res


def run(model: str | Path, npu: str | None = None, weights: str = "mram",
        brd: board_mod.Board | None = None) -> NpuReport:
    """Estime `model` sur un ou les deux NPU (`npu` ∈ {'u55_256', 'u55_128', None}). Lève NpuError."""
    if npu is not None and npu not in NPUS:
        raise NpuError(f"NPU inconnu : {npu!r} (attendu : {', '.join(NPUS)})")
    path = _resolve_model(model)
    python = vela_python()
    if python is None:
        raise NpuError("vela introuvable : `py -3.14 -m pip install ethos-u-vela` "
                       "(ou venv dédié tools/vela-venv, ou variable E7TWIN_VELA_PYTHON)")
    brd = brd or board_mod.load()
    data = path.read_bytes()
    report = NpuReport(model=path.name, model_bytes=len(data), model_sha256=hashlib.sha256(data).hexdigest(),
                       results={}, vela_version=vela_version(python), weights_region=weights)
    with tempfile.TemporaryDirectory(prefix="e7twin-vela-") as tmp:
        for key in ([npu] if npu else list(NPUS)):
            report.results[key] = _run_one(python, path, key, brd, weights, Path(tmp))
    return report


# ----------------------------------------------------------------------------- CLI


def _kib(n: int) -> str:
    return f"{n / 1024:.1f} KiB"


def format_table(report: NpuReport) -> str:
    keys = list(report.results)
    rows: list[tuple[str, list[str]]] = []

    def add(label: str, fn) -> None:
        rows.append((label, [fn(report.results[k]) if report.results[k].ok else "-" for k in keys]))

    add("Coeur associe", lambda r: f"{r.core} @ {r.clock_hz / 1e6:.0f} MHz")
    add("Cycles NPU (total)", lambda r: f"{r.cycles_total:,}".replace(",", " "))
    add("Cycles NPU (calcul)", lambda r: f"{r.cycles_npu:,}".replace(",", " "))
    add("Temps estime", lambda r: f"{r.inference_time_s * 1e3:.3f} ms ({r.inferences_per_s:.0f} inf/s)")
    add("MACs", lambda r: f"{r.macs:,}".replace(",", " "))
    add("Arena SRAM (pic)", lambda r: f"{_kib(r.sram_bytes)} / {_kib(r.sram_budget_bytes)} "
                                      f"[{'OK' if r.fits else 'TROP GRAND'}]")
    add(f"Poids ({report.weights_region.upper()})", lambda r: _kib(r.weights_bytes))
    add("Ops sur NPU / CPU", lambda r: f"{r.npu_operators} / {r.cpu_operators}")
    add("Ops hors NPU", lambda r: ", ".join(f"{k} x{v}" for k, v in r.cpu_op_types.items()) or "aucun")
    w0 = max(len(lbl) for lbl, _ in rows)
    widths = [max([len(k)] + [len(v[i]) for _, v in rows]) for i, k in enumerate(keys)]
    lines = [f"Modele : {report.model} ({_kib(report.model_bytes)}), vela {report.vela_version or '?'}",
             "  ".join([" " * w0] + [k.ljust(w) for k, w in zip(keys, widths)])]
    for lbl, vals in rows:
        lines.append("  ".join([lbl.ljust(w0)] + [v.ljust(w) for v, w in zip(vals, widths)]))
    for k in keys:
        if report.results[k].error:
            lines.append(f"ERREUR {k} : {report.results[k].error}")
    lines.append("ATTENTION : " + report.fidelity)
    return "\n".join(lines)


def main(model: str, npu: str | None = None, out_dir: str = "reports") -> int:
    """Point d'entrée de `e7twin npu`. 0 : tient ; 1 : l'arena dépasse la SRAM d'un cœur ; 2 : erreur."""
    for stream in (sys.stdout, sys.stderr):  # console Windows cp1252/cp850 : ne jamais planter sur un accent
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        report = run(model, npu)
    except NpuError as e:
        print(f"e7twin npu : {e}", file=sys.stderr)
        return 2
    print(format_table(report))
    if not report.ok:
        return 2
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{Path(report.model).stem}.npu.json"
    target.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(f"Rapport : {target}")
    if not report.fits:
        print("ECHEC : l'arena depasse la SRAM disponible pour au moins un coeur.", file=sys.stderr)
        return 1
    return 0
