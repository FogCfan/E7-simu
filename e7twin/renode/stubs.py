"""Génère l'overlay Renode des périphériques Python (stubs) de E7 Twin (agent 04).

`write(board, outdir)` produit `<outdir>/stubs.repl`, à charger APRÈS `e7.repl` (généré par l'agent 01) :
  * un `Python.PythonPeripheral` « regfile » par bloc `sysctrl` global du modèle de carte
    (CLKCTL, CGU, PINMUX, AON, VBAT, ANA, …) : lecture = dernière valeur écrite (| bits ready) ;
  * un MHU fonctionnel par lien (e7_mhu.py, voir `mhu_links`) : TX d'un cœur -> IRQ du RX de l'autre ;
  * un stub « uartdw » par UART : registres DesignWare au-delà du 16550 standard (USR : TX FIFO vide) ;
  * un `Python.PythonPeripheral` « counter » par bloc `counter` (S32K, REFCLK) : base de temps des
    `sys_busy_loop_*` du SDK, adossée au temps virtuel de Renode.
Aucune adresse ici : tout vient de `board.load()`. Les bits « ready/locked » découverts à l'exécution
sont dans `READY` ci-dessous (clé = nom du bloc -> {offset: masque}) et documentés dans
docs/SPIKE_RENODE.md / docs/FIDELITE.md.
"""

from __future__ import annotations

from pathlib import Path

from e7twin.board import NS16550_WINDOW, Board, renode_cpu_name

STUB_SCRIPT = Path(__file__).resolve().parent / "py" / "e7_stub.py"
MHU_SCRIPT = Path(__file__).resolve().parent / "py" / "e7_mhu.py"

# bloc -> {offset: bits forcés à 1 en lecture}. Rempli au fil des blocages du SDK (voir SPIKE_RENODE.md).
READY: dict[str, dict[int, int]] = {}


def _script_file(name: str, params: dict, outdir: Path, body: Path | None = None) -> Path:
    """Écrit `<outdir>/<bloc>.py` = paramètres + corps de e7_stub.py.

    Un fichier par bloc plutôt qu'un `exec` : dans Renode, le code `exec`é perd les méthodes
    d'extension de `self` (WarningLog, Log), et `filename:` exige un chemin absolu.
    """
    head = "".join(f"{k} = {v!r}\n" for k, v in params.items())
    path = outdir / f"{name}.py"
    path.write_text(
        # IronPython 2.7 (Renode 1.17) exige la déclaration d'encodage dès qu'il y a du non-ASCII
        "# -*- coding: utf-8 -*-\n# GENERE par e7twin/renode/stubs.py : ne pas editer.\n" + head
        + (body or STUB_SCRIPT).read_text(encoding="utf-8"),
        encoding="utf-8", newline="\n",
    )
    return path


def mhu_links(board: Board) -> dict[str, dict]:
    """Appariement des MHU par attribut `link` du modèle de carte.

    Renvoie {lien: {"tx": Peripheral | None, "rx": Peripheral | None}}. Un lien complet a son TX sur un
    cœur et son RX sur l'autre (ex. `m55hp_m55he_0` : TX HP 0x40090000, RX HE 0x40080000) ; les liens vers
    les A32 et le SE n'ont qu'une extrémité (l'autre cœur n'est pas simulé).
    """
    links: dict[str, dict] = {}
    for p in board.peripherals:
        if p.type in ("mhu_tx", "mhu_rx"):
            slot = links.setdefault(p.attrs["link"], {"tx": None, "rx": None})
            role = p.type[4:]
            if slot[role] is not None:
                raise ValueError(f"lien MHU {p.attrs['link']} : deux extrémités {role}")
            slot[role] = p
    return links


def _mhu_overlay(board: Board, outdir: Path) -> list[str]:
    L: list[str] = []
    for link, ends in mhu_links(board).items():
        eps, peer = [], {"tx": None, "rx": None}
        for role in ("tx", "rx"):
            p = ends[role]
            if p is None:
                continue
            core = p.visible_to[0]
            peer[role] = core
            eps.append({"role": role, "base": p.base, "core": core,
                        "nvic": "nvic_" + core.split("_", 1)[1], "irq": p.irq[core], "cpu": renode_cpu_name(core)})
        script = _script_file("mhu_" + link, {"LINK": link, "ENDPOINTS": eps, "PEER": peer}, outdir, MHU_SCRIPT)
        regs = ";\n".join(f"    sysbus new Bus.BusPointRegistration {{ address: 0x{e['base']:08x}; cpu: {e['cpu']} }}"
                          for e in eps)
        L += [f"mhu_{link}: Python.PythonPeripheral @ {{", regs, "}",
              "    size: 0x1000", "    initable: true", f'    filename: "{script.resolve().as_posix()}"', ""]
    return L


