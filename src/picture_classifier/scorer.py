"""Scan JPEGs, compute per-photo scores, normalize per scene, write JSON."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import click
import numpy as np

from . import db
from .scoring import blur as blur_mod
from .scoring import exposure as exp_mod
from .scoring import faces as faces_mod

EYE_CLOSED_THRESHOLD = 0.18  # EAR below this is treated as "eyes closed"


SUPPORTED_EXTS = {".jpg", ".jpeg", ".png"}


def _is_supported(p: Path) -> bool:
    return p.suffix.lower() in SUPPORTED_EXTS and not p.name.startswith("._")


def _collect_jpegs(jpeg_root: Path) -> list[tuple[str, Path]]:
    """Recursively find supported images. Scene name = first subdir component
    of the path relative to `jpeg_root`; loose files at the root → '(none)'."""
    out: list[tuple[str, Path]] = []
    for img in sorted(jpeg_root.rglob("*")):
        if not (img.is_file() and _is_supported(img)):
            continue
        rel = img.relative_to(jpeg_root)
        scene = rel.parts[0] if len(rel.parts) > 1 else "(none)"
        out.append((scene, img))
    return out


def _load_existing_decisions(db_path: Path) -> dict[str, tuple[Any, Any]]:
    if not db_path.exists():
        return {}
    old = db.load(db_path)
    return {
        p["rel_path"]: (p.get("decision"), p.get("decided_at"))
        for p in old.get("photos", [])
    }


def apply_scene_suggestions(items: list[dict[str, Any]]) -> None:
    """Mutates items in place to set 'auto_suggestion' based on per-scene normalized scores."""
    n = len(items)
    if n == 0:
        return
    if n == 1:
        items[0]["auto_suggestion"] = "review"
        return

    blurs = np.array([p["scores"]["blur"] for p in items])
    brights = np.array([p["scores"]["brightness"] for p in items])
    eyes = np.array([
        np.nan if p["scores"]["eye_open"] is None else p["scores"]["eye_open"]
        for p in items
    ])

    blur_pct = blurs.argsort().argsort() / max(n - 1, 1)
    b_mean = float(brights.mean())
    b_std = float(brights.std()) + 1e-6
    b_zscore = np.abs((brights - b_mean) / b_std)

    badness = (1.0 - blur_pct) + 0.3 * b_zscore
    eyes_closed_mask = ~np.isnan(eyes) & (eyes < EYE_CLOSED_THRESHOLD)
    badness = badness + eyes_closed_mask.astype(float) * 1.0

    for i, p in enumerate(items):
        p["scores"]["blur_pct"] = float(blur_pct[i])
        p["scores"]["exposure_zscore"] = float(b_zscore[i])
        p["scores"]["badness"] = float(badness[i])

    n_pick = max(1, int(round(n * 0.3)))
    n_reject = max(1, int(round(n * 0.3)))
    if n_pick + n_reject > n:
        n_reject = max(0, n - n_pick)

    order = np.argsort(badness)
    for rank, idx in enumerate(order):
        if rank < n_pick:
            items[idx]["auto_suggestion"] = "pick"
        elif rank >= n - n_reject:
            items[idx]["auto_suggestion"] = "reject"
        else:
            items[idx]["auto_suggestion"] = "review"


def _score_one(
    scene: str,
    jpg: Path,
    jpeg_root: Path,
    face_detect,
    existing: dict[str, tuple[Any, Any]],
    embedding_sink: list[np.ndarray],
) -> dict[str, Any]:
    blur_v = blur_mod.blur_score(str(jpg))
    bright_v = exp_mod.brightness(str(jpg))
    face_list, width, height = face_detect(str(jpg))
    # Pop embeddings into a separate list; write them to a numpy sidecar later.
    for f in face_list:
        emb = f.pop("embedding", None)
        if emb is None:
            f["embedding_idx"] = None
        else:
            f["embedding_idx"] = len(embedding_sink)
            embedding_sink.append(emb)
        f.setdefault("person_id", None)
    ears = [f["ear"] for f in face_list if f.get("ear") is not None]
    eye_v = min(ears) if ears else None
    rel = str(jpg.relative_to(jpeg_root))
    prior_decision, prior_decided_at = existing.get(rel, (None, None))
    return {
        "rel_path": rel,
        "scene": scene,
        "width": width,
        "height": height,
        "faces": face_list,
        "scores": {
            "blur": blur_v,
            "brightness": bright_v,
            "eye_open": eye_v,
            "blur_pct": None,
            "exposure_zscore": None,
            "badness": None,
        },
        "auto_suggestion": None,
        "decision": prior_decision,
        "decided_at": prior_decided_at,
    }


def run_scoring(
    photo_dir: Path,
    jpeg_subdir: str,
    db_path: Path,
    with_faces: bool,
    limit: int | None = None,
    progress_cb=None,
) -> None:
    """If `progress_cb` is given, it's called as `cb(idx, total, current_rel_path)`
    before each photo, and once more with `idx == total` and current=None at the end.
    Otherwise a click progress bar prints to stdout."""
    jpeg_root = photo_dir / jpeg_subdir
    assert jpeg_root.is_dir(), f"not a directory: {jpeg_root}"

    photos = _collect_jpegs(jpeg_root)
    if limit is not None:
        photos = photos[:limit]
    if progress_cb is None:
        click.echo(f"Found {len(photos)} JPEGs under {jpeg_root}")

    existing = _load_existing_decisions(db_path)
    if progress_cb is None and existing:
        click.echo(f"Preserving {sum(1 for d in existing.values() if d[0] is not None)} prior decisions")

    if with_faces:
        from .scoring import eyes as eyes_mod
        if progress_cb is None:
            click.echo("Face mesh + eye-open detection enabled (mediapipe)")
        face_detect = eyes_mod.detect
    else:
        if progress_cb is None:
            click.echo("Face detection + embedding enabled (insightface buffalo_l)")
        face_detect = faces_mod.detect

    scored: list[dict[str, Any]] = []
    embedding_sink: list[np.ndarray] = []
    if progress_cb is None:
        with click.progressbar(photos, label="Scoring", show_pos=True) as bar:
            for scene, jpg in bar:
                scored.append(_score_one(scene, jpg, jpeg_root, face_detect, existing, embedding_sink))
    else:
        for i, (scene, jpg) in enumerate(photos):
            progress_cb(i, len(photos), str(jpg.relative_to(jpeg_root)))
            scored.append(_score_one(scene, jpg, jpeg_root, face_detect, existing, embedding_sink))
        progress_cb(len(photos), len(photos), None)

    by_scene: dict[str, list[dict[str, Any]]] = {}
    for p in scored:
        by_scene.setdefault(p["scene"], []).append(p)
    for scene, items in by_scene.items():
        apply_scene_suggestions(items)

    # Persist embeddings to numpy sidecar; clusters wipe on re-score because
    # face indices may have changed.
    emb_path = db_path.with_suffix(db_path.suffix + ".embeddings.npy")
    if embedding_sink:
        emb_arr = np.stack(embedding_sink, axis=0).astype(np.float32)
    else:
        emb_arr = np.zeros((0, 512), dtype=np.float32)
    np.save(emb_path, emb_arr)

    data = db.init_db(photo_dir, jpeg_subdir)
    data["scored_at"] = datetime.now().isoformat()
    data["clustered_at"] = None
    data["people"] = []
    data["photos"] = scored
    db.save(db_path, data)

    if progress_cb is None:
        summary = {"pick": 0, "review": 0, "reject": 0}
        n_with_faces = 0
        total_faces = 0
        for p in scored:
            summary[p["auto_suggestion"]] += 1
            if p["faces"]:
                n_with_faces += 1
                total_faces += len(p["faces"])
        click.echo(f"\nWrote {db_path}")
        click.echo(
            f"Auto suggestions — pick: {summary['pick']}, "
            f"review: {summary['review']}, reject: {summary['reject']}"
        )
        click.echo(
            f"Faces detected — {n_with_faces}/{len(scored)} photos contain faces "
            f"({total_faces} total)"
        )
