"""FastAPI app: landing page, web viewer, full-size images, on-demand thumbs,
face crops, and persistence of decisions/clusters/scene-grouping."""
from __future__ import annotations

import platform
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps
from pydantic import BaseModel

from . import db, scenes, userstate
from .scorer import SUPPORTED_EXTS, _is_supported, apply_scene_suggestions

THUMB_LONG_EDGE = 1280
THUMB_QUALITY = 90
FACE_LONG_EDGE = 360
FACE_QUALITY = 90
FACE_PADDING = 0.85  # crop half-side = max(w, h) * FACE_PADDING (about 70% padding around face)
WEB_DIR = Path(__file__).parent / "web"

Decision = Literal["pick", "review", "reject"]


# ----- payload models -----------------------------------------------------

class DecidePayload(BaseModel):
    rel_path: str
    decision: Decision | None


class BulkDecidePayload(BaseModel):
    rel_paths: list[str]
    decision: Decision | None


class ScorePayload(BaseModel):
    with_faces: bool = False


class ClusterPayload(BaseModel):
    eps: float = 0.55
    min_samples: int = 3


class PersonUpdate(BaseModel):
    id: str
    label: str
    priority: int
    excluded: bool = False


class PeoplePayload(BaseModel):
    people: list[PersonUpdate]


class ExportPayload(BaseModel):
    target_dir: str | None = None
    flatten: bool = False


class OpenPayload(BaseModel):
    photo_dir: str
    jpeg_subdir: str = ""
    db_path: str | None = None


class BrowsePayload(BaseModel):
    initial: str | None = None


class ForgetPayload(BaseModel):
    db_path: str


class SceneGroupingPayload(BaseModel):
    mode: Literal["folder", "time_gap"]
    gap_minutes: int = 30


# ----- app context --------------------------------------------------------

class AppContext:
    """Holds all per-project mutable state. Swapped on /api/open."""

    def __init__(self) -> None:
        self.db_path: Path | None = None
        self.data: dict[str, Any] = {}
        self.photo_root: Path | None = None
        self.jpeg_root: Path | None = None
        self.thumbs_root: Path | None = None
        self.faces_root: Path | None = None
        self.photo_index: dict[str, dict[str, Any]] = {}

        self.save_lock = threading.Lock()
        self.score_lock = threading.Lock()
        self.scoring_state: dict[str, Any] = self._fresh_scoring_state()
        self.cluster_state: dict[str, Any] = self._fresh_cluster_state()
        self.opening_state: dict[str, Any] = self._fresh_opening_state()

    @staticmethod
    def _fresh_scoring_state() -> dict[str, Any]:
        return {"running": False, "idx": 0, "total": 0, "current": None,
                "started_at": None, "ended_at": None, "error": None}

    @staticmethod
    def _fresh_cluster_state() -> dict[str, Any]:
        return {"running": False, "phase": None, "idx": 0, "total": 0,
                "started_at": None, "ended_at": None, "error": None}

    @staticmethod
    def _fresh_opening_state() -> dict[str, Any]:
        return {"running": False, "phase": None, "message": None,
                "idx": 0, "total": 0, "current": None,
                "started_at": None, "ended_at": None, "error": None,
                "ready": False}

    def is_loaded(self) -> bool:
        return self.db_path is not None

    def load_db(self, db_path: Path) -> None:
        """Adopt an existing JSON db file as the current project."""
        db_path = db_path.resolve()
        data = db.load(db_path)
        photo_root = Path(data["photo_root"])
        jpeg_subdir = data.get("jpeg_subdir", "")
        jpeg_root = photo_root / jpeg_subdir if jpeg_subdir else photo_root

        self.db_path = db_path
        self.data = data
        self.photo_root = photo_root
        self.jpeg_root = jpeg_root
        self.thumbs_root = db_path.with_suffix(db_path.suffix + ".thumbs")
        self.faces_root = db_path.with_suffix(db_path.suffix + ".faces")
        self.thumbs_root.mkdir(exist_ok=True)
        self.faces_root.mkdir(exist_ok=True)
        self._rebuild_index()
        self.opening_state["ready"] = True

    def reload_data(self) -> None:
        assert self.db_path is not None
        new_data = db.load(self.db_path)
        self.data = new_data
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self.photo_index = {p["rel_path"]: p for p in self.data.get("photos", [])}

    def wipe_face_cache(self) -> None:
        if self.faces_root and self.faces_root.exists():
            shutil.rmtree(self.faces_root, ignore_errors=True)
            self.faces_root.mkdir(exist_ok=True)


