"""Serveur FastAPI + WebSocket d'E7 Twin (agent 05). Contrat : docs/INTERFACES.md."""

from __future__ import annotations

import asyncio
import contextlib
import json
import collections
import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import board as board_mod


class _NoCacheStatic(StaticFiles):
    """Fichiers statiques revalidés à chaque requête (ETag -> 304 possible)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

from .metrics import FakeSource, MetricsSource

ROOT = Path(__file__).resolve().parent.parent
SPIKE_DIR = ROOT / "build" / "spike"
EXAMPLES_DIR = ROOT / "build" / "examples"
UPLOAD_DIR = ROOT / "build" / "uploads"
SRC_EXAMPLES_DIR = ROOT / "examples"
_core_cache: dict[str, tuple[float, str | None]] = {}  # chemin ELF -> (mtime, core_guess)
_building: set[str] = set()  # noms en cours de compilation (/api/build/source)


def _anon_name() -> str:
    """Nom unique pour un import sans nom : upload_<horodatage>_<4 hex>."""
    import time
    import uuid
    return f"upload_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"


IMAGE_DIRS = {"examples": EXAMPLES_DIR, "spike": SPIKE_DIR, "uploads": UPLOAD_DIR}
MAX_UPLOAD = 32 * 1024 * 1024
MAX_SOURCE = 1024 * 1024
_EM_ARM = 40
REPORTS_DIR = ROOT / "reports"  # rapports `<stem>.npu.json` écrits par `e7twin npu`
UART_HISTORY = 200 # dernières lignes conservées par UART
WEB_DIR = ROOT / "web"
TICK_S = 0.1  # ≈ 10 Hz
_IMAGE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SpeedBody(BaseModel):
    accelerated: bool


class LoadBody(BaseModel):
    core: str
    path: str


class CheckRunBody(BaseModel):
    elf: str
    core: str
    npu_report: str | None = None


_CORE_TAGS = (("m55_hp", "hp"), ("m55_he", "he"))
_NAME_RE = re.compile(r"[A-Za-z0-9_]+")


def _under(path: str, base: Path) -> Path:
    """Chemin (relatif au dépôt ou absolu) résolu, exigé sous `base` ; sinon 400."""
    p = Path(path)
    p = (p if p.is_absolute() else ROOT / p).resolve()
    if not p.is_relative_to(base.resolve()):
        raise HTTPException(400, f"chemin refusé (hors de {base.name}/) : {path!r}")
    return p


def _shown(p: Path) -> str:
    return p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)


def _cores_of(s: str) -> list[str]:
    s = s.lower()
    return [c for c, tag in _CORE_TAGS if tag in s or c in s]


def _readme_entries() -> dict[str, dict]:
    """`examples/README.md` : lignes `nom — description — cœur(s)` (puces facultatives) ou lignes de tableau
    `| `nom` | cœur | description |`. Sans README : un dossier = un exemple (nom du dossier, description vide)."""
    out: dict[str, dict] = {}
    readme = SRC_EXAMPLES_DIR / "README.md"
    if readme.is_file():
        for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("-*").strip()
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 3 and _NAME_RE.fullmatch(cells[0].strip("`")) and cells[0].strip("`").lower() != "projet":
                    out[cells[0].strip("`")] = {"description": cells[2], "cores": _cores_of(cells[1].strip("`"))}
            else:
                parts = [x.strip() for x in re.split(r"\s+[—–]\s+", line)]
                if len(parts) >= 2 and _NAME_RE.fullmatch(parts[0].strip("`")):
                    out[parts[0].strip("`")] = {"description": parts[1],
                                                "cores": _cores_of(parts[2].strip("`")) if len(parts) > 2 else []}
    def _real(n: str) -> bool:  # projet réel : dossier avec *.cproject.yml, ou ELF déjà compilé
        return any(any((SRC_EXAMPLES_DIR / c).glob("*.cproject.yml")) or (EXAMPLES_DIR / f"{c}.elf").is_file()
                   for c in (n, f"{n}_hp", f"{n}_he"))
    out = {n: e for n, e in out.items() if _real(n)}  # ligne de README sans projet : ignorée
    if SRC_EXAMPLES_DIR.is_dir():
        for d in sorted(SRC_EXAMPLES_DIR.iterdir()):
            if d.is_dir() and d.name not in out and any(d.glob("*.cproject.yml")):
                out[d.name] = {"description": "", "cores": []}
    return out


def list_examples() -> list[dict]:
    entries = _readme_entries()
    # regroupe x_hp / x_he en un seul exemple `x` quand les deux existent
    for name in list(entries):
        if name.endswith("_hp") and name in entries and name[:-3] + "_he" in entries:
            base = name[:-3]
            hp, he = entries.pop(name), entries.pop(base + "_he")
            entries[base] = {"description": hp["description"] or he["description"], "cores": ["m55_hp", "m55_he"]}
    out = []
    for name, e in entries.items():
        elfs: dict[str, str] = {}
        cores = list(e["cores"])
        for cand, core in ((f"{name}_hp", "m55_hp"), (f"{name}_he", "m55_he"), (name, None)):
            f = EXAMPLES_DIR / f"{cand}.elf"
            if f.is_file():
                c = core or (cores[0] if len(cores) == 1 else _deduce_core(f))
                if c:
                    elfs[c] = f.relative_to(ROOT).as_posix()
                    if c not in cores:
                        cores.append(c)
        if not cores:  # nom en *_hp / *_he sans cœur dans le README
            cores = [c for c, tag in _CORE_TAGS if name.endswith("_" + tag)]
        out.append({"name": name, "cores": sorted(cores), "description": e["description"],
                    "elf": next(iter(elfs.values()), None), "elfs": elfs, "built": bool(elfs)})
    return out


def _check_elf_header(data: bytes) -> str | None:
    """Message d'erreur, ou None si `data` ressemble à un ELF 32 bits little-endian ARM."""
    if len(data) < 20 or data[:4] != b"\x7fELF":
        return "fichier refusé : ce n'est pas un ELF (magic 7F 45 4C 46 absent)"
    if data[4] != 1 or data[5] != 1:
        return "ELF refusé : il faut un ELF 32 bits little-endian (Cortex-M)"
    machine = int.from_bytes(data[18:20], "little")
    if machine != _EM_ARM:
        return f"ELF refusé : machine {machine} (attendu {_EM_ARM} = ARM)"
    return None


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
        for line in src.uart_lines()[-UART_PER_TICK:]:  # accéléré : ne pas saturer le WebSocket
            self.uart_hist.setdefault(line["uart"], collections.deque(maxlen=UART_HISTORY)).append(line)
            self.publish({"type": "uart", **line})
        for ev in src.events()[-EVENTS_PER_TICK:]:
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


