"""``e7twin package`` : ELF -> ``.bin`` + JSON ATOC + commandes SETOOLS à lancer par un humain (agent 02).

Pour chaque image (cœur M55) :

1. ``arm-none-eabi-objcopy -O binary`` sur les seules sections dont l'adresse **de chargement (LMA)**
   tombe dans le slot MRAM du cœur (``board.load().slot(core, 'mram')``) : code/données en MRAM, et
   code/données *liés* en TCM mais *stockés* en MRAM (copiés par le startup via ``.copy.table``).
   Les segments à LMA en RAM (``.bss``, pile, tas) sont exclus. Le ``.bin`` va de la LMA minimale à
   la LMA maximale, ce qui est exactement l'étendue que mesure ``check`` pour la règle ``atoc``.
2. JSON ATOC au format SETOOLS (``DEVICE`` + ``HP_APP`` / ``HE_APP`` avec ``mramAddress`` = début du
   slot, ``cpu_id``, ``flags: ["boot"]``, ``signed: false``), comme ``.alif/M55_HP_HE_mram_cfg.json``
   du template (sans les stubs A32/HE : inutiles tant que seuls les M55 sont utilisés).
3. ``check.main(elf, core, atoc=<json>)`` pour chaque image : si l'un est en FAIL, rien n'est produit.
4. **Affichage** des commandes SETOOLS. Elles ne sont **jamais** exécutées : ce module n'appelle que
   ``objcopy`` et ne lance aucun programme SETOOLS (``app-gen-toc``, ``app-write-mram``).

Codes de retour : 0 succès, 1 verdict FAIL de ``check``, 2 erreur (outil, ELF, cœur, usage).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from . import board as board_mod
from . import toolchain

OK, FAIL, ERR = 0, 1, 2

#: Entrée ``DEVICE`` recopiée du template (``.alif/*_cfg.json`` : fichier fourni par SETOOLS).
DEVICE_ENTRY = {"disabled": False, "binary": "app-device-config.json", "version": "0.5.00", "signed": True}


class PackageError(Exception):
    """Erreur d'usage ou d'outil (code 2)."""


@dataclass
class Image:
    elf: Path
    core: str            # "m55_hp"
    slot_name: str
    mram_address: int    # début du slot (adresse mise dans le JSON)
    start: int           # LMA minimale de l'image
    end: int             # LMA maximale (exclue)
    sections: list[str]
    bin_path: Path | None = None

    @property
    def size(self) -> int:
        return self.end - self.start

    @property
    def atoc_key(self) -> str:          # "HP_APP"
        return self.core.split("_", 1)[1].upper() + "_APP"

    @property
    def cpu_id(self) -> str:            # "M55_HP"
        return self.core.upper()


# ---------------------------------------------------------------------------------------------
def _err(msg: str) -> None:
    print(f"erreur : {msg}", file=sys.stderr)


def _bin_name(img: Image) -> str:
    return f"{img.cpu_id}_{img.elf.stem}.bin"


def analyze_image(elf_path: Path, core: str | None, b: board_mod.Board | None = None) -> Image:
    """Segments chargés en MRAM pour ``core`` (déduit du slot qui contient l'image si ``None``)."""
    b = b or board_mod.load()
    try:
        with elf_path.open("rb") as f:
            elf = ELFFile(f)
            loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD" and s["p_filesz"] > 0]
            if not loads:
                raise PackageError(f"{elf_path} : aucun segment PT_LOAD avec des données")
            if core is None:
                hits = sorted({s.core for s in b.atoc_slots if s.kind == "mram"
                               and any(s.base <= seg["p_paddr"] < s.end for seg in loads)
                               and b.core(s.core).executable})
                if len(hits) != 1:
                    raise PackageError(f"{elf_path.name} : cœur indéterminable depuis les LMA (slots touchés : {hits}) ; "
                                       "préciser --core")
                core = hits[0]
            try:
                c = b.core(core)
                slot = b.slot(core, "mram")
            except KeyError as e:
                raise PackageError(str(e.args[0] if e.args else e)) from e
            if not c.executable:
                raise PackageError(f"le cœur {core} n'est pas exécutable en v1")
            segs = [s for s in loads if slot.base <= s["p_paddr"] < slot.end]
            if not segs:
                raise PackageError(f"{elf_path.name} : aucun segment chargé dans le slot MRAM {slot.name} "
                                   f"[{slot.base:#x}, {slot.end:#x}) du cœur {core}")
            names: list[str] = []
            for seg in segs:
                for sec in elf.iter_sections():
                    if (sec["sh_flags"] & 2 and sec["sh_type"] != "SHT_NOBITS" and sec["sh_size"] > 0
                            and seg.section_in_segment(sec) and sec.name not in names):
                        names.append(sec.name)
            start = min(s["p_paddr"] for s in segs)
            end = max(s["p_paddr"] + s["p_filesz"] for s in segs)
            if end > slot.end:
                raise PackageError(f"image {start:#x}..{end:#x} dépasse le slot {slot.name} (fin {slot.end:#x})")
            return Image(elf_path, core, slot.name, slot.base, start, end, names)
    except ELFError as e:
        raise PackageError(f"{elf_path} n'est pas un ELF valide : {e}") from e
    except OSError as e:
        raise PackageError(str(e)) from e


def _objcopy(img: Image, dest: Path) -> None:
    tool = toolchain.find("arm-none-eabi-objcopy")
    if tool is None:
        raise PackageError("arm-none-eabi-objcopy introuvable : " + toolchain.INSTALL_HINT)
    cmd = [str(tool), "-O", "binary"]
    for n in img.sections:
        cmd += ["-j", n]
    cmd += [str(img.elf), str(dest)]
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                        stdin=subprocess.DEVNULL)
    if cp.returncode != 0:
        raise PackageError(f"objcopy a échoué : {(cp.stderr or cp.stdout).strip()[:300]}")
    # Garde-fou : le .bin doit couvrir exactement [start, end) (objcopy remplit les trous de zéros).
    got = dest.stat().st_size
    if got != img.size:
        raise PackageError(f"{dest.name} : {got} o au lieu de {img.size} o attendus (sections {img.sections})")
    img.bin_path = dest


