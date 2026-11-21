"""Point d'entrée CLI d'E7 Twin (typer).

La CLI ne contient aucune logique métier : chaque commande importe son module
**paresseusement** (board, check, sim, server, package, npu, toolchain) et lui
délègue le travail. Ainsi `e7twin --help` fonctionne même si un module manque
ou est cassé pendant le développement en parallèle des agents.

Codes de sortie (voir docs/INTERFACES.md § CLI) :
  0  succès
  1  échec métier (check FAIL, texte attendu absent, outil requis manquant…)
  2  non implémenté / module absent (et erreurs d'usage, côté typer/click)
"""

from __future__ import annotations

import importlib
import inspect
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Annotated, Optional

import typer

from e7twin import __version__

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NOT_IMPLEMENTED = 2

app = typer.Typer(
    name="e7twin",
    help="E7 Twin : compiler, vérifier, simuler et monitorer pour l'Alif Ensemble E7 AppKit.",
    no_args_is_help=True,
    add_completion=False,
)


class CoreId(str, Enum):
    """Cœurs exécutables ciblés par les commandes (les A32 sont inertes en v1)."""

    m55_hp = "m55_hp"
    m55_he = "m55_he"


class NpuTarget(str, Enum):
    u55_256 = "u55_256"
    u55_128 = "u55_128"
    both = "both"


# --------------------------------------------------------------------------- utilitaires


def _err(msg: str) -> None:
    typer.echo(msg, err=True)


def _import_module(name: str) -> tuple[ModuleType | None, str]:
    """Importe `e7twin.<name>` sans jamais faire planter la CLI.

    Renvoie (module, "") ou (None, raison lisible).
    """
    full = f"e7twin.{name}"
    try:
        return importlib.import_module(full), ""
    except ModuleNotFoundError as exc:
        if exc.name == full:
            return None, f"module {full} absent"
        return None, f"module {full} : dépendance manquante ({exc.name})"
    except Exception as exc:  # module présent mais cassé : on le dit, sans trace
        return None, f"module {full} : erreur à l'import ({type(exc).__name__}: {exc})"


def _accepted_kwargs(command: str, fn, kwargs: dict) -> dict:
    """Ne garde que les mots-clés acceptés par `fn` (introspection).

    Permet de préparer des options (ex. `serve --hp`) avant que le module les gère : un paramètre
    inconnu valant None est ignoré sans bruit ; une valeur réelle ignorée est signalée sur stderr.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    kept = {}
    for k, v in kwargs.items():
        if k in params:
            kept[k] = v
        elif v is not None and v is not False:
            _err(f"e7twin {command} : option --{k.replace('_', '-')} pas encore gérée par ce module, ignorée.")
    return kept


def _delegate(command: str, module: str, *args, **kwargs) -> None:
    """Appelle `e7twin.<module>.main(*args, **kwargs)` et sort avec son code de retour.

    Si le module ou sa fonction `main` n'existe pas encore : message propre, code 2.
    """
    mod, why = _import_module(module)
    main_fn = getattr(mod, "main", None) if mod is not None else None
    if main_fn is None:
        state = why if mod is None else f"e7twin.{module}.main() absent"
        _err(f"e7twin {command} : non implémenté ({state}).")
        raise typer.Exit(EXIT_NOT_IMPLEMENTED)
    kwargs = _accepted_kwargs(command, main_fn, kwargs)
    code = main_fn(*args, **kwargs)
    raise typer.Exit(int(code or 0))


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"e7twin {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Affiche la version."),
    ] = None,
) -> None:
    """E7 Twin : compiler, vérifier, simuler et monitorer pour l'Alif Ensemble E7 AppKit."""


# --------------------------------------------------------------------------- commandes


@app.command()
def build(
    project: Annotated[Path, typer.Argument(help="Projet .csolution.yml ou dossier CMake.")],
    core: Annotated[CoreId, typer.Option("--core", help="Cœur cible.")] = CoreId.m55_hp,
    build_type: Annotated[str, typer.Option("--build-type", help="Type de build (debug|release).")] = "debug",
    out_dir: Annotated[str, typer.Option("--out-dir", help="Dossier de sortie.")] = "build",
) -> None:
    """Compile pour la vraie cible (cbuild/CMake, flags Cortex-M55 imposés)."""
    _delegate("build", "build", str(project), core.value, build_type, out_dir)


@app.command()
def check(
    target: Annotated[Path, typer.Argument(help="ELF ou dossier de projet à vérifier.")],
    core: Annotated[CoreId, typer.Option("--core", help="Cœur visé par l'image.")] = CoreId.m55_hp,
    atoc: Annotated[
        Optional[Path], typer.Option("--atoc", help="JSON ATOC à vérifier avec l'image.")
    ] = None,
    su_dir: Annotated[
        Optional[Path], typer.Option("--su-dir", help="Dossier des .su (-fstack-usage) pour la règle pile.")
    ] = None,
    npu_report: Annotated[
        Optional[Path], typer.Option("--npu-report", help="Rapport reports/<modèle>.npu.json : arena NPU vs SRAM.")
    ] = None,
) -> None:
    """Barrière de compatibilité : verdict PASS/WARN/FAIL (code 1 si FAIL, 2 si erreur)."""
    _delegate("check", "check", str(target), core.value, str(atoc) if atoc else None,
              su_dir=str(su_dir) if su_dir else None,
              npu_report=str(npu_report) if npu_report else None)


