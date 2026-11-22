"""Localisation et diagnostic de la chaîne de compilation / simulation E7 Twin (agent 02).

Ordre de recherche de chaque outil : d'abord ``tools/`` (installé par ``scripts/install_tools.ps1`` ou ``.sh``,
versions épinglées), puis le ``PATH``. Le dossier ``tools/`` peut être déplacé avec la variable
d'environnement ``E7TWIN_TOOLS_DIR``.

API publique :

- :func:`find` — chemin d'un exécutable (``arm-none-eabi-gcc``, ``cbuild``, ``renode``…) ou ``None`` ;
- :func:`renode_binary` — chemin du binaire Renode (tous OS) ;
- :func:`env` — variables d'environnement à passer à ``cbuild`` / ``cpackget`` / CMake ;
- :func:`doctor` — liste de dicts ``{name, found, path, version, hint, required}`` (contrat
  ``docs/INTERFACES.md``), consommée par ``e7twin doctor``.

Aucune adresse matérielle ici : ce module ne connaît que des outils et des packs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------------------------
# Versions épinglées (doivent rester alignées avec scripts/install_tools.ps1 et docs/BUILD.md)
# ---------------------------------------------------------------------------------------------
GCC_VERSION = "14.3.rel1"
CMSIS_TOOLBOX_VERSION = "2.12.0"
RENODE_VERSION = "1.17.0"

#: Packs requis par alif_vscode-template (alif.csolution.yml + alif.cbuild-pack.yml).
REQUIRED_PACKS: tuple[tuple[str, str, str], ...] = (
    ("AlifSemiconductor", "Ensemble", "2.2.1"),
    ("ARM", "CMSIS", "6.1.0"),
    ("ARM", "CMSIS-Compiler", "2.1.0"),
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_macos() -> bool:
    return sys.platform == "darwin"


def install_hint() -> str:
    """Commande d'installation des outils épinglés, selon l'OS courant."""
    if _is_windows():
        return "lancer : powershell -ExecutionPolicy Bypass -File scripts\\install_tools.ps1"
    return "lancer : bash scripts/install_tools.sh"


def license_hint() -> str:
    script = ("scripts\\install_tools.ps1 -AcceptPackLicenses" if _is_windows()
              else "scripts/install_tools.sh --accept-pack-licenses")
    return f"archive téléchargée mais licence du pack non acceptée : la lire puis relancer {script}"


# Valeurs calculées à l'import (compatibilité : package.py lit ``toolchain.INSTALL_HINT``).
INSTALL_HINT = install_hint()
LICENSE_HINT = license_hint()

#: Emplacements de Renode sous macOS hors PATH. Nom du binaire dans le bundle : à confirmer sur un Mac
#: (je ne l'ai pas vu documenté localement ; ``Renode.app/Contents/MacOS/renode`` est l'hypothèse).
MACOS_RENODE_CANDIDATES: tuple[str, ...] = (
    "/Applications/Renode.app/Contents/MacOS/renode",
    "/Applications/Renode.app/Contents/MacOS/Renode",  # à confirmer
)

# Sous-dossiers de tools/ (motifs glob, le plus récent gagne) et nom du binaire relatif.
_TOOL_LAYOUT: dict[str, tuple[str, str]] = {
    "arm-none-eabi-gcc": ("arm-gnu-toolchain-*", "bin/arm-none-eabi-gcc"),
    "arm-none-eabi-objcopy": ("arm-gnu-toolchain-*", "bin/arm-none-eabi-objcopy"),
    "arm-none-eabi-objdump": ("arm-gnu-toolchain-*", "bin/arm-none-eabi-objdump"),
    "arm-none-eabi-readelf": ("arm-gnu-toolchain-*", "bin/arm-none-eabi-readelf"),
    "arm-none-eabi-size": ("arm-gnu-toolchain-*", "bin/arm-none-eabi-size"),
    "cbuild": ("cmsis-toolbox-*", "bin/cbuild"),
    "csolution": ("cmsis-toolbox-*", "bin/csolution"),
    "cpackget": ("cmsis-toolbox-*", "bin/cpackget"),
    "renode": ("renode-*", "renode"),  # + variantes macOS : voir _find_in_tools
    "dotnet": ("dotnet*", "dotnet"),
}


# ---------------------------------------------------------------------------------------------
# Points d'injection (remplacés dans les tests pour ne dépendre d'aucun outil réel)
# ---------------------------------------------------------------------------------------------
def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd: list[str], timeout: float = 30.0) -> str | None:
    """Exécute ``cmd`` et renvoie stdout+stderr, ou ``None`` si l'exécution échoue."""
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (cp.stdout or "") + (cp.stderr or "")