def atoc_dict(images: list[Image]) -> dict:
    d: dict = {"DEVICE": dict(DEVICE_ENTRY)}
    for img in images:
        d[img.atoc_key] = {
            "disabled": False,
            "binary": img.bin_path.name if img.bin_path else _bin_name(img),
            "version": "1.0.0",
            "signed": False,
            "mramAddress": f"0x{img.mram_address:08X}",
            "cpu_id": img.cpu_id,
            "flags": ["boot"],
        }
    return d


def atoc_filename(images: list[Image]) -> str:
    parts = [i.core.split("_", 1)[1].upper() for i in sorted(images, key=lambda i: i.core, reverse=True)]  # hp avant he
    return f"M55_{'_'.join(parts)}_mram_cfg.json"


def setools_commands(json_path: Path, bins: list[Path]) -> list[str]:
    """Lignes à lancer **par un humain** (jamais exécutées par e7twin). Modèle : tâches du template."""
    cfg = f"build/config/{json_path.name}"
    lines = [
        "# --- À lancer par un humain, sur le PC relié à la carte (SETOOLS : app-release-exec). e7twin ne les exécute jamais. ---",
        "# 0. Sélectionner explicitement la cible E7 (depuis SETOOLS 1.107 le défaut est l'E8 DevKit) avec l'outil de",
        "#    configuration de SETOOLS (tools-config) : pas vérifié ici, voir le SWRM Alif (ASRM0002).",
        "cd <SETOOLS_ROOT>",
    ]
    for p in bins:
        lines.append(f'cp "{p.resolve()}" build/images/')
    lines += [
        f'cp "{json_path.resolve()}" {cfg}',
        f"./app-gen-toc -f {cfg}",
        "./app-write-mram -p",
    ]
    return lines


# ---------------------------------------------------------------------------------------------
def main(elf: str, core: str | None = None, out_dir: str = "build/package",
         atoc_with: list[str] | None = None) -> int:
    """Point d'entrée CLI ``e7twin package <elf> --core ... [--with <elf2>]``.

    ``atoc_with`` : ELF d'autres cœurs à combiner dans le même JSON (ex. ``hello_he.elf`` avec un
    ``hello_hp.elf``). Le cœur de chaque image supplémentaire est déduit de ses LMA.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    from . import check as check_mod

    out = Path(out_dir)
    created: list[Path] = []
    try:
        b = board_mod.load()
        images = [analyze_image(Path(elf), core, b)]
        for extra in atoc_with or []:
            images.append(analyze_image(Path(extra), None, b))
        cores = [i.core for i in images]
        if len(set(cores)) != len(cores):
            raise PackageError(f"deux images pour le même cœur : {cores}")
        out.mkdir(parents=True, exist_ok=True)
        for img in images:
            dest = out / _bin_name(img)
            _objcopy(img, dest)
            created.append(dest)
            print(f"{img.elf.name} -> {dest}  ({img.size} o, {img.cpu_id} @ {img.mram_address:#010x}, "
                  f"slot {img.slot_name}, sections {', '.join(img.sections)})")
        json_path = out / atoc_filename(images)
        json_path.write_text(json.dumps(atoc_dict(images), indent=4) + "\n", encoding="utf-8")
        created.append(json_path)
        print(f"ATOC : {json_path}")

        worst = OK
        for img in images:
            print(f"--- check {img.cpu_id} ---")
            rc = check_mod.main(img.elf, img.core, atoc=json_path, out_dir=out / "reports" / img.core)
            worst = max(worst, rc)
        if worst != OK:
            for p in created:
                p.unlink(missing_ok=True)
            _err(("check a rendu FAIL" if worst == FAIL else "check a échoué (erreur)")
                 + " : paquet refusé, fichiers générés supprimés.")
            return worst
    except PackageError as e:
        for p in created:
            p.unlink(missing_ok=True)
        _err(str(e))
        return ERR

    print()
    print("\n".join(setools_commands(json_path, [i.bin_path for i in images if i.bin_path])))
    print("\n(e7twin n'a rien flashé : lancez ces commandes vous-même.)")
    return OK
