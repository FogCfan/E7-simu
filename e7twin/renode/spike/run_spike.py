"""Spike Renode (phase 0) : compile les 3 firmwares bare-metal, les exécute dans Renode et
vérifie les textes attendus sur les UART.

    py -3.14 e7twin/renode/spike/run_spike.py [--only uart|mve|dual] [--keep-going] [-v]

Code de sortie : 0 si tous les textes attendus sont vus, 1 sinon, 2 si l'outillage manque.

Aucune adresse en dur : `e7_board.h` et le linker script sont générés depuis `board.load()`.
Renode est lancé avec le dépôt comme répertoire courant et des chemins relatifs : le tokenizer du
monitor Renode 1.17 ne sait pas traiter un chemin contenant un espace (`C:\\Dev\\E7 Twin`).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from e7twin import board as boardmod  # noqa: E402
from e7twin import toolchain  # noqa: E402
from e7twin import sim  # noqa: E402

BUILD = REPO / "build" / "spike"
REPL = REPO / "e7twin" / "renode" / "e7.repl"

# Commun à tous les firmwares : Cortex-M55, ABI hard, MVE (+fp via -mcpu), freestanding.
CFLAGS = [
    "-mcpu=cortex-m55", "-mfloat-abi=hard", "-mthumb", "-O2", "-g",
    "-ffreestanding", "-fno-builtin", "-ffunction-sections", "-fdata-sections",
    "-Wall", "-Wextra", "-Werror",
]
LDFLAGS = ["-nostdlib", "-nostartfiles", "-Wl,--gc-sections", "-Wl,--no-warn-rwx-segments"]


@dataclass
class Firmware:
    name: str            # nom de l'ELF
    core: str            # m55_hp | m55_he
    srcs: list[str]
    defs: list[str] = field(default_factory=list)
    prebuilt: Path | None = None   # ELF déjà compilé (SDK) : pas de compilation


@dataclass
class Scenario:
    name: str
    firmwares: list[Firmware]
    # (uart, texte attendu) : vu sur la sortie de cette UART
    expect: list[tuple[str, str]]
    timeout_s: float = 20.0   # garde-fou en temps réel
    expect_fault: bool = False  # le scénario doit provoquer un HardFault détecté par Session.faults()
    min_mhu_events: int = 0   # messages MHU (send + ack) attendus dans le journal
    pre: list[str] = field(default_factory=list)   # commandes monitor avant le dé-halt (debug)
    dump: list[str] = field(default_factory=list)  # commandes monitor à afficher en fin de run


def _hp(name, srcs, defs=()):
    return Firmware(name, "m55_hp", srcs, ["E7_FAULT_UART=E7_UART4_BASE", *defs])


def _he(name, srcs, defs=()):
    return Firmware(name, "m55_he", srcs, ["E7_FAULT_UART=E7_LPUART_BASE", *defs])


SCENARIOS = [
    Scenario("uart", [_hp("uart_hp", ["start.c", "uart_hp.c"])], [("uart4", "Hello E7 HP")]),
    Scenario("mve", [_hp("mve_hp", ["start.c", "mve_hp.c"])], [("uart4", "MVE OK")]),
    Scenario(
        "mhu_pingpong",
        [_hp("mhu_hp", ["start.c", "mhu_pingpong.c"], ["PING_HP"]),
         _he("mhu_he", ["start.c", "mhu_pingpong.c"], ["PING_HE"])],
        [("uart4", "PONG 100"), ("lpuart", "SERVED 100")],
        min_mhu_events=400,
    ),
    Scenario("fault", [_hp("fault_hp", ["start.c", "fault_hp.c"])], [("uart4", "HARDFAULT")],
             expect_fault=True),
    Scenario(
        "dual",
        [_hp("dual_hp", ["start.c", "dual.c"], ["DUAL_HP"]),
         _he("dual_he", ["start.c", "dual.c"], ["DUAL_HE"])],
        [("uart4", "DUAL HP OK"), ("lpuart", "DUAL HE OK")],
    ),
]

# Partie 4 : ELF `hello` du SDK Alif (agent 02), exécutés tels quels. Option --sdk.
SDK_SCENARIOS = [
    Scenario("sdk_hp", [Firmware("hello_hp", "m55_hp", [], prebuilt=BUILD / "hello_hp.elf")],
             [("uart4", "Hello World!")], timeout_s=30.0,
             # le contournement de MPU_Setup() est appliqué par sim.Session (SPIKE_RENODE.md, blocage 2)
             ),
    Scenario("sdk_he", [Firmware("hello_he", "m55_he", [], prebuilt=BUILD / "hello_he.elf")],
             [("uart4", "Hello World!")], timeout_s=30.0),
]


# ------------------------------------------------------------------ génération depuis le modèle

def gen_header(b: boardmod.Board) -> str:
    def per(name):
        return b.peripheral(name)

    u4, u7, lp = per("uart4"), per("uart7"), per("lpuart")
    shift = {4: 2, 1: 0}[u4.attrs.get("reg_width", 4)]
    sram0 = b.region("sram0")
    lines = [
        "/* GÉNÉRÉ par run_spike.py depuis board/ensemble_e7.yaml : ne pas éditer. */",
        "#pragma once",
        f"#define E7_UART_REG_SHIFT {shift}u",
        f"#define E7_UART4_BASE {u4.base:#x}u",
        f"#define E7_UART7_BASE {u7.base:#x}u",
        f"#define E7_UART7_IRQ {u7.irq['m55_hp']}u",
        f"#define E7_LPUART_BASE {lp.base:#x}u",
        f"#define E7_SHARED_BASE {sram0.base:#x}u",
        f"#define E7_NUM_IRQS {max(max(p.irq.values(), default=0) for p in b.peripherals) + 1}",
    ]
    assert u7.irq["m55_hp"] == u7.irq["m55_he"], "le fan-out suppose le même numéro d'IRQ"
    # MHU HP<->HE canal 0 : mêmes adresses/IRQ vues des deux cœurs (fenêtres locales symétriques)
    tx = {c: b.peripheral(f"mhu_{c}_{o}_0_tx") for c, o in (("m55hp", "m55he"), ("m55he", "m55hp"))}
    rx = {c: b.peripheral(f"mhu_{o}_{c}_0_rx") for c, o in (("m55hp", "m55he"), ("m55he", "m55hp"))}
    assert tx["m55hp"].base == tx["m55he"].base and rx["m55hp"].base == rx["m55he"].base
    assert tx["m55hp"].irq["m55_hp"] == tx["m55he"].irq["m55_he"] and rx["m55hp"].irq["m55_hp"] == rx["m55he"].irq["m55_he"]
    lines += [
        f"#define E7_MHU_TX_BASE {tx['m55hp'].base:#x}u",
        f"#define E7_MHU_RX_BASE {rx['m55hp'].base:#x}u",
        f"#define E7_MHU_TX_IRQ {tx['m55hp'].irq['m55_hp']}u",
        f"#define E7_MHU_RX_IRQ {rx['m55hp'].irq['m55_hp']}u",
    ]
    return "\n".join(lines) + "\n"


def gen_linker(b: boardmod.Board, core: str) -> str:
    slot = b.slot(core, "mram")
    ram = b.region("dtcm_" + core.split("_")[1])  # TCM locale du cœur, adresse vue par ce cœur
    assert ram.local and core in ram.visible_to
    tpl = Template((HERE / "link.ld.in").read_text(encoding="utf-8"))
    return tpl.substitute(
        FLASH_BASE=f"{slot.base:#x}", FLASH_SIZE=f"{slot.size:#x}",
        RAM_BASE=f"{ram.base:#x}", RAM_SIZE=f"{ram.size:#x}",
    )


# ------------------------------------------------------------------ compilation

def build_fw(fw: Firmware, b: boardmod.Board, gcc: Path, verbose: bool) -> Path:
    gen = BUILD / "gen"
    gen.mkdir(parents=True, exist_ok=True)
    ld = gen / f"{fw.core}.ld"
    ld.write_text(gen_linker(b, fw.core), encoding="utf-8")
    elf = BUILD / f"{fw.name}.elf"
    cmd = [str(gcc), *CFLAGS, f"-I{gen}", f"-I{HERE}"]
    cmd += [f"-D{d}" for d in fw.defs]
    cmd += [str(HERE / s) for s in fw.srcs]
    cmd += [*LDFLAGS, f"-T{ld}", f"-Wl,-Map={BUILD / (fw.name + '.map')}", "-o", str(elf)]
    if verbose:
        print("  $", " ".join(cmd))
    cp = subprocess.run(cmd, capture_output=True, text=True, env=toolchain.env())
    if cp.returncode != 0:
        print(cp.stdout + cp.stderr)
        raise SystemExit(f"échec de compilation : {fw.name}")
    return elf


# ------------------------------------------------------------------ exécution (e7twin.sim.Session)

def run_scenario(sc: Scenario, b: boardmod.Board, gcc: Path, renode: Path, verbose: bool) -> bool:
    print(f"[{sc.name}] compilation…")
    for fw in sc.firmwares:
        if fw.prebuilt is None:
            build_fw(fw, b, gcc, verbose)
    print(f"[{sc.name}] Renode ({', '.join(fw.core for fw in sc.firmwares)})…")
    ok = True
    try:
        with sim.Session(b, renode=renode) as s:
            for fw in sc.firmwares:
                s.load_elf(fw.core, fw.prebuilt or BUILD / (fw.name + ".elf"))
            for line in sc.pre:
                s._mon.cmd(line)
            s.start()
            end = time.time() + sc.timeout_s
            while time.time() < end and not all(s.run_until(t, 0) for _, t in sc.expect):
                time.sleep(0.1)
            for uart, text in sc.expect:
                out = s.uart_text(uart)
                seen = text in out
                ok &= seen
                print(f"[{sc.name}] {uart}: attendu {text!r} -> {'OK' if seen else 'ABSENT'}")
                if verbose or not seen:
                    print(f"    sortie {uart} : {out!r}")
            if sc.min_mhu_events:
                evs = s.mhu_events()
                good = len(evs) >= sc.min_mhu_events
                ok &= good
                print(f"[{sc.name}] événements MHU : {len(evs)} (min {sc.min_mhu_events}) -> {'OK' if good else 'INSUFFISANT'}")
            faults = s.faults()
            for f in faults:
                print(f"[{sc.name}] FAULT {f}")
            if sc.expect_fault:
                ok &= any(f.kind == "hardfault" and f.cfsr and f.pc for f in faults)
                print(f"[{sc.name}] faute attendue détectée -> {'OK' if ok else 'NON'}")
            elif faults:
                ok = False
            for line in sc.dump:
                print(f"[{sc.name}] {line} -> {s._mon.cmd(line)}")
            if not ok:
                print(f"[{sc.name}] log Renode : {s._workdir / 'renode.log'}")
    except sim.SimError as e:
        print(f"[{sc.name}] ERREUR sim : {e}")
        return False
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=[s.name for s in SCENARIOS + SDK_SCENARIOS])
    ap.add_argument("--sdk", action="store_true", help="exécute aussi les ELF hello du SDK (build/spike/hello_*.elf)")
    ap.add_argument("--only-sdk", action="store_true", help="n'exécute que les scénarios SDK")
    ap.add_argument("--pre", action="append", default=[], help="commande monitor à exécuter avant le dé-halt (debug)")
    ap.add_argument("--dump", action="append", default=[], help="commande monitor à exécuter après le run (debug)")
    ap.add_argument("--keep-going", action="store_true", help="continue après un échec")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    gcc = toolchain.find("arm-none-eabi-gcc")
    renode = toolchain.find("renode")
    if gcc is None or renode is None:
        print("outil manquant : arm-none-eabi-gcc ou renode (voir `e7twin doctor`)", file=sys.stderr)
        return 2
    b = boardmod.load()
    if not REPL.exists():
        boardmod.write_repl(b)
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "gen").mkdir(exist_ok=True)
    (BUILD / "gen" / "e7_board.h").write_text(gen_header(b), encoding="utf-8")

    results: dict[str, bool] = {}
    todo = ([] if args.only_sdk else list(SCENARIOS)) + (SDK_SCENARIOS if (args.sdk or args.only_sdk or args.only) else [])
    for sc in todo:
        if args.only and sc.name != args.only:
            continue
        sc.pre += args.pre
        sc.dump += args.dump
        results[sc.name] = run_scenario(sc, b, gcc, renode, args.verbose)
        if not results[sc.name] and not args.keep_going:
            break
    print("\nRésumé : " + ", ".join(f"{k}={'OK' if v else 'ÉCHEC'}" for k, v in results.items()))
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