# ----- helpers ------------------------------------------------------------

def _ensure_thumb(jpeg_root: Path, thumbs_root: Path, rel_path: str) -> Path:
    src = jpeg_root / rel_path
    dst = thumbs_root / rel_path
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        # Invalidate cached thumbs whose long edge is smaller than the current
        # target (picks up THUMB_LONG_EDGE bumps without manual cache wipe).
        try:
            with Image.open(dst) as old:
                if max(old.width, old.height) >= THUMB_LONG_EDGE - 4:
                    return dst
        except Exception:
            pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.Resampling.LANCZOS)
        img.convert("RGB").save(dst, "JPEG", quality=THUMB_QUALITY, optimize=True)
    return dst


def _ensure_face_crop(
    jpeg_root: Path,
    faces_root: Path,
    rel_path: str,
    bbox_xywh: list[int],
    face_idx: int,
    db_path: Path,
) -> Path:
    src = jpeg_root / rel_path
    dst = faces_root / f"{rel_path}.f{face_idx}.jpg"
    db_mtime = db_path.stat().st_mtime if db_path.exists() else 0
    src_mtime = src.stat().st_mtime
    if dst.exists() and dst.stat().st_mtime >= max(src_mtime, db_mtime):
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    x, y, w, h = bbox_xywh
    cx, cy = x + w / 2, y + h / 2
    half = max(w, h) * FACE_PADDING
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        crop_box = (
            max(0, int(cx - half)),
            max(0, int(cy - half)),
            min(img.width, int(cx + half)),
            min(img.height, int(cy + half)),
        )
        crop = img.crop(crop_box)
        crop.thumbnail((FACE_LONG_EDGE, FACE_LONG_EDGE), Image.Resampling.LANCZOS)
        crop.convert("RGB").save(dst, "JPEG", quality=FACE_QUALITY, optimize=True)
    return dst


def _scan_jpegs(jpeg_root: Path) -> set[str]:
    """Recursively find rel_paths of all supported images under jpeg_root."""
    found: set[str] = set()
    for img in jpeg_root.rglob("*"):
        if img.is_file() and _is_supported(img):
            found.add(str(img.relative_to(jpeg_root)))
    return found


def _summarize_other_files(jpeg_root: Path, limit: int = 4) -> str:
    """Sample non-image extensions found under jpeg_root for a helpful error msg."""
    counts: dict[str, int] = {}
    for p in jpeg_root.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in SUPPORTED_EXTS or p.name.startswith("._"):
            continue
        counts[ext or "(no-ext)"] = counts.get(ext or "(no-ext)", 0) + 1
    if not counts:
        return "folder is empty or contains only hidden files"
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return "found only " + ", ".join(f"{n} {ext}" for ext, n in top)


def _native_pick_folder(initial: str | None = None) -> dict[str, Any]:
    """Open a native folder dialog. Returns {path, cancelled, error}.
    macOS uses AppleScript (`osascript`); other platforms fall back to Tk."""
    if platform.system() == "Darwin":
        # `Finder activate` brings the choose-folder dialog to the foreground
        # reliably without requiring Accessibility/Automation permissions.
        prompt_safe = "Select photo folder"
        if initial and Path(initial).is_dir():
            choose = (
                f'choose folder with prompt "{prompt_safe}" '
                f'default location POSIX file "{initial}"'
            )
        else:
            choose = f'choose folder with prompt "{prompt_safe}"'
        script = (
            'tell application "Finder" to activate\n'
            f'set f to POSIX path of ({choose})\n'
            "return f"
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=600,
            )
        except FileNotFoundError:
            return {"path": None, "cancelled": False, "error": "osascript not found"}
        except subprocess.TimeoutExpired:
            return {"path": None, "cancelled": True, "error": None}
        if r.returncode != 0:
            stderr = (r.stderr or "").strip()
            cancelled = "User canceled" in stderr or "-128" in stderr
            return {"path": None, "cancelled": cancelled,
                    "error": None if cancelled else stderr or f"exit {r.returncode}"}
        out = r.stdout.strip().rstrip("/")
        return {"path": out or None, "cancelled": not out, "error": None}

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(initialdir=initial or str(Path.home()))
        root.destroy()
        return {"path": path or None, "cancelled": not path, "error": None}
    except Exception as exc:
        return {"path": None, "cancelled": False, "error": f"{type(exc).__name__}: {exc}"}


