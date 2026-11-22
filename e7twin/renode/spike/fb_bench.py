"""Spike écran, étape A : coût de capture d'un framebuffer depuis l'hôte (agent 04).

    py -3.14 e7twin/renode/spike/fb_bench.py [--size 768000] [--addr 0x02000000] [--repeat 5]

Démarre une `sim.Session` avec `build/spike/hello_hp.elf` (cœur HP en WFI après « Hello World! »),
charge un motif pseudo-aléatoire de `--size` octets en SRAM0 (`LoadBinary`), puis mesure plusieurs voies de
lecture et vérifie que chacune rend exactement le motif :

  readbytes_4k   Session.read_mem (monitor `sysbus ReadBytes` par blocs de 4096, texte hexadécimal)
  readbytes_64k  idem, blocs de 65536
  python_file    commande monitor `python` : IronPython lit le bus (`SystemBus.ReadBytes`) et écrit un
                 fichier binaire (chemin absolu), relu côté hôte
  python_busread même commande sans fichier (borne basse : coût interne Renode + aller-retour telnet)

`sysbus.<mem> DumpBinary` existe mais n'est pas appelable depuis le monitor de Renode 1.17 (paramètre
`SequencedFilePath` : « Parameters did not match the signature » avec `@chemin` comme avec "chemin").

Résultat : tableau ms/image (médiane, min, max) sur la sortie standard. Code 0 si toutes les voies
retenues rendent le motif, 1 sinon, 2 si l'outillage manque.
"""

from __future__ import annotations

import argparse
import random
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from e7twin import board as boardmod  # noqa: E402
from e7twin import sim  # noqa: E402

ELF = REPO / "build" / "spike" / "hello_hp.elf"


def read_monitor(s: sim.Session, addr: int, size: int, chunk: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        n = min(chunk, size - len(out))
        txt = s._mon.cmd(f"sysbus ReadBytes {addr + len(out):#x} {n}")
        got = bytes(int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]{1,2}\b", txt))
        if len(got) != n:
            raise sim.SimError(f"ReadBytes : {len(got)}/{n} octets")
        out += got
    return bytes(out)


def _take(path: Path) -> bytes:
    data = path.read_bytes()
    path.unlink()
    return data


def read_python(s: sim.Session, addr: int, size: int, path: Path) -> bytes:
    # Le répertoire courant de l'interpréteur IronPython est celui de Renode (tools/renode-*) : chemin ABSOLU.
    # L'espace de `C:/Dev/E7 Twin` passe ici car il est dans une chaîne Python, pas dans un jeton `@chemin`.
    s._mon.cmd(
        f'python "System.IO.File.WriteAllBytes(\'{path.resolve().as_posix()}\', '
        f'self.Machine.SystemBus.ReadBytes({addr:#x}, {size}))"'
    )
    return _take(path)


def read_python_only(s: sim.Session, addr: int, size: int) -> bytes:
    """Coût interne Renode seul (lecture du bus, sans fichier ni transfert) : borne basse."""
    out = s._mon.cmd(f'python "print(len(self.Machine.SystemBus.ReadBytes({addr:#x}, {size})))"')
    if str(size) not in out:
        raise sim.SimError(out[-200:])
    return b""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--size", type=int, default=480 * 800 * 2, help="octets par image (480x800 RGB565 par défaut)")
    ap.add_argument("--addr", type=lambda v: int(v, 0), default=None, help="adresse du framebuffer (défaut : base de sram0)")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--skip-4k", action="store_true", help="ne mesure pas la voie ReadBytes 4 Ko (la plus lente)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not ELF.is_file():
        print(f"ELF manquant : {ELF}", file=sys.stderr)
        return 2
    b = boardmod.load()
    addr = args.addr if args.addr is not None else b.region("sram0").base
    reg = next(r for r in b.memories if r.base <= addr < r.base + r.size and not r.local)

    pattern = random.Random(7).randbytes(args.size)
    results: dict[str, list[float]] = {}
    ok = True
    with sim.Session(b) as s:
        s.load_elf("m55_hp", ELF)
        s.start()
        if not s.run_until("Hello World!", 60):
            print("hello_hp n'a pas démarré", file=sys.stderr)
            return 1
        wd = s._workdir
        src = wd / "fb_pattern.bin"
        src.write_bytes(pattern)
        s._mon.cmd(f"sysbus LoadBinary @{src.relative_to(REPO).as_posix()} {addr:#x}")

        methods = {
            "readbytes_64k": lambda: read_monitor(s, addr, args.size, 65536),
            "python_file": lambda: read_python(s, addr, args.size, wd / "fb_py.bin"),
            "python_busread": lambda: read_python_only(s, addr, args.size),
        }
        if not args.skip_4k:
            methods = {"readbytes_4k": lambda: read_monitor(s, addr, args.size, 4096), **methods}
        for name, fn in methods.items():
            times = []
            try:
                for i in range(args.repeat if not name.startswith("readbytes") else min(args.repeat, 2)):
                    t0 = time.perf_counter()
                    data = fn()
                    times.append((time.perf_counter() - t0) * 1000)
                    if data != pattern and name != "python_busread":
                        print(f"{name}: contenu DIFFÉRENT du motif ({len(data)} octets)")
                        ok = False
                        break
            except Exception as e:  # noqa: BLE001 — on veut le tableau même si une voie échoue
                print(f"{name}: ÉCHEC {type(e).__name__}: {str(e)[:200]}")
                ok = False
            if times:
                results[name] = times
        # contrôle : le cœur tourne toujours (la capture ne doit pas casser la session)
        t_virtual = s.virtual_time()

    print(f"\nimage = {args.size} octets @ {addr:#x} ({reg.name}), temps virtuel final {t_virtual:.3f} s")
    print(f"{'méthode':<15}{'n':>3}{'médiane ms':>12}{'min':>9}{'max':>9}{'img/s max':>11}")
    for name, t in results.items():
        med = statistics.median(t)
        print(f"{name:<15}{len(t):>3}{med:>12.1f}{min(t):>9.1f}{max(t):>9.1f}{1000 / med:>11.2f}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