@app.command()
def run(
    elfs: Annotated[
        Optional[list[Path]], typer.Argument(help="ELF à charger (cœur déduit de l'adresse).")
    ] = None,
    hp: Annotated[Optional[Path], typer.Option("--hp", help="ELF pour le M55-HP.")] = None,
    he: Annotated[Optional[Path], typer.Option("--he", help="ELF pour le M55-HE.")] = None,
    headless: Annotated[bool, typer.Option("--headless", help="Sans dashboard (CI).")] = False,
    expect: Annotated[
        Optional[str], typer.Option("--expect", help="Texte attendu sur l'UART (code 1 si absent).")
    ] = None,
    atoc: Annotated[
        Optional[Path], typer.Option("--atoc", help="JSON ATOC : images, adresses et cœurs à démarrer.")
    ] = None,
    timeout: Annotated[float, typer.Option("--timeout", help="Délai max en secondes (temps réel).")] = 30.0,
) -> None:
    """Exécute les vrais binaires sur une E7 émulée (Renode)."""
    _delegate(
        "run", "sim",
        [str(p) for p in (elfs or [])],
        str(hp) if hp else None,
        str(he) if he else None,
        str(atoc) if atoc else None,
        headless,
        expect,
        timeout,
    )


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", help="Port HTTP du dashboard.")] = 8000,
    host: Annotated[str, typer.Option("--host", help="Interface d'écoute.")] = "127.0.0.1",
    fake: Annotated[bool, typer.Option("--fake", help="Simulation factice (sans Renode).")] = False,
    hp: Annotated[Optional[Path], typer.Option("--hp", help="ELF M55-HP chargé au démarrage.")] = None,
    he: Annotated[Optional[Path], typer.Option("--he", help="ELF M55-HE chargé au démarrage.")] = None,
) -> None:
    """Dashboard web local + API REST/WebSocket."""
    _delegate("serve", "server", host=host, port=port, fake=fake,
              hp=str(hp) if hp else None, he=str(he) if he else None)


@app.command()
def package(
    elf: Annotated[Path, typer.Argument(help="ELF à empaqueter.")],
    core: Annotated[CoreId, typer.Option("--core", help="Cœur cible.")] = CoreId.m55_hp,
    out_dir: Annotated[str, typer.Option("--out-dir", help="Dossier de sortie.")] = "build/package",
    with_: Annotated[
        Optional[list[Path]],
        typer.Option("--with", help="ELF d'un autre cœur à combiner dans le même JSON ATOC (répétable)."),
    ] = None,
) -> None:
    """Produit .bin + JSON ATOC et AFFICHE la commande SETOOLS (jamais de flash)."""
    _delegate("package", "package", str(elf), core.value, out_dir,
              [str(p) for p in with_] if with_ else None)


@app.command()
def npu(
    model: Annotated[Path, typer.Argument(help="Modèle .tflite.")],
    target: Annotated[NpuTarget, typer.Option("--npu", help="Ethos-U55 visé.")] = NpuTarget.both,
    out_dir: Annotated[str, typer.Option("--out-dir", help="Dossier des rapports.")] = "reports",
) -> None:
    """Estimation Vela sur les Ethos-U55 (256 et 128 MAC)."""
    _delegate("npu", "npu", str(model), None if target is NpuTarget.both else target.value, out_dir)


@app.command()
def doctor() -> None:
    """Diagnostic des outils (toolchain, CMSIS-Toolbox, pack Alif, Renode)."""
    toolchain, why = _import_module("toolchain")
    if toolchain is None:
        _err(f"e7twin doctor : indisponible ({why}).")
        raise typer.Exit(EXIT_NOT_IMPLEMENTED)
    if not hasattr(toolchain, "doctor"):
        _err("e7twin doctor : indisponible (e7twin.toolchain.doctor() absent).")
        raise typer.Exit(EXIT_NOT_IMPLEMENTED)

    tools = list(toolchain.doctor())
    typer.echo(format_doctor_table(tools))
    missing = [t for t in tools if t.get("required", True) and not t.get("found")]
    if missing:
        _err("Outil(s) requis manquant(s) : " + ", ".join(str(t.get("name")) for t in missing))
        raise typer.Exit(EXIT_FAIL)


def format_doctor_table(tools: list[dict]) -> str:
    """Tableau texte (ASCII) des résultats de toolchain.doctor()."""
    headers = ["Outil", "État", "Version", "Chemin / indice"]
    rows = []
    for t in tools:
        found = bool(t.get("found"))
        state = "OK" if found else ("MANQUANT" if t.get("required", True) else "absent (optionnel)")
        detail = t.get("path") if found else t.get("hint")
        rows.append([str(t.get("name", "?")), state, str(t.get("version") or "-"), str(detail or "-")])
    widths = [max(len(r[i]) for r in [headers, *rows]) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    out = [line(headers), line(["-" * w for w in widths])]
    out += [line(r) for r in rows]
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover
    app()
