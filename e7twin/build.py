"""``e7twin build`` : compile pour la vraie cible avec la chaîne épinglée (agent 02).

Deux cas :

* **csolution** (``*.csolution.yml``, ou dossier qui en contient un, ou dossier d'un ``*.cproject.yml``) :
  ``cbuild <sol> --context <projet>.<build>+<target-type>``. Le target-type est lu dans le csolution
  (``target-types[].device`` qui se termine par ``:M55_HP`` / ``:M55_HE`` et contient le device du
  board, ``board.info['cmsis_device']``). Un dossier de sortie **par cœur** (le cache CMake de
  ``tmp/`` est lié à un contexte). Pour plusieurs projets : ``<solution>.csolution.yml#<projet>``.
* **CMake** (dossier avec ``CMakeLists.txt``) : ``cmake -G Ninja`` avec ``cmake/e7-m55.toolchain.cmake``.

``-fstack-usage`` est actif dans les deux cas. Pour cbuild, sans toucher au template : la variable
d'environnement ``CFLAGS`` est lue par CMake à la première configuration et ajoutée aux flags C. Les
``.su`` produits à côté des ``.o`` sont copiés dans ``<dossier de l'ELF>/su/`` : ``check`` (règle pile)
les y trouve (il cherche récursivement à côté de l'ELF).

Sortie : ``<out_dir>/<nom>/<cœur>/out/<nom>.elf`` (+ ``.map`` et ``su/`` à côté), ``.../tmp`` pour les
intermédiaires. Codes de retour : 0 succès, 1 échec de compilation, 2 environnement/usage.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import board as board_mod
from . import toolchain

REPO_ROOT = toolchain.REPO_ROOT
CMAKE_TOOLCHAIN_FILE = REPO_ROOT / "cmake" / "e7-m55.toolchain.cmake"

OK, FAIL, ENV_ERR = 0, 1, 2

_MEM_HEADER = re.compile(r"^\s*Memory region\s+Used Size", re.I)
_MEM_ROW = re.compile(r"^\s*\w[\w ]*:\s+\S+ ?\w*\s+\S+ ?\w*\s+[\d.]+%")


class BuildError(Exception):
    """Erreur d'usage ou d'environnement (code 2)."""


# ---------------------------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------------------------
def _err(msg: str) -> None:
    print(f"erreur : {msg}", file=sys.stderr)


def _exe(name: str) -> str:
    """Chemin complet de l'outil (CreateProcess cherche dans le PATH du parent, pas dans ``env``)."""
    p = toolchain.find(name)
    return str(p) if p else name


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    """Exécute ``cmd``, renvoie (code, sortie fusionnée). Isolé pour être remplacé dans les tests."""
    cp = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
    )
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def memory_usage(output: str) -> list[str]:
    """Lignes du tableau ``--print-memory-usage`` (en-tête + régions) trouvées dans la sortie du linker."""
    lines = output.splitlines()
    for i, ln in enumerate(lines):
        if _MEM_HEADER.match(ln):
            block = [ln]
            for nxt in lines[i + 1:]:
                if _MEM_ROW.match(nxt):
                    block.append(nxt)
                else:
                    break
            return block
    return []


def _newest(paths: list[Path]) -> Path | None:
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None


def collect_su(tmp_dir: Path, dest: Path) -> int:
    """Copie les ``.su`` de ``-fstack-usage`` (à côté des ``.o``) dans ``dest``. Renvoie leur nombre.

    Les sorties de sondage CMake (``CompilerId``) sont ignorées. Les noms sont préfixés d'un index
    pour éviter les collisions (deux ``main.c.su`` de dossiers différents).
    """
    files = sorted(p for p in tmp_dir.rglob("*.su") if "CompilerId" not in p.parts[-1] and "CompilerId" not in str(p))
    if dest.exists():
        shutil.rmtree(dest)
    if not files:
        return 0
    dest.mkdir(parents=True)
    for i, f in enumerate(files):
        shutil.copyfile(f, dest / f"{i:04d}_{f.name}")
    return len(files)


#: Au-delà, gcc ne peut plus écrire les .su sous Windows (MAX_PATH 260 : « cannot open ….su for writing »).
_LONG_PATH_WARN = 110


def _warn_long_path(out_root: Path) -> None:
    if sys.platform.startswith("win") and len(str(out_root)) > _LONG_PATH_WARN:
        print(f"attention : dossier de sortie long ({len(str(out_root))} car.) : sous Windows, gcc risque d'échouer "
              f"à écrire les .su (MAX_PATH 260). Utiliser un --out-dir court.", file=sys.stderr)