UART_PER_TICK = 200
EVENTS_PER_TICK = 200


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


def _run_build_all(projects: list[str]) -> tuple[int, str]:
    """Lance `examples/build_all.py --only ...` en sous-processus ; (code, journal)."""
    import subprocess
    try:
        r = subprocess.run([sys.executable, str(SRC_EXAMPLES_DIR / "build_all.py"), "--only", *projects],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        return 1, "délai dépassé (900 s)"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


_SIG_RE = re.compile(r"SIG\s+([0-9A-Fa-f]{8})\s+(\d+)/(\d+)\s+OK")


def _run_test_elf(elf: Path, core: str, timeout: float = 120.0) -> dict:
    """Exécute `elf` en headless jusqu'à la ligne `SIG ...` (ou `timeout`). Session toujours fermée."""
    import time
    from . import sim
    t0 = time.time()
    with sim.Session() as s:  # __exit__ -> close() : pas de renode.exe orphelin
        s.load_elf(core, elf)
        s.start()
        s.run_until(r"SIG\s+[0-9A-Fa-f]{8}\s+\d+/\d+\s+OK", timeout)
        uart = s.uart_text()
    m = _SIG_RE.search(uart)
    score = f"{m.group(2)}/{m.group(3)}" if m else None
    return {"ok": bool(m and m.group(2) == m.group(3)), "sig": m.group(1).upper() if m else None,
            "score": score, "uart": uart[:20000], "duration_s": round(time.time() - t0, 1)}


def _board_cmd(elf: Path) -> str | None:
    """Commandes SETOOLS (affichées, jamais exécutées) pour cet ELF, sans rien générer."""
    try:
        from . import package
        img = package.analyze_image(elf, None)
        out = ROOT / "build" / "package"
        lines = package.setools_commands(out / package.atoc_filename([img]), [out / package._bin_name(img)])
        return f"e7twin package {_shown(elf)}   # génère .bin + ATOC dans build/package, puis :\n" + "\n".join(lines)
    except Exception:
        return None


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

    @app.get("/api/examples")
    def get_examples() -> list[dict]:
        return list_examples()

    def _prog_elf(stem: str) -> Path | None:
        """ELF d'un programme : `<stem>.elf` (importé) sinon l'ELF compilé sous `<stem>/out/`."""
        top = UPLOAD_DIR / f"{stem}.elf"
        if top.is_file():
            return top
        out = UPLOAD_DIR / stem / "out"
        if out.is_dir():
            elfs = [p for p in out.rglob(f"{stem}.elf") if p.is_file()]
            if elfs:
                return max(elfs, key=lambda p: p.stat().st_mtime)
        return None

    def _prog_stem(name: str) -> str:
        if not _IMAGE_RE.match(name) or name.startswith(".") or ".." in name:
            raise HTTPException(400, f"nom de programme invalide : {name!r}")
        return name

    def _core_cached(elf: Path) -> str | None:
        key = str(elf)
        mt = elf.stat().st_mtime
        hit = _core_cache.get(key)
        if hit and hit[0] == mt:
            return hit[1]
        try:
            from . import sim
            core = sim.assign_core(elf)[0]
        except Exception:
            core = _deduce_core(elf)
        _core_cache[key] = (mt, core)
        return core

    @app.get("/api/programs")
    def list_programs() -> list[dict]:
        if not UPLOAD_DIR.is_dir():
            return []
        stems = {p.stem for p in UPLOAD_DIR.glob("*.elf")} | {p.stem for p in UPLOAD_DIR.glob("*.c")}
        out = []
        for stem in stems:
            elf = _prog_elf(stem)
            if elf is None or not _IMAGE_RE.match(stem):
                continue  # source sans ELF (compilation échouée) : non exposé
            has_src = (UPLOAD_DIR / f"{stem}.c").is_file()
            out.append({"id": f"uploads/{stem}", "name": stem, "elf": _shown(elf),
                        "core_guess": _core_cached(elf), "origin": "compiled" if has_src else "imported",
                        "has_source": has_src, "mtime": elf.stat().st_mtime})
        return sorted(out, key=lambda d: d["mtime"], reverse=True)

    @app.get("/api/programs/{name}/source")
    def program_source(name: str) -> Response:
        src = UPLOAD_DIR / f"{_prog_stem(name)}.c"
        if not src.is_file():
            raise HTTPException(404, f"pas de source pour {name!r}")
        return Response(src.read_text(encoding="utf-8", errors="replace"), media_type="text/plain; charset=utf-8")

    @app.delete("/api/programs/{name}")
    def delete_program(name: str) -> dict:
        import shutil
        stem = _prog_stem(name)
        files = [UPLOAD_DIR / f"{stem}{s}" for s in (".elf", ".c", ".map")]
        workdir = UPLOAD_DIR / stem
        if not any(f.is_file() for f in files) and not workdir.is_dir():
            raise HTTPException(404, f"programme inconnu : {name!r}")
        if stem in _building:
            raise HTTPException(409, f"{stem!r} est en cours de compilation")
        root = UPLOAD_DIR.resolve()
        try:
            loaded = [Path(p).resolve() for p in (source.status().get("images") or {}).values()]
        except Exception:
            loaded = []
        for p in loaded:
            if p == (root / f"{stem}.elf") or (root / stem) in p.parents:
                raise HTTPException(409, f"{stem!r} est chargé dans la simulation")
        for f in files:
            f.unlink(missing_ok=True)
        if workdir.is_dir() and workdir.resolve().parent == root:
            shutil.rmtree(workdir, ignore_errors=True)
        return {"ok": True}

    def _example_projects(name: str) -> tuple[dict, list[str]]:
        ex = next((e for e in list_examples() if e["name"] == name), None)
        if ex is None:
            raise HTTPException(404, f"exemple inconnu : {name!r}")
        import importlib.util
        spec = importlib.util.spec_from_file_location("_e7_build_all", SRC_EXAMPLES_DIR / "build_all.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        known = set(mod.PROJECTS)
        if name in known:
            projects = [name]
        else:
            projects = [f"{name}_{c[-2:]}" for c in ex["cores"]]
        missing = [p for p in projects if p not in known]
        if not projects or missing:
            raise HTTPException(404, f"projet(s) absent(s) de build_all.PROJECTS : {missing or [name]}")
        return ex, projects

    @app.post("/api/examples/{name}/build")
    async def build_example(name: str) -> dict:
        ex, projects = _example_projects(name)
        key = f"example:{name}"
        if key in _building:
            raise HTTPException(409, f"une compilation de l'exemple {name!r} est déjà en cours")
        _building.add(key)
        try:
            code, log = await run_in_threadpool(_run_build_all, projects)
        finally:
            _building.discard(key)
        fresh = next((e for e in list_examples() if e["name"] == name), ex)
        present = all((EXAMPLES_DIR / f"{p}.elf").is_file() for p in projects)
        return {"ok": code == 0 and present, "name": name, "projects": projects,
                "log_tail": "\n".join(log.splitlines()[-40:]), "example": fresh}

    def _tests() -> list[dict]:
        return [e for e in list_examples() if e["name"].startswith("parity_")]

    @app.get("/api/tests")
    def list_tests() -> list[dict]:
        return _tests()

    @app.post("/api/tests/{name}/run")
    async def run_test(name: str) -> dict:
        t = next((e for e in _tests() if e["name"] == name), None)
        if t is None:
            raise HTTPException(404, f"test inconnu : {name!r}")
        if not t["elfs"]:
            raise HTTPException(409, f"test {name!r} non compilé (POST /api/examples/{name}/build)")
        key = f"test:{name}"
        if key in _building:
            raise HTTPException(409, f"une exécution de {name!r} est déjà en cours")
        core = "m55_hp" if "m55_hp" in t["elfs"] else next(iter(t["elfs"]))
        elf = ROOT / t["elfs"][core]
        _building.add(key)
        try:
            res = await run_in_threadpool(_run_test_elf, elf, core)
            cmd = await run_in_threadpool(_board_cmd, elf)
        except Exception as e:
            from . import sim
            if isinstance(e, sim.SimError) and "Renode introuvable" in str(e):
                raise HTTPException(503, str(e)) from e
            raise HTTPException(500, f"exécution échouée : {e}") from e
        finally:
            _building.discard(key)
        return {"ok": res["ok"], "name": name, "elf": _shown(elf), "core": core, "sig": res["sig"],
                "score": res["score"], "uart": res["uart"], "duration_s": res["duration_s"], "board_cmd": cmd}

    @app.post("/api/upload")
    async def upload(request: Request, name: str = "") -> dict:
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(name.replace("\\", "/")).name)
        stem = stem[:-4] if stem.lower().endswith(".elf") else stem
        stem = stem.strip("._") or _anon_name()
        too_big = f"fichier trop gros (max {MAX_UPLOAD // (1024 * 1024)} Mo)"
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
            raise HTTPException(400, too_big)
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_UPLOAD:
                raise HTTPException(400, too_big)
        err = _check_elf_header(data)
        if err:
            raise HTTPException(400, err)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = UPLOAD_DIR / f"{stem}.elf"
        path.write_bytes(data)
        try:
            from . import sim
            core = sim.assign_core(path)[0]
        except Exception:
            core = _deduce_core(path)
        return {"id": f"uploads/{stem}", "name": stem, "elf": _shown(path), "core_guess": core}

    @app.post("/api/build/source")
    async def build_source_route(request: Request, core: str = "", name: str = "") -> dict:
        from . import build as build_mod
        if core not in ("m55_hp", "m55_he"):
            raise HTTPException(400, f"cœur invalide : {core!r} (m55_hp|m55_he)")
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_SOURCE:
                raise HTTPException(400, f"source trop gros (max {MAX_SOURCE // 1024} Ko)")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise HTTPException(400, "le corps doit être du texte UTF-8") from e
        if not text.strip():
            raise HTTPException(400, "corps vide : envoyer le texte de main.c")
        stem = build_mod.sanitize_name(name, default=_anon_name())
        if stem in _building:
            raise HTTPException(409, f"une compilation de {stem!r} est déjà en cours")
        _building.add(stem)  # boucle asyncio mono-thread : test + ajout atomiques
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            src = UPLOAD_DIR / f"{stem}.c"
            src.write_text(text, encoding="utf-8", newline="\n")
            res = await run_in_threadpool(build_mod.build_source, src, core, stem, UPLOAD_DIR)
        finally:
            _building.discard(stem)
        res = dict(res)
        res["name"] = stem
        if res.get("ok") and res.get("elf"):
            try:
                from . import sim
                res["core_guess"] = sim.assign_core(res["elf"])[0]
            except Exception:
                res["core_guess"] = core
        return res

    @app.post("/api/check/run")
    def check_run(body: CheckRunBody) -> dict:
        elf = _under(body.elf, ROOT / "build")
        if not elf.is_file():
            raise HTTPException(404, f"ELF introuvable : {body.elf!r}")
        npu = None
        if body.npu_report:
            npu = _under(body.npu_report, rdir)
            if not npu.is_file():
                raise HTTPException(404, f"rapport NPU introuvable : {body.npu_report!r}")
        from . import check, report as report_mod
        try:
            rep = check.run(elf, body.core, npu_report=npu)
        except KeyError as e:
            raise HTTPException(404, f"cœur inconnu : {body.core!r}") from e
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        _, html = report_mod.write(rep, rdir)
        shown = _shown(html)
        return {**rep.to_dict(), "report_html": shown, "report_url": f"/api/report/{html.name}"}

    @app.get("/api/report/{name}")
    def get_report(name: str) -> FileResponse:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.check\.(html|json)", name) or ".." in name:
            raise HTTPException(404, f"rapport inconnu : {name!r}")
        path = rdir / name
        if not path.is_file():
            raise HTTPException(404, f"rapport inconnu : {name!r}")
        return FileResponse(path, media_type="text/html" if name.endswith(".html") else "application/json")

    @app.get("/api/uart")
    def get_uart(n: int = UART_HISTORY) -> dict:
        n = max(0, min(n, UART_HISTORY))
        return {"n": n, "uarts": {u: list(h)[-n:] if n else [] for u, h in hub.uart_hist.items()}}

    @app.get("/api/sim/status")
    def sim_status() -> dict:
        return source.status()

    @app.post("/api/sim/speed")
    def sim_speed(body: SpeedBody) -> dict:
        fn = getattr(source, "set_speed", None)
        try:
            if fn is None:
                raise RuntimeError("vitesse non supportée")
            fn(body.accelerated)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        hub.publish(hub.status_msg())
        return source.status()

    @app.post("/api/sim/load")
    def sim_load(body: LoadBody) -> dict:
        try:
            lp = Path(body.path)
            source.load(body.core, str(ROOT / lp if not lp.is_absolute() and (ROOT / lp).exists() else lp))
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

    def _fb() -> dict | None:
        fn = getattr(source, "framebuffer", None)
        return fn() if fn else None

    @app.get("/api/display")
    def get_display() -> dict:
        fb = _fb()
        if not fb:
            return {"enabled": False, "width": None, "height": None, "format": None, "address": None}
        return {"enabled": True, "width": fb["width"], "height": fb["height"],
                "format": fb["format"], "address": fb.get("address")}

    @app.get("/api/display.png")
    def get_display_png() -> Response:
        from .framebuffer import to_png
        fb = _fb()
        if not fb:
            raise HTTPException(404, "écran inactif")
        return Response(to_png(fb), media_type="image/png", headers={"Cache-Control": "no-store"})

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
                # on écoute aussi le client : fermeture de l'onglet ou arrêt d'uvicorn (frame close) = sortie propre
                get = asyncio.ensure_future(q.get())
                recv = asyncio.ensure_future(websocket.receive())
                try:
                    done, _ = await asyncio.wait({get, recv}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for t in (get, recv):
                        if not t.done():
                            t.cancel()
                if recv in done:
                    if recv.result().get("type") == "websocket.disconnect":
                        break
                    if get not in done:
                        continue
                await websocket.send_json(get.result())
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            hub.clients.discard(q)

    @app.post("/api/shutdown")
    async def shutdown(request: Request) -> dict:
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "arrêt réservé au poste local")
        srv = getattr(app.state, "server", None)  # posé par main() ; absent avec create_app seul
        if srv is not None:
            asyncio.get_running_loop().call_later(0.5, setattr, srv, "should_exit", True)
        return {"ok": True}

    if WEB_DIR.is_dir():
        app.mount("/", _NoCacheStatic(directory=WEB_DIR, html=True), name="web")
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
        app = create_app(source)
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info",
                                               timeout_graceful_shutdown=3))
        app.state.server = server
        try:
            server.run()
        except KeyboardInterrupt:  # Ctrl+C : uvicorn a déjà arrêté proprement, le signal est ré-émis à la sortie
            pass
    finally:
        close = getattr(source, "close", None)
        if close:
            close()
    return 0