# ---------------------------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------------------------
def tools_dir() -> Path:
    """Dossier des outils locaux (``E7TWIN_TOOLS_DIR`` ou ``<dépôt>/tools``)."""
    override = os.environ.get("E7TWIN_TOOLS_DIR")
    return Path(override) if override else REPO_ROOT / "tools"


def _exe(rel: str) -> str:
    return rel + ".exe" if _is_windows() else rel


def _version_key(p: Path) -> tuple:
    """Clé de tri « naturelle » pour préférer la version la plus récente d'un dossier."""
    return tuple(int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p.name))


def _find_in_tools(name: str, tdir: Path) -> Path | None:
    layout = _TOOL_LAYOUT.get(name)
    if layout is None or not tdir.is_dir():
        return None
    pattern, rel = layout
    for d in sorted(tdir.glob(pattern), key=_version_key, reverse=True):
        rels = [rel] + (["Renode.app/Contents/MacOS/renode"] if name == "renode" and _is_macos() else [])
        for r in rels:
            cand = d / _exe(r)
            if cand.is_file():
                return cand
    return None


def find(name: str, tdir: Path | None = None) -> Path | None:
    """Chemin de l'exécutable ``name`` : ``tools/`` d'abord, puis le ``PATH``."""
    tdir = tools_dir() if tdir is None else tdir
    local = _find_in_tools(name, tdir)
    if local is not None:
        return local
    on_path = _which(name)
    if on_path:
        return Path(on_path)
    if name == "renode" and _is_macos():
        for c in MACOS_RENODE_CANDIDATES:
            if Path(c).is_file():
                return Path(c)
    return None


def renode_binary(tdir: Path | None = None) -> Path | None:
    """Chemin du binaire Renode, ou ``None``. À utiliser par ``sim.py`` à la place de ``find("renode")``.

    Ordre : ``$E7TWIN_RENODE`` (fichier existant), ``tools/`` (version épinglée), ``renode`` dans le
    ``PATH`` (Windows, macOS, Linux), puis sous macOS ``/Applications/Renode.app/Contents/MacOS/``.
    """
    override = os.environ.get("E7TWIN_RENODE")
    if override and Path(override).is_file():
        return Path(override)
    return find("renode", tdir)


def _from_tools(path: Path | None, tdir: Path) -> bool:
    if path is None:
        return False
    try:
        path.resolve().relative_to(tdir.resolve())
        return True
    except (ValueError, OSError):
        return False


def _default_pack_root() -> Path:
    """Emplacement par défaut de CMSIS-Toolbox quand CMSIS_PACK_ROOT n'est pas défini."""
    if _is_windows():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Arm" / "Packs"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "arm" / "packs"


def pack_root(tdir: Path | None = None) -> Path:
    """Pack root CMSIS : ``tools/packs`` s'il est initialisé, sinon ``$CMSIS_PACK_ROOT``, sinon défaut."""
    tdir = tools_dir() if tdir is None else tdir
    local = tdir / "packs"
    if (local / "pack.idx").exists() or (local / ".Web").is_dir():
        return local
    env_root = os.environ.get("CMSIS_PACK_ROOT")
    return Path(env_root) if env_root else _default_pack_root()


def find_pack(vendor: str, name: str, version: str, root: Path | None = None) -> Path | None:
    """Dossier du pack installé (contenant son ``.pdsc``) ou ``None``."""
    root = pack_root() if root is None else root
    d = root / vendor / name / version
    if d.is_dir() and any(d.glob("*.pdsc")):
        return d
    return None


def _pack_downloaded(vendor: str, name: str, version: str, root: Path) -> bool:
    return (root / ".Download" / f"{vendor}.{name}.{version}.pack").is_file()


# ---------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------
_RE_SEMVER = re.compile(r"(\d+\.\d+\.\d+)")


def gcc_version(gcc: Path | None = None) -> str | None:
    """Version complète de GCC, ex. ``14.3.1`` (``-dumpfullversion``), ou déduite du dossier."""
    gcc = find("arm-none-eabi-gcc") if gcc is None else gcc
    if gcc is None:
        return None
    out = _run([str(gcc), "-dumpfullversion"])
    if out:
        m = _RE_SEMVER.search(out)
        if m:
            return m.group(1)
    # Repli : arm-gnu-toolchain-14.3.rel1 -> 14.3.1
    m = re.search(r"(\d+)\.(\d+)\.rel(\d+)", str(gcc))
    return ".".join(m.groups()) if m else None


