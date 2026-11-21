"""Serveur FastAPI + WebSocket d'E7 Twin (agent 05). Contrat : docs/INTERFACES.md."""

from __future__ import annotations

import asyncio
import contextlib
import json
import collections
import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import board as board_mod
from .metrics import FakeSource, MetricsSource

ROOT = Path(__file__).resolve().parent.parent
SPIKE_DIR = ROOT / "build" / "spike"
EXAMPLES_DIR = ROOT / "build" / "examples"
IMAGE_DIRS = {"examples": EXAMPLES_DIR, "spike": SPIKE_DIR}
REPORTS_DIR = ROOT / "reports"  # rapports `<stem>.npu.json` écrits par `e7twin npu`
UART_HISTORY = 200 # dernières lignes conservées par UART
WEB_DIR = ROOT / "web"
TICK_S = 0.1  # ≈ 10 Hz
_IMAGE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class LoadBody(BaseModel):
    core: str
    path: str


class _Hub:
    """Un seul producteur (tick de la source) diffusé à tous les clients WebSocket."""

    def __init__(self, source: MetricsSource):
        self.source = source
        self.clients: set[asyncio.Queue] = set()
        self._last_state: str | None = None
        self._last_sample = None
        self.uart_hist: dict[str, collections.deque] = {}

    def publish(self, msg: dict) -> None:
        for q in list(self.clients):
            q.put_nowait(msg)

    def status_msg(self) -> dict:
        st = self.source.status()
        return {"type": "status", **st}

    def tick(self) -> None:
        src = self.source
        sample = src.sample()
        for line in src.uart_lines():
            self.uart_hist.setdefault(line["uart"], collections.deque(maxlen=UART_HISTORY)).append(line)
            self.publish({"type": "uart", **line})
        for ev in src.events():
            self.publish({"type": "event", "data": ev})
        if sample is not self._last_sample:  # une source lente (SimSource) ne republie pas le même sample
            self._last_sample = sample
            self.publish({"type": "sample", "data": sample.to_dict()})
        st = src.status()
        if st.get("state") != self._last_state:
            self._last_state = st.get("state")
            self.publish({"type": "status", **st})

    async def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:  # ne jamais tuer la boucle de diffusion
                self.publish({"type": "status", "state": "error", "fake": True, "message": str(e)})
            await asyncio.sleep(TICK_S)


def _core_from_image(name: str) -> str:
    stem = Path(name).stem
    for suffix, core in (("_hp", "m55_hp"), ("_he", "m55_he")):
        if stem.endswith(suffix):
            return core
    raise HTTPException(400, f"cœur non déductible du nom {name!r} (attendu : *_hp / *_he)")


def _deduce_core(path: Path) -> str | None:
    """Cœur d'une image : suffixe du nom, sinon slot MRAM de l'ELF (sim.assign_core)."""
    for suffix, core in (("_hp", "m55_hp"), ("_he", "m55_he")):
        if path.stem.endswith(suffix):
            return core
    try:
        from . import sim
        return sim.assign_core(path)[0]
    except Exception:
        return None


def list_images() -> list[dict]:
    out = []
    for d, folder in IMAGE_DIRS.items():
        if folder.is_dir():
            for p in sorted(folder.glob("*.elf")):
                out.append({"id": f"{d}/{p.stem}", "name": p.stem, "dir": d,
                            "path": p.relative_to(ROOT).as_posix(), "core": _deduce_core(p)})
    return out


def _resolve_image(image: str) -> Path:
    """`examples/hello_hp`, `spike/hello_hp` ou nom nu (examples d'abord, puis spike)."""
    parts = image.split("/")
    if len(parts) > 2 or not all(_IMAGE_RE.match(x) and not x.startswith(".") for x in parts):
        raise HTTPException(400, "nom d'image invalide")
    if len(parts) == 2:
        if parts[0] not in IMAGE_DIRS:
            raise HTTPException(404, f"dossier inconnu : {parts[0]!r} (connus : {sorted(IMAGE_DIRS)})")
        folders = [IMAGE_DIRS[parts[0]]]
    else:
        folders = list(IMAGE_DIRS.values())
    name = parts[-1] if parts[-1].endswith(".elf") else parts[-1] + ".elf"
    for f in folders:
        if (f / name).is_file():
            return f / name
    raise HTTPException(404, f"image inconnue : {image!r} (connues : {[i['id'] for i in list_images()]})")


