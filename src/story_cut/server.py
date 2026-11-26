"""`story-cut serve`: a small web app so a phone (or any browser) can drive the PC.

The PC does the heavy work (scene detection, Whisper, the local vision model, ffmpeg).
The phone only picks a video, chooses a mode or types a request, watches progress,
reviews/trims the result, and opens it in OpenReel or downloads it.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".ts"}
COOKIE = "sc_token"
STATIC_DIR = Path(__file__).parent / "web"


@dataclass
class ServeOptions:
    videos: Path = Path(".")
    host: str = "127.0.0.1"
    port: int = 8765
    token: str | None = None
    no_token: bool = False
    work_dir: Path | None = None
    openreel_url: str | None = None
    backend: str = "lmstudio"
    endpoint: str = "http://localhost:1234/v1"
    model: str = "qwen/qwen3-vl-8b"
    language: str = "ko"
    whisper_model: str = "small"
    cache_dir: Path | None = None


def token_file() -> Path:
    return Path.home() / ".story-cut" / "token"


def load_or_create_token(explicit: str | None) -> str:
    """One stable password per PC, so a phone bookmark keeps working."""
    path = token_file()
    if explicit:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(explicit, encoding="utf-8")
        return explicit
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(8)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return value


def lan_addresses() -> list[str]:
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip.startswith("127.") or ip in out:
            continue
        out.append(ip)
    # Tailscale (100.64.0.0/10) last, home Wi-Fi first.
    return sorted(out, key=lambda ip: ip.startswith("100."))


def resolve_cache_dir(opts: ServeOptions, work: Path) -> Path:
    """Reuse the analysis cache of earlier CLI runs (story-cut folder) when it is there."""
    if opts.cache_dir:
        return opts.cache_dir.expanduser().resolve()
    here = Path.cwd() / ".story-cut-cache"
    if here.is_dir():
        return here.resolve()
    return work / ".story-cut-cache"


def encode_id(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")


def decode_id(value: str) -> str:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad).decode("utf-8")


def safe_name(name: str) -> str:
    base = Path(name).name
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .") or "upload.mp4"
    stem, ext = os.path.splitext(base)
    if ext.lower() not in VIDEO_EXTS:
        ext = ".mp4"
    return stem[:120] + ext


def inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- jobs


@dataclass
class Job:
    id: str
    video: str
    video_name: str
    mode: str | None
    prompt: str | None
    max_total: float | None
    created: float
    status: str = "queued"  # queued, running, done, error, canceled
    progress: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: str = ""
    finished: float | None = None
    render: dict[str, Any] = field(default_factory=dict)
    proxy: dict[str, Any] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def public(self, with_log: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "video_name": self.video_name,
            "mode": self.mode,
            "prompt": self.prompt,
            "max_total": self.max_total,
            "created": self.created,
            "finished": self.finished,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "render": self.render,
            "proxy": self.proxy,
        }
        if with_log:
            data["log"] = self.log[-120:]
        return data


class JobManager:
    def __init__(self, opts: ServeOptions, work: Path):
        self.opts = opts
        self.work = work
        self.cache = resolve_cache_dir(opts, work)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.queue: list[str] = []
        self.wake = threading.Event()
        self.procs: dict[str, subprocess.Popen[str]] = {}
        self._load()
        threading.Thread(target=self._worker, daemon=True).start()

    # persistence
    def job_dir(self, job_id: str) -> Path:
        return self.work / job_id

    def _save(self, job: Job) -> None:
        d = self.job_dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        data = job.public()
        data["video"] = job.video
        (d / "job.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self.work.is_dir():
            return
        for f in self.work.glob("*/job.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                job = Job(
                    id=data["id"], video=data["video"], video_name=data["video_name"],
                    mode=data.get("mode"), prompt=data.get("prompt"), max_total=data.get("max_total"),
                    created=data.get("created") or f.stat().st_mtime,
                )
                job.status = data.get("status") or "error"
                job.progress = data.get("progress") or {}
                job.message = data.get("message") or ""
                job.error = data.get("error") or ""
                job.finished = data.get("finished")
                job.render = data.get("render") or {}
                job.proxy = data.get("proxy") or {}
                if job.status in {"queued", "running"}:
                    job.status, job.error = "error", "서버가 다시 시작되어 중단됐습니다. 다시 실행해 주세요."
                for key in ("render", "proxy"):
                    state = getattr(job, key)
                    if state.get("status") == "running":
                        state.update(status="error", error="서버가 다시 시작되어 중단됐습니다.")
                log = f.parent / "log.txt"
                if log.is_file():
                    job.log = log.read_text(encoding="utf-8", errors="replace").splitlines()[-120:]
                self.jobs[job.id] = job
            except Exception:
                continue

    # api
    def create(self, video: Path, mode: str | None, prompt: str | None, max_total: float | None) -> Job:
        job = Job(
            id=time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4],
            video=str(video), video_name=video.name, mode=mode or None,
            prompt=(prompt or "").strip() or None, max_total=max_total, created=time.time(),
        )
        with self.lock:
            self.jobs[job.id] = job
            self.queue.append(job.id)
            self._save(job)
        self.wake.set()
        return job

    def cancel(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if job.id in self.queue:
                self.queue.remove(job.id)
                job.status = "canceled"
                self._save(job)
                return
            proc = self.procs.get(job_id)
        if proc and proc.poll() is None:
            job.status = "canceled"
            proc.terminate()

    def delete(self, job_id: str) -> None:
        self.cancel(job_id) if self.jobs.get(job_id) and self.jobs[job_id].status in {"queued", "running"} else None
        with self.lock:
            self.jobs.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    def _worker(self) -> None:
        while True:
            self.wake.wait(1.0)
            self.wake.clear()
            while True:
                with self.lock:
                    if not self.queue:
                        break
                    job_id = self.queue.pop(0)
                    job = self.jobs.get(job_id)
                if job and job.status == "queued":
                    self._run(job)

    def _command(self, job: Job) -> list[str]:
        o = self.opts
        out = self.job_dir(job.id) / "edit.json"
        cmd = [
            sys.executable, "-m", "story_cut", "analyze", job.video, "--out", str(out),
            "--backend", o.backend, "--endpoint", o.endpoint, "--model", o.model,
            "--language", o.language, "--whisper-model", o.whisper_model,
            "--cache-dir", str(self.cache),
        ]
        if job.mode:
            cmd += ["--mode", job.mode]
        if job.prompt:
            cmd += ["--prompt", job.prompt]
        if job.max_total:
            cmd += ["--max-total", str(job.max_total)]
        return cmd

    def _run(self, job: Job) -> None:
        d = self.job_dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, STORY_CUT_PROGRESS="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        job.status, job.progress, job.message = "running", {"step": 0, "steps": 6}, "시작하는 중..."
        self._save(job)
        logf = (d / "log.txt").open("w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                self._command(job), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env, bufsize=1,
            )
        except OSError as exc:
            job.status, job.error = "error", f"실행하지 못했습니다: {exc}"
            self._save(job)
            logf.close()
            return
        self.procs[job.id] = proc
        assert proc.stdout is not None
        last_save = 0.0
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith("@@progress "):
                try:
                    job.progress = json.loads(line[len("@@progress "):])
                except ValueError:
                    pass
            else:
                logf.write(line + "\n")
                logf.flush()
                job.log.append(line)
                if len(job.log) > 400:
                    del job.log[:200]
                if line.strip():
                    job.message = line.strip()[:200]
            if time.time() - last_save > 2:
                self._save(job)
                last_save = time.time()
        code = proc.wait()
        logf.close()
        self.procs.pop(job.id, None)
        job.finished = time.time()
        if job.status == "canceled":
            job.message = "취소했습니다."
        elif code == 0 and (d / "edit.json").is_file():
            job.status, job.message = "done", "완료"
            job.progress = {"step": 6, "steps": 6}
            try:
                side = json.loads((d / "edit.story-cut.json").read_text(encoding="utf-8"))
                job.mode = job.mode or side.get("mode")
            except (OSError, ValueError):
                pass
        else:
            job.status = "error"
            errors = [l for l in job.log if l.startswith("오류")]
            job.error = (errors[-1] if errors else (job.log[-1] if job.log else f"종료 코드 {code}"))[:800]
        self._save(job)

    # results
    def result(self, job: Job) -> dict[str, Any] | None:
        side = self.job_dir(job.id) / "edit.story-cut.json"
        if not side.is_file():
            return None
        from story_cut.project import read_sidecar
        data = read_sidecar(side)
        data.pop("video", None)
        data.pop("cache_dir", None)
        data.pop("edit", None)
        data["openreel_edited"] = (self.job_dir(job.id) / "edit.original.json").is_file()
        return data

    def start_render(self, job: Job, burn_subs: bool, encoder: str) -> None:
        if job.render.get("status") == "running":
            return
        job.render = {"status": "running", "progress": 0.0, "burn_subs": burn_subs}
        self._save(job)

        def work() -> None:
            from story_cut.render import render_edit
            d = self.job_dir(job.id)
            out = d / f"{Path(job.video_name).stem}-{job.mode or 'edit'}.mp4"
            try:
                def tick(p: float) -> None:
                    job.render["progress"] = round(p, 3)
                render_edit(d / "edit.json", Path(job.video), out, burn_subtitles=burn_subs, encoder=encoder, on_progress=tick)
                job.render.update(status="done", file=out.name, progress=1.0, size=out.stat().st_size)
            except Exception as exc:
                job.render.update(status="error", error=str(exc)[-800:])
            self._save(job)

        threading.Thread(target=work, daemon=True).start()

    def proxy_path(self, job: Job) -> Path:
        from story_cut.cache import fingerprint
        return self.work / ".proxies" / f"{fingerprint(Path(job.video))}.mp4"

    def start_proxy(self, job: Job) -> None:
        path = self.proxy_path(job)
        if path.is_file():
            job.proxy = {"status": "done", "size": path.stat().st_size}
            self._save(job)
            return
        if job.proxy.get("status") == "running":
            return
        job.proxy = {"status": "running"}
        self._save(job)

        def work() -> None:
            from story_cut.render import make_proxy
            tmp = path.with_suffix(".part.mp4")
            try:
                make_proxy(Path(job.video), tmp)
                tmp.replace(path)
                job.proxy = {"status": "done", "size": path.stat().st_size}
            except Exception as exc:
                job.proxy = {"status": "error", "error": str(exc)[-600:]}
            self._save(job)

        threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------- app


def create_app(opts: ServeOptions):
    videos_root = opts.videos.expanduser().resolve()
    work = (opts.work_dir or (videos_root / "story-cut-results")).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    token = None if opts.no_token else load_or_create_token(opts.token)
    manager = JobManager(opts, work)
    app = FastAPI(title="story-cut", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager
    app.state.token = token
    app.state.work = work

    def authed(request: Request) -> bool:
        if token is None:
            return True
        supplied = request.cookies.get(COOKIE) or ""
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        supplied = request.query_params.get("token") or supplied
        return bool(supplied) and hmac.compare_digest(supplied.encode(), token.encode())

    def cors(request: Request, response: Response) -> Response:
        origin = request.headers.get("origin")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Range"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Content-Disposition"
        # OpenReel runs cross-origin isolated (COEP require-corp).
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        return response

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS":
            return cors(request, Response(status_code=204))
        public = path in {"/", "/index.html", "/api/login", "/manifest.webmanifest", "/icon.svg", "/favicon.ico"}
        if not public and not authed(request):
            return cors(request, JSONResponse({"detail": "비밀번호가 필요합니다."}, status_code=401))
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        return cors(request, response)

    def set_cookie(resp: Response) -> Response:
        if token:
            resp.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 60, httponly=True, samesite="lax", path="/")
        return resp

    @app.get("/")
    async def index(request: Request):
        if token and request.query_params.get("token"):
            if authed(request):
                return set_cookie(RedirectResponse("./", status_code=303))
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/manifest.webmanifest")
    async def manifest():
        return JSONResponse({
            "name": "story-cut", "short_name": "story-cut", "start_url": "./", "display": "standalone",
            "background_color": "#0b0f17", "theme_color": "#0b0f17",
            "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    @app.get("/icon.svg")
    async def icon():
        return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        given = str(body.get("token") or "").strip()
        if token and not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "비밀번호가 맞지 않습니다.")
        return set_cookie(JSONResponse({"ok": True}))

    @app.get("/api/info")
    async def info(request: Request):
        from story_cut import __version__
        from story_cut.modes import modes_for_ui
        return {
            "version": __version__,
            "modes": modes_for_ui(),
            "backend": opts.backend,
            "model": opts.model,
            "language": opts.language,
            "videos_dir": str(videos_root),
            "work_dir": str(work),
            "openreel_url": opts.openreel_url,
            "port": opts.port,
            "lan": [f"http://{ip}:{opts.port}/" for ip in lan_addresses()] if opts.host not in {"127.0.0.1", "localhost"} else [],
        }

    @app.get("/api/videos")
    async def list_videos():
        items = []
        skip = {work.name, "node_modules", "story-cut-results", ".venv", "__pycache__"}
        base_depth = len(videos_root.parts)
        for dirpath, dirnames, filenames in os.walk(videos_root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in skip and not Path(dirpath, d).resolve() == work]
            if len(Path(dirpath).parts) - base_depth >= 3:
                dirnames[:] = []
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() not in VIDEO_EXTS or ".part" in p.name:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                rel = p.relative_to(videos_root).as_posix()
                items.append({"id": encode_id(rel), "name": name, "path": rel, "size": st.st_size, "mtime": st.st_mtime})
            if len(items) > 2000:
                break
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return {"root": str(videos_root), "videos": items[:500]}

    def video_path(video_id: str) -> Path:
        try:
            rel = decode_id(video_id)
        except Exception:
            raise HTTPException(400, "영상 id가 잘못됐습니다.")
        p = (videos_root / rel).resolve()
        if not inside(videos_root, p) or not p.is_file():
            raise HTTPException(404, "영상을 찾지 못했습니다.")
        return p

    @app.get("/api/videos/{video_id}/file")
    async def video_file(video_id: str):
        return FileResponse(video_path(video_id))

    @app.post("/api/upload")
    async def upload(request: Request, name: str):
        dest_dir = videos_root / "uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        fname = safe_name(name)
        dest = dest_dir / fname
        n = 1
        while dest.exists():
            dest = dest_dir / f"{Path(fname).stem} ({n}){Path(fname).suffix}"
            n += 1
        part = dest.with_name(dest.name + ".part")
        size = 0
        try:
            with part.open("wb") as fh:
                async for chunk in request.stream():
                    fh.write(chunk)
                    size += len(chunk)
            if size == 0:
                raise HTTPException(400, "빈 파일입니다.")
            part.replace(dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        rel = dest.relative_to(videos_root).as_posix()
        return {"id": encode_id(rel), "name": dest.name, "path": rel, "size": size}

    @app.get("/api/jobs")
    async def jobs():
        items = sorted(manager.jobs.values(), key=lambda j: j.created, reverse=True)
        return {"jobs": [j.public() for j in items[:100]]}

    @app.post("/api/jobs")
    async def create_job(request: Request):
        body = await request.json()
        video = video_path(str(body.get("video") or ""))
        mode = str(body.get("mode") or "").strip() or None
        from story_cut.modes import ALIASES, MODES
        if mode and mode not in MODES and mode not in ALIASES:
            raise HTTPException(400, "모르는 모드입니다.")
        max_total = None
        if body.get("max_total"):
            try:
                max_total = max(5.0, float(body["max_total"]))
            except (TypeError, ValueError):
                raise HTTPException(400, "길이 값이 잘못됐습니다.")
        prompt = str(body.get("prompt") or "")[:1000]
        if not mode and not prompt.strip():
            mode = "story"
        job = manager.create(video, mode, prompt, max_total)
        return job.public()

    def get_job(job_id: str) -> Job:
        job = manager.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "작업을 찾지 못했습니다.")
        return job

    @app.get("/api/jobs/{job_id}")
    async def job_detail(job_id: str):
        job = get_job(job_id)
        data = job.public(with_log=True)
        data["result"] = manager.result(job) if job.status == "done" else None
        return data

    @app.post("/api/jobs/{job_id}/cancel")
    async def job_cancel(job_id: str):
        manager.cancel(get_job(job_id).id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/delete")
    async def job_delete(job_id: str):
        manager.delete(get_job(job_id).id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/rerun")
    async def job_rerun(job_id: str, request: Request):
        old = get_job(job_id)
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        mode = body.get("mode", old.mode)
        prompt = body.get("prompt", old.prompt)
        job = manager.create(Path(old.video), mode, prompt, body.get("max_total", old.max_total))
        return job.public()

    def job_file(job: Job, name: str) -> Path:
        p = manager.job_dir(job.id) / name
        if not p.is_file():
            raise HTTPException(404, "파일이 아직 없습니다.")
        return p

    @app.get("/api/jobs/{job_id}/edit.json")
    async def job_edit(job_id: str, download: int = 0):
        job = get_job(job_id)
        p = job_file(job, "edit.json")
        name = f"{Path(job.video_name).stem}-{job.mode or 'edit'}.json"
        return FileResponse(p, media_type="application/json", filename=name if download else None)

    @app.get("/api/jobs/{job_id}/report.md")
    async def job_report(job_id: str):
        return FileResponse(job_file(get_job(job_id), "edit.report.md"), media_type="text/markdown; charset=utf-8")

    @app.get("/api/jobs/{job_id}/report.csv")
    async def job_report_csv(job_id: str):
        job = get_job(job_id)
        return FileResponse(job_file(job, "edit.report.csv"), media_type="text/csv; charset=utf-8", filename=f"{job.id}.csv")

    @app.get("/api/jobs/{job_id}/media")
    async def job_media(job_id: str, proxy: int = 0):
        job = get_job(job_id)
        if proxy:
            p = manager.proxy_path(job)
            if not p.is_file():
                raise HTTPException(404, "가벼운 영상이 아직 없습니다.")
            return FileResponse(p, media_type="video/mp4", filename=job.video_name if job.video_name.lower().endswith(".mp4") else Path(job.video_name).stem + ".mp4")
        p = Path(job.video)
        if not p.is_file():
            raise HTTPException(404, "원본 영상이 없습니다.")
        return FileResponse(p, filename=job.video_name)

    @app.post("/api/jobs/{job_id}/ranges")
    async def job_ranges(job_id: str, request: Request):
        job = get_job(job_id)
        body = await request.json()
        ranges = body.get("ranges")
        if not isinstance(ranges, list):
            raise HTTPException(400, "ranges 목록이 필요합니다.")
        from story_cut.project import rebuild_edit
        d = manager.job_dir(job.id)
        rebuild_edit(d / "edit.story-cut.json", ranges, aspect=body.get("aspect"))
        (d / "edit.original.json").unlink(missing_ok=True)
        job.render = {}
        manager._save(job)
        return manager.result(job)

    @app.post("/api/jobs/{job_id}/edit-json")
    async def job_put_edit(job_id: str, request: Request):
        """OpenReel sends its edited timeline back so the PC can render it at full quality."""
        job = get_job(job_id)
        raw = await request.body()
        if len(raw) > 20_000_000:
            raise HTTPException(413, "너무 큽니다.")
        doc = json.loads(raw)
        from story_cut.render import cuts_from_edit
        if doc.get("format") != "openreel-edit":
            raise HTTPException(400, "openreel-edit JSON이 아닙니다.")
        cuts_from_edit(doc)  # validates
        d = manager.job_dir(job.id)
        if not (d / "edit.original.json").is_file():
            shutil.copyfile(d / "edit.json", d / "edit.original.json")
        (d / "edit.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        job.render = {}
        manager._save(job)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/render")
    async def job_render(job_id: str, request: Request):
        job = get_job(job_id)
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        manager.start_render(job, bool(body.get("burn_subs")), str(body.get("encoder") or "libx264"))
        return job.public()

    @app.get("/api/jobs/{job_id}/output")
    async def job_output(job_id: str):
        job = get_job(job_id)
        name = job.render.get("file")
        if not name:
            raise HTTPException(404, "아직 영상을 만들지 않았습니다.")
        return FileResponse(job_file(job, name), media_type="video/mp4", filename=name)

    @app.post("/api/jobs/{job_id}/proxy")
    async def job_proxy(job_id: str):
        job = get_job(job_id)
        manager.start_proxy(job)
        return job.public()

    return app


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def print_banner(opts: ServeOptions, token: str | None, app_work: Path) -> None:
    local = f"http://localhost:{opts.port}/"
    suffix = f"?token={token}" if token else ""
    exposed = opts.host not in {"127.0.0.1", "localhost"}
    ips = lan_addresses()
    print("")
    print("story-cut 웹 화면이 켜졌습니다. 이 창은 켜 둔 채로 쓰세요. (끄려면 이 창을 닫으세요)")
    print(f"  이 PC에서:      {local}{suffix}")
    qr_url = None
    reel = _port_open(5173)
    if reel and token:
        for ip in ips:
            kind = "Tailscale" if ip.startswith("100.") else "같은 와이파이"
            print(f"  휴대폰({kind}, 추천): https://{ip}:5173/storycut/{suffix}")
        qr_url = f"https://{ips[0]}:5173/storycut/{suffix}" if ips else None
    if exposed:
        for ip in ips:
            kind = "Tailscale" if ip.startswith("100.") else "같은 와이파이"
            print(f"  휴대폰({kind}): http://{ip}:{opts.port}/{suffix}")
        qr_url = qr_url or (f"http://{ips[0]}:{opts.port}/{suffix}" if ips else None)
    if token:
        print(f"  접속 비밀번호:  {token}   (바꾸려면 --token 새비밀번호)")
    else:
        print("  비밀번호 없이 열었습니다. 집 밖에 노출하지 마세요.")
    print(f"  영상 폴더:      {opts.videos.expanduser().resolve()}")
    print(f"  결과 폴더:      {app_work}")
    print(f"  분석 캐시:      {resolve_cache_dir(opts, app_work)}")
    if not reel and not exposed:
        print("  휴대폰에서 쓰려면: OpenReel 폴더에서 'pnpm dev:remote'를 켜고 그 창에 나온 https://...:5173/storycut/ 주소로 여세요.")
        print("  (OpenReel 없이 story-cut만 쓰려면 이 명령에 --host 0.0.0.0 을 붙이세요)")
    if qr_url:
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(qr_url)
            qr.make(fit=True)
            print("  휴대폰 카메라로 아래 QR을 찍으면 바로 열립니다:")
            qr.print_ascii(invert=True)
        except Exception:
            pass
    print("")


def run_server(opts: ServeOptions) -> None:
    import uvicorn
    app = create_app(opts)
    print_banner(opts, app.state.token, app.state.work)
    uvicorn.run(app, host=opts.host, port=opts.port, log_level="warning")