def _autodetect_jpeg_subdir(photo_dir: Path) -> str:
    """If `photo_dir` has no top-level supported images but a common subfolder
    does, return that subfolder's name. Otherwise return ''."""
    try:
        for entry in photo_dir.iterdir():
            if entry.is_file() and _is_supported(entry):
                return ""  # already has images at the root
    except OSError:
        return ""
    for candidate in ("JPEG", "jpeg", "JPG", "jpg", "JPEGS", "Photos", "photos"):
        if (photo_dir / candidate).is_dir():
            return candidate
    return ""


def _resolve_db_path(payload: OpenPayload) -> Path:
    if payload.db_path:
        return Path(payload.db_path).expanduser().resolve()
    return (Path(payload.photo_dir).expanduser() / "picks.json").resolve()


# ----- app construction ---------------------------------------------------

def create_app(initial_db_path: Path | None = None) -> FastAPI:
    ctx = AppContext()
    if initial_db_path is not None:
        ctx.load_db(initial_db_path)

    app = FastAPI(title="Picture Classifier")

    @app.middleware("http")
    async def _no_cache_for_web_assets(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))

    # ----- project state & open --------------------------------------

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        return {
            "ready": ctx.is_loaded() and ctx.opening_state.get("ready", False),
            "db_path": str(ctx.db_path) if ctx.db_path else None,
            "photo_root": str(ctx.photo_root) if ctx.photo_root else None,
            "opening": ctx.opening_state,
        }

    @app.post("/api/browse-folder")
    def browse_folder(payload: BrowsePayload | None = None) -> dict[str, Any]:
        initial = payload.initial if payload else None
        return _native_pick_folder(initial)

    @app.get("/api/recents")
    def get_recents() -> dict[str, Any]:
        return {"recents": userstate.get_recents()}

    @app.post("/api/recents/forget")
    def forget_recent(payload: ForgetPayload) -> dict[str, Any]:
        userstate.forget(Path(payload.db_path))
        return {"recents": userstate.get_recents()}

    def _open_worker(payload: OpenPayload, db_path: Path) -> None:
        try:
            photo_dir = Path(payload.photo_dir).expanduser().resolve()

            # Load any existing db first so we can inherit its jpeg_subdir.
            existing_data: dict[str, Any] | None = None
            if db_path.exists():
                try:
                    existing_data = db.load(db_path)
                except Exception:
                    existing_data = None

            # Effective jpeg_subdir resolution priority:
            #   user input > existing db > autodetect common subfolder
            jpeg_subdir = payload.jpeg_subdir or ""
            if not jpeg_subdir and existing_data:
                jpeg_subdir = existing_data.get("jpeg_subdir", "") or ""
            if not jpeg_subdir:
                jpeg_subdir = _autodetect_jpeg_subdir(photo_dir)
            jpeg_root = photo_dir / jpeg_subdir if jpeg_subdir else photo_dir

            ctx.opening_state["phase"] = "scanning"
            ctx.opening_state["message"] = (
                f"Scanning {jpeg_root}"
                + (f" (subfolder: {jpeg_subdir})" if jpeg_subdir else "")
                + "…"
            )
            if not jpeg_root.is_dir():
                raise RuntimeError(f"folder does not exist: {jpeg_root}")
            current_files = _scan_jpegs(jpeg_root)
            if not current_files:
                hint = _summarize_other_files(jpeg_root)
                raise RuntimeError(
                    f"no .jpg/.jpeg/.png images found under {jpeg_root} ({hint}); "
                    f"pick a different folder or set the JPEG subfolder under Advanced"
                )

            existing_files = (
                {p["rel_path"] for p in existing_data["photos"]}
                if existing_data else set()
            )
            needs_score = (
                existing_data is None
                or existing_data.get("scored_at") is None
                or current_files != existing_files
            )

            if needs_score:
                ctx.opening_state["phase"] = "scoring"
                ctx.opening_state["message"] = "Scoring photos with face detection…"
                from .scorer import run_scoring

                def score_cb(i: int, total: int, current: str | None) -> None:
                    ctx.opening_state["idx"] = i
                    ctx.opening_state["total"] = total
                    ctx.opening_state["current"] = current

                run_scoring(
                    photo_dir, jpeg_subdir, db_path,
                    with_faces=False, progress_cb=score_cb,
                )

                ctx.opening_state["phase"] = "clustering"
                ctx.opening_state["message"] = "Clustering faces…"
                ctx.opening_state["idx"] = 0
                ctx.opening_state["total"] = 0
                from .cluster import run_clustering

                def cluster_cb(phase: str, idx: int, total: int) -> None:
                    ctx.opening_state["message"] = f"Clustering: {phase}"
                    ctx.opening_state["idx"] = idx
                    ctx.opening_state["total"] = total

                run_clustering(db_path, progress_cb=cluster_cb)

            ctx.opening_state["phase"] = "loading"
            ctx.opening_state["message"] = "Loading project…"
            ctx.load_db(db_path)
            userstate.remember_open(db_path, photo_dir, jpeg_subdir)

            ctx.opening_state["phase"] = "done"
            ctx.opening_state["message"] = None
        except Exception as exc:
            ctx.opening_state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            ctx.opening_state["running"] = False
            ctx.opening_state["ended_at"] = datetime.now().isoformat()

    @app.post("/api/open")
    def open_project(payload: OpenPayload) -> dict[str, Any]:
        with ctx.score_lock:
            if ctx.opening_state["running"] or ctx.scoring_state["running"] or ctx.cluster_state["running"]:
                raise HTTPException(status_code=409, detail="another task is running")
            ctx.opening_state.update(
                ctx._fresh_opening_state(),
                running=True,
                started_at=datetime.now().isoformat(),
            )
        db_path = _resolve_db_path(payload)
        threading.Thread(target=_open_worker, args=(payload, db_path), daemon=True).start()
        return {"started": True, "db_path": str(db_path)}

    # ----- viewer data -----------------------------------------------

    def _require_loaded() -> None:
        if not ctx.is_loaded():
            raise HTTPException(status_code=409, detail="no project loaded")

    @app.get("/api/db")
    def get_db() -> dict[str, Any]:
        _require_loaded()
        return {
            "scored_at": ctx.data["scored_at"],
            "clustered_at": ctx.data.get("clustered_at"),
            "photo_root": ctx.data["photo_root"],
            "jpeg_subdir": ctx.data["jpeg_subdir"],
            "scene_grouping": ctx.data.get("scene_grouping", {"mode": "folder", "gap_minutes": 30}),
            "people": ctx.data.get("people", []),
            "photos": ctx.data["photos"],
        }

    @app.post("/api/decide")
    def decide(payload: DecidePayload) -> dict[str, Any]:
        _require_loaded()
        if ctx.scoring_state["running"]:
            raise HTTPException(status_code=409, detail="scoring in progress; decisions disabled")
        photo = ctx.photo_index.get(payload.rel_path)
        if photo is None:
            raise HTTPException(status_code=404, detail="photo not found")
        photo["decision"] = payload.decision
        photo["decided_at"] = datetime.now().isoformat() if payload.decision else None
        with ctx.save_lock:
            db.save(ctx.db_path, ctx.data)
        return photo

    @app.post("/api/decide/bulk")
    def decide_bulk(payload: BulkDecidePayload) -> dict[str, Any]:
        _require_loaded()
        if ctx.scoring_state["running"]:
            raise HTTPException(status_code=409, detail="scoring in progress; decisions disabled")
        now = datetime.now().isoformat() if payload.decision else None
        for rp in payload.rel_paths:
            photo = ctx.photo_index.get(rp)
            if photo is None:
                raise HTTPException(status_code=404, detail=f"photo not found: {rp}")
            photo["decision"] = payload.decision
            photo["decided_at"] = now
        with ctx.save_lock:
            db.save(ctx.db_path, ctx.data)
        return {"updated": len(payload.rel_paths)}

    # ----- scoring ---------------------------------------------------

    @app.post("/api/score")
    def start_score(payload: ScorePayload | None = None) -> dict[str, Any]:
        _require_loaded()
        with ctx.score_lock:
            if ctx.scoring_state["running"]:
                raise HTTPException(status_code=409, detail="scoring already running")
            ctx.scoring_state.update(
                running=True, idx=0, total=0, current=None,
                started_at=datetime.now().isoformat(), ended_at=None, error=None,
            )

        with ctx.save_lock:
            db.save(ctx.db_path, ctx.data)

        with_faces = payload.with_faces if payload else False

        def progress_cb(i: int, total: int, current: str | None) -> None:
            ctx.scoring_state["idx"] = i
            ctx.scoring_state["total"] = total
            ctx.scoring_state["current"] = current

        def runner() -> None:
            try:
                from .scorer import run_scoring
                run_scoring(
                    ctx.photo_root,
                    ctx.data["jpeg_subdir"],
                    ctx.db_path,
                    with_faces=with_faces,
                    progress_cb=progress_cb,
                )
                ctx.reload_data()
                ctx.wipe_face_cache()
            except Exception as exc:
                ctx.scoring_state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                ctx.scoring_state["running"] = False
                ctx.scoring_state["ended_at"] = datetime.now().isoformat()

        threading.Thread(target=runner, daemon=True).start()
        return {"started": True}

    @app.get("/api/score/status")
    def score_status() -> dict[str, Any]:
        return ctx.scoring_state

    # ----- clustering ------------------------------------------------

    @app.post("/api/cluster")
    def start_cluster(payload: ClusterPayload | None = None) -> dict[str, Any]:
        _require_loaded()
        with ctx.score_lock:
            if ctx.scoring_state["running"] or ctx.cluster_state["running"]:
                raise HTTPException(status_code=409, detail="another task is already running")
            ctx.cluster_state.update(
                running=True, phase="starting", idx=0, total=0,
                started_at=datetime.now().isoformat(), ended_at=None, error=None,
            )

        eps = payload.eps if payload else 0.55
        min_samples = payload.min_samples if payload else 3

        def progress_cb(phase: str, idx: int, total: int) -> None:
            ctx.cluster_state["phase"] = phase
            ctx.cluster_state["idx"] = idx
            ctx.cluster_state["total"] = total

        def runner() -> None:
            try:
                from .cluster import run_clustering
                run_clustering(ctx.db_path, eps=eps, min_samples=min_samples, progress_cb=progress_cb)
                ctx.reload_data()
                ctx.wipe_face_cache()
            except Exception as exc:
                ctx.cluster_state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                ctx.cluster_state["running"] = False
                ctx.cluster_state["ended_at"] = datetime.now().isoformat()

        threading.Thread(target=runner, daemon=True).start()
        return {"started": True}

    @app.get("/api/cluster/status")
    def cluster_status() -> dict[str, Any]:
        return ctx.cluster_state

    # ----- people ----------------------------------------------------

    @app.post("/api/people")
    def update_people(payload: PeoplePayload) -> dict[str, Any]:
        _require_loaded()
        if ctx.scoring_state["running"] or ctx.cluster_state["running"]:
            raise HTTPException(status_code=409, detail="another task is running")
        existing = {p["id"]: p for p in ctx.data.get("people", [])}
        if not existing:
            raise HTTPException(status_code=400, detail="no clusters yet; run /api/cluster first")
        for upd in payload.people:
            cur = existing.get(upd.id)
            if cur is None:
                raise HTTPException(status_code=400, detail=f"unknown person id: {upd.id}")
            cur["label"] = upd.label
            cur["priority"] = upd.priority
            cur["excluded"] = upd.excluded
        ctx.data["people"] = sorted(existing.values(), key=lambda p: p["priority"])
        with ctx.save_lock:
            db.save(ctx.db_path, ctx.data)
        return {"people": ctx.data["people"]}

    # ----- scene grouping --------------------------------------------

    @app.post("/api/scene-grouping")
    def set_scene_grouping(payload: SceneGroupingPayload) -> dict[str, Any]:
        _require_loaded()
        if ctx.scoring_state["running"] or ctx.cluster_state["running"]:
            raise HTTPException(status_code=409, detail="another task is running")
        photos = ctx.data["photos"]
        scenes.regroup(photos, ctx.jpeg_root, payload.mode, payload.gap_minutes)
        # Recompute per-scene auto-suggestions because the groups changed.
        by_scene: dict[str, list[dict[str, Any]]] = {}
        for p in photos:
            by_scene.setdefault(p["scene"], []).append(p)
        for items in by_scene.values():
            apply_scene_suggestions(items)
        ctx.data["scene_grouping"] = {"mode": payload.mode, "gap_minutes": payload.gap_minutes}
        with ctx.save_lock:
            db.save(ctx.db_path, ctx.data)
        return {"scene_grouping": ctx.data["scene_grouping"], "photos": photos}

    # ----- export ----------------------------------------------------

    @app.get("/api/export/picks/preview")
    def export_picks_preview() -> dict[str, Any]:
        _require_loaded()
        picks = [p for p in ctx.data["photos"] if p.get("decision") == "pick"]
        default_target = ctx.db_path.parent / f"{ctx.db_path.stem}.picks"
        return {"count": len(picks), "default_target": str(default_target)}

    @app.post("/api/export/picks")
    def export_picks(payload: ExportPayload | None = None) -> dict[str, Any]:
        _require_loaded()
        target = (
            Path(payload.target_dir).expanduser()
            if payload and payload.target_dir
            else ctx.db_path.parent / f"{ctx.db_path.stem}.picks"
        )
        target = target.resolve()
        target.mkdir(parents=True, exist_ok=True)
        picks = [p for p in ctx.data["photos"] if p.get("decision") == "pick"]
        copied: list[str] = []
        skipped: list[str] = []
        flatten = bool(payload and payload.flatten)
        used_names: set[str] = set()
        for photo in picks:
            src = ctx.jpeg_root / photo["rel_path"]
            if not src.is_file():
                skipped.append(photo["rel_path"])
                continue
            if flatten:
                base = Path(photo["rel_path"]).name
                stem, suffix = Path(base).stem, Path(base).suffix
                name = base
                n = 1
                while name in used_names or (target / name).exists():
                    name = f"{stem}_{n}{suffix}"
                    n += 1
                used_names.add(name)
                dst = target / name
            else:
                dst = target / photo["rel_path"]
                dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(photo["rel_path"])
        return {
            "target_dir": str(target),
            "copied": len(copied),
            "skipped": len(skipped),
            "missing": skipped,
        }

    # ----- image serving ---------------------------------------------

    @app.get("/img/{rel_path:path}")
    def get_image(rel_path: str) -> FileResponse:
        _require_loaded()
        path = (ctx.jpeg_root / rel_path).resolve()
        if ctx.jpeg_root.resolve() not in path.parents and path != ctx.jpeg_root.resolve():
            raise HTTPException(status_code=403, detail="forbidden")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media)

    @app.get("/thumb/{rel_path:path}")
    def get_thumb(rel_path: str) -> FileResponse:
        _require_loaded()
        src = (ctx.jpeg_root / rel_path).resolve()
        if ctx.jpeg_root.resolve() not in src.parents:
            raise HTTPException(status_code=403, detail="forbidden")
        if not src.is_file():
            raise HTTPException(status_code=404, detail="not found")
        thumb = _ensure_thumb(ctx.jpeg_root, ctx.thumbs_root, rel_path)
        return FileResponse(thumb, media_type="image/jpeg")

    @app.get("/face/{rel_path:path}")
    def get_face(rel_path: str, idx: int = 0) -> FileResponse:
        _require_loaded()
        photo = ctx.photo_index.get(rel_path)
        if photo is None:
            raise HTTPException(status_code=404, detail="photo not found")
        face_list = photo.get("faces") or []
        if idx < 0 or idx >= len(face_list):
            raise HTTPException(status_code=404, detail="face not found")
        face = face_list[idx]
        bbox = face["bbox_xywh"]
        crop_path = _ensure_face_crop(ctx.jpeg_root, ctx.faces_root, rel_path, bbox, idx, ctx.db_path)
        return FileResponse(crop_path, media_type="image/jpeg")

    return app


def serve(db_path: Path | None, host: str, port: int) -> None:
    import uvicorn
    app = create_app(db_path)
    print(f"\n  Picture Classifier — open http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