def _finalize(out_root: Path, tmp_dir: Path, elf: Path, map_candidates: list[Path]) -> Path:
    """Place le ``.map`` et les ``.su`` à côté de l'ELF final, renvoie l'ELF."""
    m = _newest(map_candidates)
    if m is not None:
        shutil.copyfile(m, elf.with_suffix(".map"))
    n = collect_su(tmp_dir, elf.parent / "su")
    print(f"ELF   : {elf}")
    if m is not None:
        print(f"map   : {elf.with_suffix('.map')}")
    print(f"su    : {n} fichier(s) dans {elf.parent / 'su'}" + ("" if n else "  (AUCUN : -fstack-usage inactif ?)"))
    return elf


# ---------------------------------------------------------------------------------------------
# csolution
# ---------------------------------------------------------------------------------------------
def _split_target(target: str) -> tuple[Path, str | None]:
    """``alif.csolution.yml#hello`` -> (chemin, 'hello')."""
    if "#" in target:
        path, _, proj = target.rpartition("#")
        return Path(path), proj or None
    return Path(target), None


def find_csolution(path: Path) -> tuple[Path, str | None] | None:
    """(fichier csolution, projet implicite) pour ``path``, ou ``None`` si ce n'est pas un csolution.

    Un dossier contenant un ``*.cproject.yml`` mais pas de csolution : on remonte les parents.
    """
    if path.is_file() and path.name.endswith(".csolution.yml"):
        return path, None
    if not path.is_dir():
        return None
    sols = sorted(path.glob("*.csolution.yml"))
    if sols:
        return sols[0], None
    cprojs = sorted(path.glob("*.cproject.yml"))
    if cprojs:
        for parent in path.resolve().parents:
            ps = sorted(parent.glob("*.csolution.yml"))
            if ps:
                return ps[0], cprojs[0].name[: -len(".cproject.yml")]
    return None


def _load_yaml(p: Path) -> dict:
    import yaml

    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("solution") or {}


def select_context(sol: Path, core: str, build_type: str, project: str | None, b: board_mod.Board | None = None) -> str:
    """Contexte cbuild ``<projet>.<build>+<target-type>`` pour ``core``, lu dans le csolution."""
    b = b or board_mod.load()
    data = _load_yaml(sol)
    soc = str(b.info.get("cmsis_device") or b.info.get("soc") or "")
    suffix = ":" + core.upper()  # ':M55_HP'
    candidates = [
        t["type"]
        for t in data.get("target-types", []) or []
        if str(t.get("device", "")).upper().endswith(suffix.upper()) and soc.upper() in str(t.get("device", "")).upper()
    ]
    if not candidates:
        raise BuildError(f"aucun target-type de {sol.name} ne vise {soc}{suffix} (device lu dans target-types)")
    if len(candidates) > 1:
        raise BuildError(f"target-type ambigu pour {core} dans {sol.name} : {candidates}")

    projects = [Path(p["project"]).name[: -len(".cproject.yml")] for p in data.get("projects", []) or [] if "project" in p]
    if project is None:
        if len(projects) == 1:
            project = projects[0]
        else:
            raise BuildError(
                f"{sol.name} contient plusieurs projets {projects} : préciser '{sol.name}#<projet>'"
            )
    elif projects and project not in projects:
        raise BuildError(f"projet {project!r} absent de {sol.name} (projets : {projects})")
    return f"{project}.{build_type}+{candidates[0]}"


def _build_csolution(sol: Path, project: str | None, core: str, build_type: str, out_dir: Path,
                     env: dict[str, str]) -> int:
    ctx = select_context(sol, core, build_type, project)
    proj = ctx.split(".")[0]
    out_root = (out_dir / sol.name[: -len(".csolution.yml")] / proj / core).resolve()
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)
    _warn_long_path(out_root)

    cmd = [_exe("cbuild"), str(sol.resolve()), "--context", ctx, "-O", str(out_root)]
    if (sol.parent / (sol.name[: -len(".csolution.yml")] + ".cbuild-pack.yml")).is_file():
        cmd.append("--frozen-packs")  # versions de packs imposées par le template
    # -fstack-usage sans modifier le template : CMake initialise CMAKE_C_FLAGS avec $CFLAGS.
    flags = (env.get("CFLAGS", "") + " -fstack-usage").strip()
    env = {**env, "CFLAGS": flags, "CXXFLAGS": (env.get("CXXFLAGS", "") + " -fstack-usage").strip()}

    print(f"==> {' '.join(cmd)}  (CFLAGS={flags!r})")
    code, out = _run(cmd, sol.parent, env)
    if code != 0:
        print(out)
        _err(f"cbuild a échoué (code {code}) pour le contexte {ctx}")
        return FAIL
    elfs = [p for p in (out_root / "out").rglob("*.elf")] if (out_root / "out").is_dir() else []
    elf = _newest(elfs)
    if elf is None:
        print(out)
        _err(f"cbuild a réussi mais aucun .elf sous {out_root / 'out'}")
        return FAIL
    for ln in memory_usage(out):
        print(ln)
    tmp = out_root / "tmp"
    _finalize(out_root, tmp, elf, list(tmp.rglob("linker.map")) if tmp.is_dir() else [])
    return OK