CDC_HW_VER = 0x00010000  # valeur inventée non nulle : le vrai CDC_HW_VER n'est pas sourcé (voir FIDELITE.md)


def _display_overlay(board: Board, outdir: Path) -> list[str]:
    """CDC200 (regfile : le pilote SDK n'attend rien) et DSI (regfile + DSI_PHY_STATUS prêt), depuis `board.display`.

    Lus par l'hôte via `Session.read_framebuffer()` ; aucun pixel n'est émulé ici (pas de balayage ni de mélange).
    """
    d = board.display
    if not d or "controller" not in d or "dsi" not in d:
        return []
    cdc, dsi = d["controller"], d["dsi"]
    regs = cdc["registers"]
    L: list[str] = []
    specs = [
        ("cdc200", cdc, {"FIXED": {regs["hw_ver"]: CDC_HW_VER, regs["lcnt"]: cdc["layers"]}, "READY": {}}),
        ("dsi", dsi, {"FIXED": {}, "READY": {dsi["registers"]["phy_status"]["offset"]:
                                             dsi["registers"]["phy_status"]["ready_bits"]}}),
    ]
    for name, blk, extra in specs:
        script = _script_file(name, {"KIND": "regfile", "BLOCK": name, **extra}, outdir)
        L += [f"{name}: Python.PythonPeripheral @ sysbus 0x{blk['base']:08x}",
              f"    size: 0x{blk['size']:x}", "    initable: true", f'    filename: "{script.resolve().as_posix()}"', ""]
    return L


def generate(board: Board, outdir: Path) -> str:
    """Texte de l'overlay .repl ; écrit au passage les scripts Python par bloc dans `outdir`."""
    outdir.mkdir(parents=True, exist_ok=True)
    L = [
        "// GÉNÉRÉ par e7twin/renode/stubs.py depuis board/ensemble_e7.yaml : ne pas éditer.",
        "// À charger après e7.repl. Voir docs/FIDELITE.md pour ce que ces stubs approximent.",
        "",
    ]
    L += _mhu_overlay(board, outdir)
    L += _display_overlay(board, outdir)
    for p in board.peripherals:
        if p.type == "uart16550":
            # registres DesignWare au-delà du 16550 standard (USR, TFL, SRR, DLF, ...)
            base, size = p.base + NS16550_WINDOW, p.size - NS16550_WINDOW
            script = _script_file(p.name + "_dw", {"KIND": "uartdw", "BLOCK": p.name + "_dw",
                                                   "WINDOW": NS16550_WINDOW}, outdir)
            L += [
                f"{p.name}_dw: Python.PythonPeripheral @ sysbus 0x{base:08x}",
                f"    size: 0x{size:x}",
                "    initable: true",
                f'    filename: "{script.resolve().as_posix()}"',
                "",
            ]
            continue
        if p.type == "sysctrl" and not p.local:
            params = {"KIND": "regfile", "BLOCK": p.name, "READY": READY.get(p.name, {}), "FIXED": {}}
        elif p.type == "counter":
            params = {"KIND": "counter", "BLOCK": p.name, "ROLE": p.attrs["role"],
                      "FREQ": p.attrs["freq_hz"]}
        else:
            continue
        script = _script_file(p.name, params, outdir)
        L += [
            f"{p.name}: Python.PythonPeripheral @ sysbus 0x{p.base:08x}",
            f"    size: 0x{p.size:x}",
            "    initable: true",
            f'    filename: "{script.resolve().as_posix()}"',
            "",
        ]
    return "\n".join(L)


def write(board: Board, outdir: Path) -> Path:
    """Génère `<outdir>/stubs.repl` (+ scripts) et renvoie son chemin."""
    repl = outdir / "stubs.repl"
    repl.write_text(generate(board, outdir), encoding="utf-8", newline="\n")
    return repl