def create_app(source: MetricsSource, reports_dir: Path | None = None) -> FastAPI:
    hub = _Hub(source)
    rdir = Path(reports_dir) if reports_dir is not None else REPORTS_DIR

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(hub.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            close = getattr(source, "close", None)
            if close:
                await asyncio.to_thread(close)

    app = FastAPI(title="E7 Twin", lifespan=lifespan)
    app.state.source = source

    @app.get("/api/board")
    def get_board() -> dict:
        return board_mod.load().to_dict()

    @app.get("/api/check")
    def list_check() -> dict:
        return {"images": list_images()}

    @app.get("/api/check/{image:path}")
    def get_check(image: str) -> dict:
        path = _resolve_image(image)
        core = _deduce_core(path)
        if core is None:
            raise HTTPException(400, f"cœur non déductible pour {image!r}")
        from . import check
        try:
            return check.run(path, core).to_dict()
        except (ValueError, KeyError) as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/npu")
    def list_npu() -> list[dict]:
        files = sorted(rdir.glob("*.npu.json"), key=lambda p: p.stat().st_mtime, reverse=True) if rdir.is_dir() else []
        return [{"model": p.name[: -len(".npu.json")], "file": p.name, "mtime": p.stat().st_mtime} for p in files]

    @app.get("/api/npu/{stem}")
    def get_npu(stem: str) -> dict:
        if not _IMAGE_RE.match(stem) or ".." in stem:
            raise HTTPException(404, f"rapport NPU inconnu : {stem!r}")
        path = rdir / f"{stem}.npu.json"
        if not path.is_file():
            raise HTTPException(404, f"rapport NPU inconnu : {stem!r}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise HTTPException(422, f"rapport illisible : {e}") from e

    @app.get("/api/uart")
    def get_uart(n: int = UART_HISTORY) -> dict:
        n = max(0, min(n, UART_HISTORY))
        return {"n": n, "uarts": {u: list(h)[-n:] if n else [] for u, h in hub.uart_hist.items()}}

    @app.get("/api/sim/status")
    def sim_status() -> dict:
        return source.status()

    @app.post("/api/sim/load")
    def sim_load(body: LoadBody) -> dict:
        try:
            source.load(body.core, body.path)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"ok": True}

    @app.post("/api/sim/{action}")
    def sim_action(action: str) -> dict:
        fn = {"start": source.start, "pause": source.pause, "reset": source.reset}.get(action)
        if fn is None:
            raise HTTPException(404, f"action inconnue : {action!r} (start|pause|reset)")
        try:
            fn()
        except RuntimeError as e:  # ex. sim.SimError : aucune image chargée
            raise HTTPException(409, str(e)) from e
        if action == "reset":
            hub.uart_hist.clear()
        hub.publish(hub.status_msg())
        hub._last_state = source.status().get("state")
        return source.status()

    @app.get("/api/profile/{core}")
    def get_profile(core: str) -> dict:
        try:
            return source.profile(core)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        hub.clients.add(q)
        try:
            await websocket.send_json(hub.status_msg())
            for hist in list(hub.uart_hist.values()):  # historique UART, rejoué comme des messages `uart`
                for line in list(hist):
                    await websocket.send_json({"type": "uart", **line})
            while True:
                await websocket.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.clients.discard(q)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def main(host: str = "127.0.0.1", port: int = 8000, fake: bool = True,
         hp: str | None = None, he: str | None = None) -> int:
    """Lance le serveur. `fake=False` : simulation Renode (SimSource), avec `hp` / `he` chargés au démarrage.
    Codes : 0 normal, 2 erreur d'environnement ou d'image."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    source: MetricsSource
    if fake:
        source = FakeSource()
        source.start()
    else:
        try:
            from .simsource import SimSource
            from . import sim
        except ImportError as e:
            print(f"e7twin serve : simulation Renode indisponible ({e}) ; utiliser --fake.", file=sys.stderr)
            return 2
        source = SimSource()
        try:
            for core, p in (("m55_hp", hp), ("m55_he", he)):
                if p:
                    source.load(core, p)
            if hp or he:
                source.start()
        except (sim.SimError, ValueError, RuntimeError, KeyError, OSError) as e:
            print(f"e7twin serve : {e}", file=sys.stderr)
            source.close()
            return 2
    import uvicorn
    try:
        uvicorn.run(create_app(source), host=host, port=port, log_level="info")
    finally:
        close = getattr(source, "close", None)
        if close:
            close()
    return 0