# ---------------------------------------------------------------------------------------------
# CMake
# ---------------------------------------------------------------------------------------------
def _build_cmake(src: Path, core: str, build_type: str, out_dir: Path, env: dict[str, str]) -> int:
    out_root = (out_dir / src.resolve().name / core).resolve()
    if out_root.exists():
        shutil.rmtree(out_root)
    tmp = out_root / "tmp"
    tmp.mkdir(parents=True)
    _warn_long_path(out_root)
    cfg = [
        _exe("cmake"), "-G", "Ninja", "-S", str(src.resolve()), "-B", str(tmp),
        f"-DCMAKE_TOOLCHAIN_FILE={CMAKE_TOOLCHAIN_FILE.as_posix()}",
        f"-DCMAKE_BUILD_TYPE={'Release' if build_type == 'release' else 'Debug'}",
        f"-DE7_CORE={core}",
    ]
    for cmd in (cfg, [_exe("cmake"), "--build", str(tmp)]):
        print("==> " + " ".join(cmd))
        code, out = _run(cmd, src, env)
        if code != 0:
            print(out)
            _err(f"cmake {'--build' if '--build' in cmd else 'configure'} a échoué (code {code})")
            return FAIL
    elfs = list(tmp.rglob("*.elf"))
    elf = _newest([p for p in elfs if "CMakeFiles" not in p.parts])
    if elf is None:
        print(out)
        _err(f"build réussi mais aucun .elf sous {tmp}")
        return FAIL
    for ln in memory_usage(out):
        print(ln)
    final = out_root / "out" / elf.name
    final.parent.mkdir(parents=True)
    shutil.copyfile(elf, final)
    _finalize(out_root, tmp, final, list(tmp.glob("*.map")))
    return OK


# ---------------------------------------------------------------------------------------------
def main(target: str, core: str | None = None, build_type: str = "debug", out_dir: str = "build") -> int:
    """Point d'entrée CLI ``e7twin build <cible> --core m55_hp|m55_he --build-type debug|release``.

    ``target`` : ``.csolution.yml`` (suffixe ``#projet`` si plusieurs projets), dossier de csolution
    ou de cproject, ou dossier CMake. ``core`` par défaut : ``m55_hp``. Retourne 0, 1 (échec de
    compilation) ou 2 (environnement absent / usage).
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        b = board_mod.load()
        core = core or "m55_hp"
        if not b.core(core).executable:
            raise BuildError(f"le cœur {core} n'est pas une cible de build (cibles : "
                             f"{[c.id for c in b.cores if c.executable]})")
        if build_type not in ("debug", "release"):
            raise BuildError(f"build_type invalide : {build_type!r} (debug|release)")
        path, proj = _split_target(str(target))
        sol = find_csolution(path)
        is_cmake = sol is None and path.is_dir() and (path / "CMakeLists.txt").is_file()
        if sol is None and not is_cmake:
            raise BuildError(f"{path} : ni .csolution.yml, ni dossier de projet, ni CMakeLists.txt")
        needed = ["arm-none-eabi-gcc"] + (["cmake", "ninja"] if is_cmake else ["cbuild", "csolution"])
        res = {r["name"]: r for r in toolchain.doctor()}
        if not is_cmake:
            needed += [n for n in res if n.startswith("pack ")]
        missing = [n for n in needed if not res.get(n, {}).get("found")]
        if missing:
            for n in missing:
                _err(f"{n} manquant : {res.get(n, {}).get('hint', '')}")
            return ENV_ERR
        env = toolchain.env()
        if is_cmake:
            if not CMAKE_TOOLCHAIN_FILE.is_file():
                raise BuildError(f"toolchain file introuvable : {CMAKE_TOOLCHAIN_FILE}")
            return _build_cmake(path, core, build_type, Path(out_dir), env)
        sol_path, implicit = sol
        return _build_csolution(sol_path, proj or implicit, core, build_type, Path(out_dir), env)
    except BuildError as e:
        _err(str(e))
        return ENV_ERR
    except (KeyError, OSError) as e:
        _err(str(e))
        return ENV_ERR