def _tool_version(path: Path, args: list[str], pattern: re.Pattern[str] = _RE_SEMVER) -> str | None:
    out = _run([str(path), *args])
    if not out:
        return None
    m = pattern.search(out)
    return m.group(1) if m else None


def _renode_bundled_runtime(renode: Path | None) -> str | None:
    """Version de Microsoft.NETCore.App embarquée par un Renode portable (self-contained)."""
    if renode is None:
        return None
    rc = renode.parent / "Renode.runtimeconfig.json"
    if not rc.is_file():
        return None
    try:
        data = json.loads(rc.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for fw in data.get("runtimeOptions", {}).get("includedFrameworks", []) or []:
        if fw.get("name") == "Microsoft.NETCore.App":
            return fw.get("version")
    return None


# ---------------------------------------------------------------------------------------------
# Environnement pour cbuild / cpackget / CMake
# ---------------------------------------------------------------------------------------------
def env(base: dict[str, str] | None = None, tdir: Path | None = None) -> dict[str, str]:
    """Environnement complet (copie de ``base`` ou ``os.environ``) prêt pour ``cbuild``.

    - ``PATH`` : bin CMSIS-Toolbox, bin GCC et dossier Renode de ``tools/`` en tête ;
    - ``CMSIS_PACK_ROOT`` : voir :func:`pack_root` ;
    - ``CMSIS_COMPILER_ROOT`` : ``<cmsis-toolbox>/etc`` ;
    - ``GCC_TOOLCHAIN_<maj>_<min>_<patch>`` : bin GCC (enregistrement du compilateur dans CMSIS-Toolbox).
    """
    tdir = tools_dir() if tdir is None else tdir
    out = dict(os.environ if base is None else base)
    prepend: list[str] = []

    cbuild = find("cbuild", tdir)
    if cbuild is not None:
        prepend.append(str(cbuild.parent))
        etc = cbuild.parent.parent / "etc"
        if etc.is_dir():
            out["CMSIS_COMPILER_ROOT"] = str(etc)

    gcc = find("arm-none-eabi-gcc", tdir)
    if gcc is not None:
        prepend.append(str(gcc.parent))
        ver = gcc_version(gcc)
        if ver:
            out["GCC_TOOLCHAIN_" + ver.replace(".", "_")] = str(gcc.parent)

    renode = renode_binary(tdir)
    if renode is not None:
        prepend.append(str(renode.parent))

    out["CMSIS_PACK_ROOT"] = str(pack_root(tdir))

    path_key = next((k for k in out if k.upper() == "PATH"), "PATH")
    existing = out.get(path_key, "")
    seen: set[str] = set()
    parts = []
    for p in prepend + ([existing] if existing else []):
        if p and p not in seen:
            seen.add(p)
            parts.append(p)
    out[path_key] = os.pathsep.join(parts)
    return out


# ---------------------------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------------------------
def _entry(name: str, path: Path | None, version: str | None, hint: str, required: bool = True) -> dict:
    return {
        "name": name,
        "found": path is not None,
        "path": str(path) if path is not None else None,
        "version": version,
        "hint": hint,
        "required": required,
    }


def doctor(tdir: Path | None = None) -> list[dict]:
    """Diagnostic des outils. Ne lève jamais d'exception pour un outil absent.

    Chaque élément : ``{name, found, path, version, hint, required}``. ``hint`` explique quoi faire
    si l'outil manque (ou d'où il vient s'il est trouvé).
    """
    tdir = tools_dir() if tdir is None else tdir
    results: list[dict] = []

    def where(p: Path | None) -> str:
        return "tools/ (version épinglée)" if _from_tools(p, tdir) else "PATH (version non épinglée)"

    # --- Arm GNU Toolchain
    gcc = find("arm-none-eabi-gcc", tdir)
    results.append(
        _entry(
            "arm-none-eabi-gcc",
            gcc,
            gcc_version(gcc) if gcc else None,
            where(gcc) if gcc else f"Arm GNU Toolchain {GCC_VERSION} absente — {install_hint()}",
        )
    )

    # --- binutils (objcopy : package ; objdump : check/pile ; size, readelf : rapports)
    for tool in ("arm-none-eabi-objcopy", "arm-none-eabi-objdump", "arm-none-eabi-size", "arm-none-eabi-readelf"):
        p = find(tool, tdir)
        results.append(
            _entry(
                tool,
                p,
                gcc_version(gcc) if (p and gcc) else None,
                where(p) if p else f"binutils de l'Arm GNU Toolchain {GCC_VERSION} absents — {install_hint()}",
            )
        )

    # --- CMSIS-Toolbox
    for tool in ("cbuild", "csolution", "cpackget"):
        p = find(tool, tdir)
        results.append(
            _entry(
                tool,
                p,
                _tool_version(p, ["--version"]) if p else None,
                where(p) if p else f"CMSIS-Toolbox {CMSIS_TOOLBOX_VERSION} absent — {install_hint()}",
            )
        )

    # --- CMake / Ninja (requis par cbuild, générateur Ninja)
    for tool, hint in (
        ("cmake", "CMake >= 3.27 requis par cbuild : l'ajouter au PATH"),
        ("ninja", "Ninja requis par cbuild (générateur par défaut) : l'ajouter au PATH"),
    ):
        p = find(tool, tdir)
        results.append(_entry(tool, p, _tool_version(p, ["--version"]) if p else None, where(p) if p else hint))

    # --- Packs CMSIS
    root = pack_root(tdir)
    for vendor, name, version in REQUIRED_PACKS:
        p = find_pack(vendor, name, version, root)
        if p is not None:
            hint = f"pack root : {root}"
        elif _pack_downloaded(vendor, name, version, root):
            hint = license_hint()
        else:
            hint = f"pack absent de {root} — {install_hint()}"
        results.append(_entry(f"pack {vendor}::{name}", p, version if p else None, hint))

    # --- Renode
    renode = renode_binary(tdir)
    rver = None
    if renode is not None:
        rver = _tool_version(renode, ["--version"], re.compile(r"Renode v?(\d+\.\d+\.\d+)"), )
        if rver is None:
            m = re.search(r"renode[-_](\d+\.\d+\.\d+)", str(renode))
            rver = m.group(1) if m else None
    results.append(
        _entry(
            "renode",
            renode,
            rver,
            where(renode) if renode else f"Renode {RENODE_VERSION} portable absent — {install_hint()}",
        )
    )

    # --- .NET : le Renode portable Windows est self-contained (runtime embarqué) ; un dotnet
    #     système n'est utile que pour un Renode « framework-dependent » (ex. renode-*.linux.tar.gz).
    bundled = _renode_bundled_runtime(renode)
    if bundled:
        results.append(
            _entry(
                ".NET (runtime Renode)",
                renode.parent if renode else None,
                bundled,
                "embarqué dans Renode portable (self-contained) : aucun .NET système requis",
                required=False,
            )
        )
    else:
        dotnet = find("dotnet", tdir)
        dver = None
        if dotnet is not None:
            out = _run([str(dotnet), "--list-runtimes"]) or ""
            vers = re.findall(r"Microsoft\.NETCore\.App (\d+\.\d+\.\d+)", out)
            dver = max(vers, key=lambda v: tuple(int(x) for x in v.split("."))) if vers else None
        results.append(
            _entry(
                ".NET (runtime Renode)",
                dotnet,
                dver,
                where(dotnet)
                if dotnet
                else "aucun runtime .NET trouvé : utiliser le Renode portable (runtime embarqué) — " + install_hint(),
                required=False,
            )
        )

    return results


def missing_required(results: list[dict] | None = None) -> list[str]:
    """Noms des outils requis absents (pratique pour build.py)."""
    results = doctor() if results is None else results
    return [r["name"] for r in results if r.get("required", True) and not r.get("found")]


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - utilitaire pour les scripts
    """``python -m e7twin.toolchain [--json | --env-json]``.

    ``--env-json`` : ``{"env": {PATH, CMSIS_*, GCC_TOOLCHAIN_*}, "missing": [...]}`` — utilisé par
    ``scripts/build_spike.ps1`` (évite de passer du code Python en argument depuis PowerShell 5.1).
    """
    import argparse

    ap = argparse.ArgumentParser(prog="python -m e7twin.toolchain")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true", help="doctor() en JSON")
    g.add_argument("--env-json", action="store_true", help="variables d'environnement de build en JSON")
    args = ap.parse_args(argv)
    results = doctor()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.env_json:
        e = env()
        keep = {k: v for k, v in e.items() if k.upper() == "PATH" or k.startswith(("CMSIS_", "GCC_TOOLCHAIN_"))}
        print(json.dumps({"env": keep, "missing": missing_required(results)}, ensure_ascii=False))
    else:
        for r in results:
            state = "OK " if r["found"] else ("-- " if not r["required"] else "KO ")
            print(f"{state} {r['name']:<34} {r['version'] or '-':<10} {r['path'] or r['hint']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
