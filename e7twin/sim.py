"""Simulation Renode de la carte Alif Ensemble E7 (agent 04).

Contrat : docs/INTERFACES.md § e7twin.sim.

    with Session() as s:
        s.load_elf("m55_hp", "build/spike/hello_hp.elf")
        s.start()
        s.run_until("Hello World!", 30)

La session lance `renode --disable-gui -P <port libre>` et le pilote par le monitor telnet.
Chaque UART du modèle de carte est exposée sur un socket TCP (`CreateServerSocketTerminal`) lu dans un
thread. Points de fidélité : docs/FIDELITE.md ; blocages rencontrés : docs/SPIKE_RENODE.md.

Chemins : Renode est lancé avec le dépôt comme répertoire courant et reçoit des chemins relatifs, car le
tokenizer de son monitor ne sait pas traiter un chemin contenant un espace (`C:\\Dev\\E7 Twin`).
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from e7twin import board as boardmod
from e7twin import toolchain
from e7twin.renode import stubs

__all__ = ["Fault", "Image", "MhuEvent", "Session", "UnmappedAccess", "SimError", "UartLine", "assign_core", "main", "parse_atoc"]

REPO = Path(__file__).resolve().parents[1]
REPL = Path(__file__).resolve().parent / "renode" / "e7.repl"
WORK = REPO / "build" / "sim"

_TELNET_IAC = re.compile(rb"\xff[\xfb-\xfe].|\xff.")
_PROMPT = re.compile(rb"\((?:monitor|[\w.]+)\) $")
_MHU_EVT = re.compile(
    r"MHU_EVT t_us=(?P<t>\d+) link=(?P<link>\S+) kind=(?P<kind>\w+) src=(?P<src>\S+) dst=(?P<dst>\S+) "
    r"ch=(?P<ch>\d+) val=(?P<val>0x[0-9A-Fa-f]+)")
_UNMAPPED = re.compile(
    r"\[(?P<cpu>\w+): (?P<pc>0x[0-9A-Fa-f]+)\](?: \(tag: '[^']*'\))? (?P<acc>Read|Write)(?P<sz>Byte|Word|DoubleWord|QuadWord)"
    r" (?:from|to) non existing peripheral at (?P<addr>0x[0-9A-Fa-f]+)(?:, (?:returning|value) (?P<val>0x[0-9A-Fa-f]+))?")
_SIZES = {"Byte": 1, "Word": 2, "DoubleWord": 4, "QuadWord": 8}
_VTIME = re.compile(r"Elapsed Virtual Time:\s*(\d+):(\d+):(\d+)\.(\d+)")


class SimError(RuntimeError):
    """Erreur de pilotage Renode, d'environnement ou d'image."""


@dataclass
class UartLine:
    t_virtual_s: float   # dernier temps virtuel échantillonné (résolution ~0,1 s réel), pas exact
    core: str            # cœur auquel la ligne est attribuée (heuristique, cf. Session._core_of)
    uart: str            # nom du périphérique, ex. "uart4"
    text: str


@dataclass
class MhuEvent:
    t_virtual_s: float
    kind: str      # "send" (écriture CH_SET côté TX) | "ack" (CH_CLR côté RX, acquittement)
    link: str      # ex. "m55hp_m55he_0"
    src: str       # cœur émetteur du message (pour "ack" : celui qui acquitte)
    dst: str       # cœur destinataire, ou "none" si l'autre extrémité n'est pas simulée (A32, SE)
    channel: int
    value: int


@dataclass
class UnmappedAccess:
    """Accès bus à une adresse sans périphérique ni mémoire (Renode : journalisé, lecture = 0, écriture perdue)."""
    core: str
    pc: int | None
    access: str          # "read" | "write"
    size: int            # octets
    addr: int
    value: int | None    # valeur écrite (écriture) ou renvoyée (lecture)
    fault: bool          # True si l'adresse est hors de tout espace de périphériques/système : la carte lèverait un BusFault

    def __str__(self) -> str:
        pc = f" PC={self.pc:#x}" if self.pc is not None else ""
        return f"unmapped {self.access}{self.size * 8} {self.core}{pc} addr={self.addr:#010x}"


@dataclass
class Fault:
    core: str
    kind: str      # "hardfault" (CFSR/HFSR non nuls) | "lockup" | "abort" (Renode : exécution hors mémoire) | "unmapped" (accès hors mémoire/périphériques)
    pc: int | None
    cfsr: int | None = None
    hfsr: int | None = None
    message: str = ""

    def __str__(self) -> str:
        pc = f" PC={self.pc:#x}" if self.pc is not None else ""
        cf = f" CFSR={self.cfsr:#x}" if self.cfsr is not None else ""
        hf = f" HFSR={self.hfsr:#x}" if self.hfsr is not None else ""
        return f"{self.kind} {self.core}{pc}{cf}{hf} {self.message}".rstrip()


@dataclass
class Image:
    """Une image à charger : ELF (segments par adresse de chargement) ou .bin brut."""

    core: str                 # m55_hp | m55_he
    path: Path
    boot: bool = True         # démarre le cœur
    load_address: int | None = None   # obligatoire pour un .bin ; ELF : déduit des segments
    vtor: int | None = None   # base de la table des vecteurs (MRAM) ; défaut : adresse de chargement mini

    @property
    def is_elf(self) -> bool:
        return self.path.suffix.lower() in (".elf", ".axf", "")


# ------------------------------------------------------------------ lecture ELF / ATOC

def _elf_segments(path: Path) -> list[tuple[int, int]]:
    """[(paddr, filesz)] des segments PT_LOAD non vides (adresse de CHARGEMENT, pas d'exécution)."""
    from elftools.elf.elffile import ELFFile

    with path.open("rb") as f:
        elf = ELFFile(f)
        return [(s["p_paddr"], s["p_filesz"]) for s in elf.iter_segments()
                if s["p_type"] == "PT_LOAD" and s["p_filesz"] > 0]


def _elf_symbol(path: Path, name: str) -> int | None:
    from elftools.elf.elffile import ELFFile

    with path.open("rb") as f:
        sec = ELFFile(f).get_section_by_name(".symtab")
        if sec is None:
            return None
        for sym in sec.get_symbol_by_name(name) or []:
            return sym["st_value"] & ~1
    return None


def assign_core(path: str | Path, b: boardmod.Board | None = None) -> tuple[str, int]:
    """(cœur, base de l'image) d'un ELF d'après son adresse de chargement : slot MRAM/OSPI HP ou HE.

    La base est le plus petit paddr tombant dans le slot (là où se trouve la table des vecteurs).
    """
    b = b or boardmod.load()
    path = Path(path)
    if not path.is_file():
        raise SimError(f"ELF introuvable : {path}")
    segs = _elf_segments(path)
    hits: dict[str, list[int]] = {}
    for paddr, _ in segs:
        for s in b.atoc_slots:
            if s.base <= paddr < s.end:
                hits.setdefault(s.core, []).append(paddr)
    if not hits:
        raise SimError(f"{path.name} : aucun segment chargé dans un slot ATOC MRAM/OSPI "
                       f"({', '.join(f'{s.name}@{s.base:#x}' for s in b.atoc_slots)})")
    if len(hits) > 1:
        raise SimError(f"{path.name} : segments dans les slots de plusieurs cœurs {sorted(hits)}")
    core = next(iter(hits))
    return core, min(hits[core])


def parse_atoc(path: str | Path, b: boardmod.Board | None = None) -> list[Image]:
    """Lit un JSON ATOC (format SETOOLS, docs/E7_FAITS.md § Packaging ATOC).

    Chaque entrée : {"binary", "mramAddress" | "loadAddress", "cpu_id", "flags": ["boot", "load"]}.
    Les entrées A32 (inertes en v1) sont ignorées. Les `binary` sont relatifs au dossier du JSON ;
    un `.elf` est chargé par segments, un `.bin` à `mramAddress`/`loadAddress`.
    """
    b = b or boardmod.load()
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SimError(f"ATOC illisible ({path}) : {e}") from e
    entries = data.get("entries", data) if isinstance(data, dict) else {}
    if not isinstance(entries, dict) or not entries:
        raise SimError(f"ATOC {path} : aucune entrée")
    images: list[Image] = []
    for name, e in entries.items():
        if not isinstance(e, dict) or "cpu_id" not in e:
            continue
        core = str(e["cpu_id"]).lower()
        if core not in ("m55_hp", "m55_he"):
            continue  # A32 / SE : non exécutés en v1
        binary = path.parent / str(e.get("binary", ""))
        if not binary.is_file():
            raise SimError(f"ATOC {name} : binaire introuvable : {binary}")
        addr_s = e.get("mramAddress", e.get("loadAddress"))
        addr = int(addr_s, 0) if isinstance(addr_s, str) else addr_s
        flags = [str(f).lower() for f in e.get("flags", [])]
        img = Image(core, binary, boot="boot" in flags, load_address=addr, vtor=addr)
        if not img.is_elf and addr is None:
            raise SimError(f"ATOC {name} : .bin sans mramAddress/loadAddress")
        images.append(img)
    if not images:
        raise SimError(f"ATOC {path} : aucune image M55 (HP/HE)")
    return images


def mpu_prime(b: boardmod.Board, cpu: str) -> list[str]:
    """Commandes monitor armant le MPU d'un cœur pour rendre MRAM/XIP exécutables.

    tlib (Renode 1.17) interdit l'exécution à toute adresse >= 0x80000000 tant qu'aucune région MPU ne la
    permet (IACCVIOL à la 1re instruction), alors que la carte exécute depuis la MRAM avec le MPU éteint.
    Une région RWX par région MRAM/XIP (attr 0 = Normal WB), puis MPU_CTRL = ENABLE | PRIVDEFENA.
    Voir docs/SPIKE_RENODE.md (blocage 1) et docs/FIDELITE.md.
    """
    def w(addr: int, val: int) -> str:
        return f"sysbus WriteDoubleWord {addr:#x} {val:#x} {cpu}"

    ctrl, rnr, rbar, rlar, mair0 = 0xE000ED94, 0xE000ED98, 0xE000ED9C, 0xE000EDA0, 0xE000EDC0
    regs = [r for r in b.memories if r.kind in ("mram", "xip") and r.alias_of is None]
    cmds = [w(mair0, 0xFF)]
    for i, r in enumerate(regs):
        cmds += [w(rnr, i), w(rbar, r.base | (0b01 << 1)), w(rlar, ((r.end - 1) & ~0x1F) | 1)]
    cmds.append(w(ctrl, 0b101))
    return cmds


# ------------------------------------------------------------------ pilotage Renode

_LIVE: set["Monitor"] = set()


def _kill_live() -> None:
    """Filet de sécurité à la sortie de l'interpréteur : aucun Renode orphelin si `close()` n'a pas eu lieu."""
    for m in list(_LIVE):
        try:
            m.proc.kill()
        except OSError:
            pass


atexit.register(_kill_live)


class Monitor:
    """Client du monitor Renode (telnet). `cmd()` renvoie le texte affiché, sans écho ni prompt."""

    def __init__(self, renode: Path, port: int, log: Path) -> None:
        self.port = port
        self.lock = threading.Lock()
        self.log_path = log
        self.log = log.open("wb")
        # Renode réécrit TOUT l'historique des commandes (%APPDATA%\renode\history, partagé entre instances) à
        # chaque commande monitor ; sous un sondage soutenu, l'écriture finit par échouer (IOException « fichier
        # utilisé par un autre processus ») et Renode meurt. Config propre à la session => historique propre.
        args = [str(renode), "--disable-gui", "--plain", "-P", str(port)]
        if not os.environ.get("E7TWIN_SHARED_HISTORY"):  # interrupteur de debug : reproduit le bug d'origine
            cfg = log.parent / "renode.config"
            cfg.write_text(f"[general]\nhistory-path = {log.parent / 'history'}\n", encoding="utf-8")
            args[3:3] = ["--config", str(cfg)]
        try:
            self.proc = subprocess.Popen(args, cwd=REPO, stdout=self.log, stderr=subprocess.STDOUT)
        except OSError as e:
            self.log.close()
            raise SimError(f"impossible de lancer Renode ({renode}) : {e}") from e
        _LIVE.add(self)
        self.sock: socket.socket | None = None
        for _ in range(200):
            if self.proc.poll() is not None:
                break
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                time.sleep(0.1)
        if self.sock is None:
            self.close()
            raise SimError(f"monitor Renode injoignable sur le port {port} (log : {log})")
        try:
            self._read_prompt(60.0)  # bannière + prompt : Renode est prêt (et c'est bien lui qui répond)
        except SimError:
            self.close()
            raise

    def _read_prompt(self, timeout: float) -> str:
        buf = b""
        end = time.time() + timeout
        self.sock.settimeout(1.0)
        while time.time() < end:
            try:
                c = self.sock.recv(65536)
            except socket.timeout:
                if self.proc.poll() is not None:
                    raise SimError("Renode s'est arrêté (voir le log)")
                continue
            except OSError as e:
                raise SimError(f"connexion au monitor perdue : {e}") from e
            if not c:
                raise SimError("Renode a fermé le monitor (voir le log)")
            buf += c
            if _PROMPT.search(buf):
                break
        else:
            raise SimError(f"délai dépassé en attendant le prompt Renode ({timeout:.0f} s)")
        return _TELNET_IAC.sub(b"", buf).decode("utf-8", "replace")

    def cmd(self, line: str, timeout: float = 120.0) -> str:
        with self.lock:
            if self.sock is None:
                raise SimError("session fermée")
            self.sock.sendall(line.encode("utf-8") + b"\n")
            raw = self._read_prompt(timeout)
        self.log.write(f">>> {line}\n{raw}\n".encode("utf-8", "replace"))
        self.log.flush()
        lines = raw.replace("\r", "").split("\n")
        body = "\n".join(lines[1:-1]).strip()  # 1re ligne = écho, dernière = prompt
        if "There was an error executing command" in body or "Error E" in body:
            raise SimError(f"{line!r} -> {body}")
        return body

    def close(self) -> None:
        _LIVE.discard(self)
        try:
            if self.sock is not None:
                try:
                    self.sock.sendall(b"quit\n")
                except OSError:
                    pass
                self.sock.close()
                self.sock = None
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        finally:
            self.log.close()


class _UartReader(threading.Thread):
    def __init__(self, session: "Session", name: str, port: int) -> None:
        super().__init__(daemon=True, name=f"uart-{name}")
        self.session, self.uart, self.port = session, name, port
        self.sock: socket.socket | None = None
        for _ in range(50):
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                time.sleep(0.1)
        if self.sock is None:
            raise SimError(f"terminal socket {name} (port {port}) injoignable")
        self.sock.settimeout(0.2)
        self.partial = ""
        self.stopped = False

    def run(self) -> None:
        pending = b""
        while not self.stopped:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            pending += chunk
            # retire la négociation telnet, garde un éventuel début de séquence IAC pour le tour suivant
            tail = b""
            i = pending.rfind(b"\xff")
            if i != -1 and len(pending) - i < 3:
                pending, tail = pending[:i], pending[i:]
            text = _TELNET_IAC.sub(b"", pending).decode("utf-8", "replace")
            pending = tail
            self.session._on_uart_text(self, text)

    def stop(self) -> None:
        self.stopped = True
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """Une machine Renode E7 pilotable (contrat docs/INTERFACES.md)."""

    def __init__(self, board: boardmod.Board | None = None, *, renode: Path | None = None,
                 port: int | None = None) -> None:
        self.board = board or boardmod.load()
        self._renode = Path(renode) if renode else toolchain.find("renode")
        self._port = port
        self._images: list[Image] = []
        self._mon: Monitor | None = None
        self._readers: dict[str, _UartReader] = {}
        self._lines: list[UartLine] = []
        self._text: dict[str, str] = {}
        self._lock = threading.Lock()
        self._vtime = 0.0
        self._booted = False
        self._running = False
        self._painted: dict[str, tuple[int, int]] = {}
        self._log_off = 0
        self._scan_lock = threading.Lock()
        self._mhu_q: list[MhuEvent] = []
        self._aborts: list[str] = []
        self._unmapped_q: list[UnmappedAccess] = []
        self._unmapped_faults: list[UnmappedAccess] = []
        self._periph_blocks: set[int] | None = None
        self._t0 = 0.0  # temps virtuel (brut) au dé-halt : la machine tourne à vide pendant l'armement du MPU
        self._workdir: Path | None = None
        self._profiles: dict[str, Path] = {}  # cœur -> fichier CollapsedStack du profiler Renode
        self._cpus = {c.id: boardmod.renode_cpu_name(c.id) for c in self.board.cores if c.executable}

    # -- cycle de vie
    def __enter__(self) -> "Session":
        try:
            self._open()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _open(self) -> None:
        if self._mon is not None:
            return
        if self._renode is None or not Path(self._renode).is_file():
            raise SimError("Renode introuvable (voir `e7twin doctor`)")
        # Démarrage du monitor : le port libre choisi peut être pris entre-temps (autre session/processus) ; on
        # réessaie avec un autre port (3 essais) sauf si l'appelant en impose un. Seul le DÉMARRAGE est rejoué :
        # une commande qui échoue ensuite (vrai échec fonctionnel) n'est jamais rejouée.
        last: SimError | None = None
        for attempt in range(1 if self._port else 3):
            port = self._port or _free_port()
            self._workdir = WORK / f"s{os.getpid()}_{port}"
            self._workdir.mkdir(parents=True, exist_ok=True)
            try:
                self._mon = Monitor(Path(self._renode), port, self._workdir / "renode.log")
                break
            except SimError as e:
                last = e
        else:
            raise last
        m = self._mon
        m.cmd('mach create "e7"')
        m.cmd(f"machine LoadPlatformDescription @{self._rel(REPL)}")
        m.cmd(f"machine LoadPlatformDescription @{self._rel(stubs.write(self.board, self._workdir / 'stubs'))}")
        # Lecture d'un stub avant le boot. Cycle 4 : mesurée SANS effet sur le palier de ~5 s (voir SPIKE_RENODE.md
        # « Palier de boot ») ; conservée, inoffensive (quelques ms).
        warm = next((p for p in self.board.peripherals if p.type == "counter" and p.attrs.get("role") == "read"), None)
        if warm is not None:
            m.cmd(f"sysbus ReadDoubleWord {warm.base:#x}")
        for cid, cpu in self._cpus.items():
            # approximation CPI = 1 : 1 instruction par cycle à la fréquence du modèle (400 / 160 MHz).
            # Renode vaut 100 MIPS par défaut, sans lien avec la fréquence.
            m.cmd(f"{cpu} PerformanceInMips {self.board.core(cid).freq_hz // 1_000_000}")
            m.cmd(f"{cpu} IsHalted true")  # rien ne s'exécute avant l'armement du MPU / le dé-halt
        for p in self.board.peripherals:
            if p.type == "uart16550":
                port_u = self._create_terminal(p.name)
                m.cmd(f"connector Connect sysbus.{p.name} term_{p.name}")
                r = _UartReader(self, p.name, port_u)
                r.start()
                self._readers[p.name] = r

    def _create_terminal(self, uart: str) -> int:
        """Crée le terminal socket de l'UART sur un port libre ; si Renode ne peut pas s'y lier (port pris
        entre le choix et la liaison), nouvel essai avec un autre port (5 essais)."""
        err: SimError | None = None
        for _ in range(5):
            port = _free_port()
            try:
                self._mon.cmd(f'emulation CreateServerSocketTerminal {port} "term_{uart}"')
                return port
            except SimError as e:
                err = e
                try:  # le terminal a pu être créé à moitié : on le retire avant de réessayer
                    self._mon.cmd(f'emulation RemoveTerminal "term_{uart}"')
                except SimError:
                    pass
        raise SimError(f"terminal socket {uart} : aucun port utilisable ({err})")

    def close(self) -> None:
        if self._mon is not None:
            self._disable_profilers()
        for r in self._readers.values():
            r.stop()
        self._readers.clear()
        if self._mon is not None:
            self._mon.close()
            self._mon = None
        self._running = False

    # -- chemins
    def _rel(self, p: Path) -> str:
        p = Path(p).resolve()
        try:
            return p.relative_to(REPO).as_posix()
        except ValueError:  # hors dépôt : copie dans le dossier de travail (chemin relatif sans espace)
            dst = self._workdir / p.name
            shutil.copyfile(p, dst)
            return dst.relative_to(REPO).as_posix()

    # -- chargement
    def load_elf(self, core: str, path: str | Path) -> None:
        self.load_image(Image(core, Path(path)))

    def load_image(self, img: Image) -> None:
        if img.core not in self._cpus:
            raise SimError(f"cœur non exécutable : {img.core!r} (exécutables : {sorted(self._cpus)})")
        if not Path(img.path).is_file():
            raise SimError(f"image introuvable : {img.path}")
        self._open()
        cpu = self._cpus[img.core]
        m = self._mon
        if img.is_elf:
            core, base = assign_core(img.path, self.board)
            if core != img.core:
                raise SimError(f"{Path(img.path).name} est lié pour {core} (slot MRAM) mais chargé sur {img.core}")
            # useVirtualAddress=false : adresses de CHARGEMENT (LMA) ; les ELF du SDK sont liés en TCM
            m.cmd(f"sysbus LoadELF @{self._rel(Path(img.path))} false {cpu}")
            img.vtor = img.vtor if img.vtor is not None else base
        else:
            if img.load_address is None:
                raise SimError(f"{img.path} : .bin sans adresse de chargement")
            m.cmd(f"sysbus LoadBinary @{self._rel(Path(img.path))} {img.load_address:#x} {cpu}")
            img.vtor = img.vtor if img.vtor is not None else img.load_address
        m.cmd(f"{cpu} VectorTableOffset {img.vtor:#x}")
        self._images = [i for i in self._images if i.core != img.core] + [img]
        self._booted = False
        self._painted.pop(img.core, None)
        if img.is_elf:
            self.paint_stack(img.core)  # motif 0xDEADBEEF pour mesurer le watermark de pile

    # -- profiler Renode (CollapsedStack ; chemin absolu entre guillemets, sans « @ »)
    def enable_profiler(self, core: str) -> Path:
        self._open()
        path = self._workdir / f"prof_{core}.txt"
        self._mon.cmd(f'{self._cpus[core]} EnableProfiler CollapsedStack "{path.as_posix()}"')
        self._profiles[core] = path
        return path

    def flush_profiler(self, core: str) -> Path | None:
        """Vide le tampon du profiler dans le fichier et le renvoie (None si non activé)."""
        path = self._profiles.get(core)
        if path is not None:
            self._mon.cmd(f"{self._cpus[core]} FlushProfiler")
        return path

    def disable_profiler(self, core: str) -> None:
        if self._profiles.pop(core, None) is not None:
            self._mon.cmd(f"{self._cpus[core]} DisableProfiler")

    def _disable_profilers(self) -> None:
        """DisableProfiler sur tous les cœurs profilés ; erreurs ignorées (moniteur mort, profiler déjà arrêté)."""
        for core in list(self._profiles):
            try:
                self.disable_profiler(core)
            except Exception:
                self._profiles.pop(core, None)

    # -- exécution
    def _boot(self) -> None:
        """MPU armé, contournements SDK, puis dé-halt des cœurs `boot` (SP/PC lus dans la table des vecteurs)."""
        m = self._mon
        m.cmd("start")  # cœurs encore halted : la machine tourne mais rien ne s'exécute
        started = [i for i in self._images if i.boot]
        for img in started:
            cpu = self._cpus[img.core]
            for line in mpu_prime(self.board, cpu):
                m.cmd(line)
            if img.is_elf:
                # MPU_Setup() du SDK commence par désarmer le MPU puis continue d'exécuter en MRAM : fault dans
                # tlib (cf. mpu_prime). On le court-circuite : sa 1re instruction (en MRAM simulée) devient `bx lr`
                # (0x4770). Pas de hook Python : le 1er hook initialisait IronPython pendant ~5 s réelles, avec le
                # temps virtuel figé, au milieu du boot. FIDELITE.md.
                addr = _elf_symbol(Path(img.path), "MPU_Setup")
                if addr is not None:
                    m.cmd(f"sysbus WriteWord {addr:#x} 0x4770")
        self._t0 = 0.0
        self._t0 = self.virtual_time()  # origine du temps virtuel = instant du dé-halt
        self._vtime = 0.0
        for img in started:
            m.cmd(f"{self._cpus[img.core]} IsHalted false")
        self._booted = True

    def start(self) -> None:
        self._open()
        if not self._images:
            raise SimError("aucune image chargée (load_elf / load_image)")
        if not self._booted:
            self._boot()
        else:
            self._mon.cmd("start")
        self._running = True

    def pause(self) -> None:
        if self._mon is not None:
            self._mon.cmd("pause")
            self._running = False

    def reset(self) -> None:
        """Remet la machine à zéro et recharge les images (état identique à un premier `start`)."""
        self._open()
        m = self._mon
        m.cmd("pause")
        self._disable_profilers()
        m.cmd("machine Reset")
        for cpu in self._cpus.values():
            m.cmd(f"{cpu} IsHalted true")
        imgs, self._images = self._images, []
        for img in imgs:
            self.load_image(img)
        with self._lock:
            self._lines.clear()
            self._text = {k: "" for k in self._text}
        self._running = False

    def read_mem(self, addr: int, size: int, core: str | None = None) -> bytes:
        """Lit `size` octets à `addr`. Sans `core` : vue du bus système (adresses globales, y compris les
        alias des TCM) ; avec `core` : vue de ce cœur (TCM locales à 0x0 / 0x20000000)."""
        self._open()
        ctx = f" {self._cpus[core]}" if core else ""
        out = bytearray()
        while len(out) < size:
            n = min(4096, size - len(out))
            txt = self._mon.cmd(f"sysbus ReadBytes {addr + len(out):#x} {n}{ctx}")
            got = bytes(int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]{1,2}\b", txt))
            if len(got) != n:
                raise SimError(f"lecture mémoire invalide à {addr + len(out):#x} ({len(got)}/{n} octets)")
            out += got
        return bytes(out)

    # -- mesures (agent 05) : instructions, pile, MHU, fautes
    def cpu_stats(self, core: str) -> dict:
        """{instructions, halted_or_wfi, pc} d'un cœur, d'après l'état du CPU Renode.

        `instructions` = ExecutedInstructions (ne croît pas en WFI) ; `halted_or_wfi` = cœur halted, ou PC sur/juste
        après un WFI/WFE (heuristique : Renode 1.17 n'expose pas l'état WFI en lecture)."""
        self._open()
        cpu, m = self._cpus[core], self._mon
        instr = int(m.cmd(f"{cpu} ExecutedInstructions"), 0)
        pc = int(m.cmd(f"{cpu} PC"), 0)
        halted = m.cmd(f"{cpu} IsHalted").strip().lower() == "true"
        if not halted:
            for a in (pc, pc - 2):
                try:
                    hw = int(m.cmd(f"sysbus ReadWord {a:#x} {cpu}"), 0)
                except SimError:
                    continue
                if hw in (0xBF30, 0xBF20):  # WFI / WFE
                    halted = True
        return {"instructions": instr, "halted_or_wfi": halted, "pc": pc}

    STACK_PATTERN = 0xDEADBEEF

    def _stack_bounds(self, core: str) -> tuple[int, int] | None:
        """(__StackLimit, SP initial) du cœur, lus dans l'ELF et la table des vecteurs ; None si absents."""
        img = next((i for i in self._images if i.core == core), None)
        if img is None or not img.is_elf:
            return None
        limit = _elf_symbol(Path(img.path), "__StackLimit")
        if limit is None:
            return None
        sp = int.from_bytes(self.read_mem(img.vtor, 4), "little")
        return (limit, sp) if limit < sp else None

    def paint_stack(self, core: str) -> bool:
        """Écrit 0xDEADBEEF entre `__StackLimit` et le SP initial (appelé au chargement). False si l'ELF
        n'a pas de `__StackLimit` (ex. firmwares du spike)."""
        b = self._stack_bounds(core)
        if b is None:
            return False
        limit, sp = b
        f = self._workdir / f"stack_{core}.bin"
        f.write_bytes(self.STACK_PATTERN.to_bytes(4, "little") * ((sp - limit) // 4))
        self._mon.cmd(f"sysbus LoadBinary @{self._rel(f)} {limit:#x} {self._cpus[core]}")
        self._painted[core] = (limit, sp)
        return True

    def stack_watermark(self, core: str) -> int | None:
        """Octets de pile utilisés (plus haute profondeur atteinte) depuis la peinture, ou None si non peinte."""
        if core not in self._painted:
            return None
        limit, sp = self._painted[core]
        data = self.read_mem(limit, sp - limit, core)
        pat = self.STACK_PATTERN.to_bytes(4, "little")
        for i in range(0, len(data), 4):
            if data[i:i + 4] != pat:
                return sp - (limit + i)
        return 0

    def _scan_log(self) -> None:
        """Lit les lignes NOUVELLES du log Renode (incrémental) et en extrait événements MHU, aborts CPU et accès
        non mappés. Le log peut devenir très gros (récursion folle) : on ne le relit jamais en entier."""
        try:
            with (self._workdir / "renode.log").open("rb") as f:
                with self._scan_lock:
                    f.seek(self._log_off)
                    chunk = f.read()
                    end = chunk.rfind(b"\n") + 1  # n'avale que des lignes complètes
                    self._log_off += end
                    text = chunk[:end].decode("utf-8", "replace")
                    for ln in text.splitlines():
                        if "MHU_EVT" in ln:
                            m = _MHU_EVT.search(ln)
                            if m:
                                self._mhu_q.append(MhuEvent(int(m["t"]) / 1e6 - self._t0, m["kind"], m["link"], m["src"],
                                                            m["dst"], int(m["ch"]), int(m["val"], 16)))
                        elif "CPU abort" in ln:
                            self._aborts.append(ln.strip())
                        elif "non existing peripheral" in ln:
                            u = self._parse_unmapped(ln)
                            if u is not None:
                                if len(self._unmapped_q) < 100_000:
                                    self._unmapped_q.append(u)
                                if u.fault and len(self._unmapped_faults) < 1000:
                                    self._unmapped_faults.append(u)
        except (OSError, TypeError):
            return

    def _is_peripheral_space(self, addr: int) -> bool:
        """Adresse dans un espace où des registres existent sur la carte sans être tous modélisés : zone système
        ARM (>= 0xE0000000), région « Peripheral » du plan mémoire par défaut Armv8-M (0x40000000-0x5FFFFFFF),
        ou bloc de 16 Mo contenant un périphérique du modèle de carte (ex. 0x1A... : horloges/PINMUX)."""
        if self._periph_blocks is None:
            self._periph_blocks = {p.base >> 24 for p in self.board.peripherals}
        return addr >= 0xE000_0000 or 0x4000_0000 <= addr < 0x6000_0000 or (addr >> 24) in self._periph_blocks

    def _parse_unmapped(self, ln: str) -> UnmappedAccess | None:
        m = _UNMAPPED.search(ln)
        if m is None:
            return None
        addr = int(m["addr"], 16)
        core = next((c for c, n in self._cpus.items() if n == m["cpu"]), m["cpu"])
        return UnmappedAccess(core, int(m["pc"], 16), m["acc"].lower(), _SIZES[m["sz"]], addr,
                              int(m["val"], 16) if m["val"] else None, not self._is_peripheral_space(addr))

    def mhu_events(self) -> list[MhuEvent]:
        """Messages MHU journalisés depuis le dernier appel (la file est vidée)."""
        self._scan_log()
        with self._scan_lock:
            out, self._mhu_q = self._mhu_q, []
        return out

    def unmapped_events(self) -> list[UnmappedAccess]:
        """Accès à une adresse non mappée vus depuis le dernier appel (file vidée) : source des événements
        `unmapped` de la timeline. Seuls ceux hors espace périphériques/système (`fault=True`) comptent comme faute."""
        self._scan_log()
        with self._scan_lock:
            out, self._unmapped_q = self._unmapped_q, []
        return out

    def virtual_time(self) -> float:
        """Temps virtuel écoulé (s), interrogé via `machine GetTimeSourceInfo`."""
        if self._mon is None:
            return self._vtime
        mt = _VTIME.search(self._mon.cmd("machine GetTimeSourceInfo"))
        if mt:
            h, mi, s, ns = mt.groups()
            self._vtime = int(h) * 3600 + int(mi) * 60 + int(s) + int(ns.ljust(9, "0")[:9]) / 1e9 - self._t0
        return self._vtime

    # -- UART
    def _core_of(self, uart: str) -> str:
        """Cœur auquel attribuer une ligne : LPUART -> m55_he ; sinon le cœur démarré (HP si les deux)."""
        if uart == "lpuart":
            return "m55_he"
        booted = [i.core for i in self._images if i.boot]
        return "m55_hp" if (not booted or "m55_hp" in booted) else booted[0]

    def _on_uart_text(self, reader: _UartReader, text: str) -> None:
        with self._lock:
            self._text[reader.uart] = self._text.get(reader.uart, "") + text
            buf = reader.partial + text
            *done, reader.partial = buf.split("\n")
            for ln in done:
                self._lines.append(UartLine(self._vtime, self._core_of(reader.uart), reader.uart,
                                            ln.rstrip("\r")))

    def uart_lines(self) -> list[UartLine]:
        with self._lock:
            return list(self._lines)

    def uart_text(self, uart: str | None = None) -> str:
        """Texte brut reçu (toutes les UART, ou une seule), y compris la ligne en cours."""
        with self._lock:
            if uart:
                return self._text.get(uart, "")
            return "".join(self._text.values())

    def _aborted(self) -> list[str]:
        """Aborts CPU et accès non mappés fatals vus dans le log (peu coûteux : lecture incrémentale du log)."""
        self._scan_log()
        with self._scan_lock:
            return self._aborts + [str(u) for u in self._unmapped_faults[:1]]

    def faults(self) -> list[Fault]:
        """Fautes détectées : abort Renode (code hors mémoire), lockup, ou HardFault/fautes de bus-usage-mémoire
        (CFSR / HFSR non nuls, lus à `0xE000ED28` / `0xE000ED2C` dans la vue de chaque cœur démarré)."""
        self._scan_log()
        out = []
        with self._scan_lock:
            aborts, unm = list(self._aborts), list(self._unmapped_faults)
        for u in unm[:1]:  # le premier accès fautif suffit ; les suivants sont des conséquences
            out.append(Fault(u.core, "unmapped", u.pc, message=f"{u.access}{u.size * 8} à {u.addr:#010x} "
                             f"(hors mémoire/périphériques ; BusFault sur la carte, {len(unm)} accès journalisés)"))
        for ln in aborts:
            m = re.search(r"\] (\w+): CPU abort \[PC=(0x[0-9A-Fa-f]+)\]: (.*)", ln)
            core = next((c for c, n in self._cpus.items() if m and n == m[1]), "?")
            out.append(Fault(core, "abort", int(m[2], 16) if m else None, message=m[3] if m else ln))
        if self._mon is not None and self._booted:
            for img in self._images:
                if not img.boot:
                    continue
                cpu = self._cpus[img.core]
                try:
                    cfsr = int(self._mon.cmd(f"sysbus ReadDoubleWord 0xE000ED28 {cpu}"), 0)
                    hfsr = int(self._mon.cmd(f"sysbus ReadDoubleWord 0xE000ED2C {cpu}"), 0)
                    pc = int(self._mon.cmd(f"{cpu} PC"), 0)
                except SimError:
                    continue
                if cfsr or hfsr:
                    out.append(Fault(img.core, "hardfault", pc, cfsr, hfsr))
        return out

    def run_until(self, pattern: str, timeout: float) -> bool:
        """True si `pattern` (regex, sinon texte littéral) apparaît sur une UART avant `timeout` s réelles."""
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        end = time.time() + timeout
        while True:
            with self._lock:
                texts = list(self._text.values())
            if any(rx.search(t) for t in texts):
                return True
            if time.time() >= end or self._aborted():
                return False
            time.sleep(0.05)


_PORT_LOCK = threading.Lock()
_PORTS_GIVEN: set[int] = set()


def _free_port() -> int:
    """Port TCP libre. Jamais deux fois le même dans ce processus (le port est libéré avant d'être réutilisé par
    Renode : sans cette garde, deux appels rapprochés peuvent renvoyer le même numéro) ; la course avec un AUTRE
    processus est traitée par les nouveaux essais de `Session._open` / `_create_terminal`."""
    with _PORT_LOCK:
        for _ in range(50):
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            if port not in _PORTS_GIVEN:
                _PORTS_GIVEN.add(port)
                return port
    raise SimError("aucun port TCP libre")


# ------------------------------------------------------------------ point d'entrée CLI

def main(elfs: list[str], hp: str | None, he: str | None, atoc: str | None,
         headless: bool, expect: str | None, timeout: float) -> int:
    """`e7twin run` : 0 si `expect` apparaît sur une UART avant `timeout`, 1 sinon (ou abort CPU),
    2 en cas d'erreur d'environnement (Renode absent, ELF introuvable, image invalide)."""
    out = sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8", errors="replace")
    try:
        b = boardmod.load()
        images: list[Image] = []
        if atoc:
            images += parse_atoc(atoc, b)
        for core, p in (("m55_hp", hp), ("m55_he", he)):
            if p:
                images.append(Image(core, Path(p)))
        for p in elfs or []:
            core, _ = assign_core(p, b)
            images.append(Image(core, Path(p)))
        if not images:
            print("erreur : aucune image (elfs, --hp, --he ou --atoc)", file=sys.stderr)
            return 2
        if not headless:
            print("note : seul le mode headless est disponible (pas d'interface Renode)", file=sys.stderr)
        seen = {}
        for img in images:
            if img.core in seen:
                print(f"erreur : deux images pour {img.core}", file=sys.stderr)
                return 2
            seen[img.core] = img
        with Session(b) as s:
            for img in images:
                s.load_image(img)
            s.start()
            shown = 0
            end = time.time() + timeout
            found = False
            while time.time() < end:
                lines = s.uart_lines()
                for ln in lines[shown:]:
                    print(f"[{ln.uart}] {ln.text}", flush=True)
                shown = len(lines)
                if expect is not None and s.run_until(expect, 0):
                    found = True
                    break
                if s._aborted():
                    break
                time.sleep(0.05)
            time.sleep(0.15)  # laisse arriver la fin de la ligne en cours
            lines = s.uart_lines()
            for ln in lines[shown:]:
                print(f"[{ln.uart}] {ln.text}", flush=True)
            for uart, r in sorted(s._readers.items()):
                if r.partial.strip():  # ligne non terminée par un saut de ligne
                    print(f"[{uart}] {r.partial.rstrip()}", flush=True)
            for f in s.faults():
                print(f"FAULT : {f}", file=sys.stderr)
            if expect is None:
                return 1 if s.faults() else 0
            return 0 if (found or s.run_until(expect, 0)) else 1
    except (SimError, boardmod.BoardError, FileNotFoundError) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main([], None, None, None, True, None, 5))
