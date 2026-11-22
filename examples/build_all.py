#!/usr/bin/env python3
"""Compile les firmwares de reference E7 Twin (examples/) avec cbuild (multiplateforme).

Equivalent Python de ``build_all.ps1`` : ELF, .map et .su dans ``build/examples/<nom>.{elf,map,su}``.
Environnement : ``e7twin.toolchain.env()`` (meme source que le script PowerShell).
Un dossier de sortie cbuild par projet. Ne flashe rien.

Usage : python examples/build_all.py [--only NOM ...] [--build-type debug]
Code de sortie : 0 si tout compile, 1 si un projet echoue, 2 si outils/arguments invalides.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EX = REPO / "examples"
DEST = REPO / "build" / "examples"

# nom du projet -> coeur ; un projet = un contexte <nom>.<type>+DevKit-E7-<coeur>
PROJECTS = {
    "hello_hp": "HP",
    "hello_he": "HE",
    "helium_dot": "HP",
    "too_big": "HE",
    "stack_overflow": "HP",
    "dual_core_mhu_hp": "HP",
    "dual_core_mhu_he": "HE",
    "load_loop_hp": "HP",
    "load_loop_he": "HE",
    "display_hp": "HP",
    "parity_hp": "HP",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="*", default=[], metavar="NOM")
    ap.add_argument("--build-type", default="debug")
    args = ap.parse_args(argv)
    for o in args.only:
        if o not in PROJECTS:
            print(f"projet inconnu : {o} (attendus : {', '.join(PROJECTS)})", file=sys.stderr)
            return 2

    sys.path.insert(0, str(REPO))
    from e7twin import toolchain

    missing = toolchain.missing_required()
    if missing:
        print("Outils/packs manquants : " + ", ".join(missing), file=sys.stderr)
        return 2
    env = {**os.environ, **toolchain.env()}
    # cbuild : celui de l'environnement construit (tools/ prioritaire), sinon PATH
    cbuild = shutil.which("cbuild", path=env.get("PATH") or env.get("Path")) or "cbuild"

    DEST.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for name, core in PROJECTS.items():
        if args.only and name not in args.only:
            continue
        ctx = f"{name}.{args.build_type}+DevKit-E7-{core}"
        out_base = REPO / "build" / "examples_cbuild" / name
        if out_base.exists():
            shutil.rmtree(out_base, ignore_errors=True)
        print(f"==> cbuild {ctx}", flush=True)
        rc = subprocess.run([cbuild, "alif.csolution.yml", "--context", ctx, "-O", str(out_base)],
                            cwd=EX, env=env).returncode
        if rc != 0:
            print(f"WARNING: cbuild {ctx} a echoue (code {rc})", file=sys.stderr)
            failed.append(name)
            continue
        elf = next(out_base.rglob(f"{name}.elf"), None)
        if elf is None:
            print(f"WARNING: {name}.elf introuvable sous {out_base}", file=sys.stderr)
            failed.append(name)
            continue
        shutil.copy2(elf, DEST / f"{name}.elf")
        # cdefault.yml : -Wl,-Map=linker.map (sous tmp/)
        mp = next(out_base.rglob("linker.map"), None)
        if mp:
            shutil.copy2(mp, DEST / f"{name}.map")
        else:
            print(f"WARNING: {name} : .map introuvable", file=sys.stderr)
        su_files = sorted(out_base.rglob("*.su"))
        su_out = DEST / f"{name}.su"
        if su_files:
            with su_out.open("w", encoding="ascii", errors="replace", newline="\n") as f:
                for s in su_files:
                    f.write(s.read_text(encoding="utf-8", errors="replace"))
        else:
            print(f"WARNING: {name} : aucun .su (flag -fstack-usage non pris en compte ?)", file=sys.stderr)
            su_out.unlink(missing_ok=True)
        print(f"    OK  {DEST / (name + '.elf')}  ({len(su_files)} .su)", flush=True)

    if failed:
        print("projets en echec : " + ", ".join(failed), file=sys.stderr)
        return 1
    print(f"Tous les projets compiles dans {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
