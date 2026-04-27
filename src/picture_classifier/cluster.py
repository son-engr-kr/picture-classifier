"""Cluster face embeddings into person groups using DBSCAN with cosine distance.

Reads embeddings from `<db_path>.embeddings.npy`, runs DBSCAN, and writes
`people: [...]` and `face.person_id` back into the JSON db.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import DBSCAN

from . import db


DEFAULT_EPS = 0.55           # cosine distance threshold for DBSCAN
DEFAULT_MIN_SAMPLES = 3      # smallest valid cluster size


def _representative_face(
    photos: list[dict[str, Any]],
    member_ids: list[tuple[int, int]],
    embeddings: np.ndarray,
) -> dict[str, Any]:
    """Pick the face closest to the cluster centroid as the representative."""
    rows = [embeddings[photos[pi]["faces"][fi]["embedding_idx"]] for pi, fi in member_ids]
    centroid = np.mean(np.stack(rows, axis=0), axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-9
    best_dist = float("inf")
    best = member_ids[0]
    for (pi, fi), emb in zip(member_ids, rows):
        emb_n = emb / (np.linalg.norm(emb) + 1e-9)
        d = float(1.0 - np.dot(emb_n, centroid))
        if d < best_dist:
            best_dist = d
            best = (pi, fi)
    pi, fi = best
    return {"rel_path": photos[pi]["rel_path"], "face_idx": fi}


def run_clustering(
    db_path: Path,
    eps: float = DEFAULT_EPS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    progress_cb=None,
) -> None:
    """Run DBSCAN over saved embeddings and persist `people[]` + `face.person_id`."""
    data = db.load(db_path)
    photos = data["photos"]

    emb_path = db_path.with_suffix(db_path.suffix + ".embeddings.npy")
    assert emb_path.is_file(), (
        f"embeddings sidecar not found at {emb_path}; run `pcls score` first"
    )
    embeddings = np.load(emb_path)
    n = embeddings.shape[0]

    # Build a parallel list of (photo_idx, face_idx) rows so we can map cluster
    # labels back to faces in the db.
    row_to_face: list[tuple[int, int]] = []
    for pi, photo in enumerate(photos):
        for fi, face in enumerate(photo.get("faces", [])):
            ei = face.get("embedding_idx")
            if ei is None or ei >= n:
                continue
            assert ei == len(row_to_face), (
                f"embedding_idx mismatch at photo {pi} face {fi}: "
                f"got {ei}, expected {len(row_to_face)}"
            )
            row_to_face.append((pi, fi))
            face["person_id"] = None  # reset

    if progress_cb:
        progress_cb("normalizing", 0, n)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / (norms + 1e-9)

    if progress_cb:
        progress_cb("clustering", 0, n)
    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(normalized)

    # Group rows by cluster label (ignore -1 noise).
    clusters: dict[int, list[tuple[int, int]]] = {}
    for row, label in enumerate(labels):
        if label < 0:
            continue
        clusters.setdefault(int(label), []).append(row_to_face[row])

    # Sort by size desc — biggest cluster becomes Person 1.
    sorted_clusters = sorted(clusters.items(), key=lambda kv: -len(kv[1]))

    if progress_cb:
        progress_cb("building people", 0, len(sorted_clusters))

    people: list[dict[str, Any]] = []
    for new_idx, (_orig_label, members) in enumerate(sorted_clusters):
        pid = f"p{new_idx}"
        for pi, fi in members:
            photos[pi]["faces"][fi]["person_id"] = pid
        ref = _representative_face(photos, members, embeddings)
        people.append({
            "id": pid,
            "label": f"Person {new_idx + 1}",
            "priority": new_idx + 1,
            "excluded": False,
            "count": len(members),
            "ref": ref,
        })
        if progress_cb:
            progress_cb("building people", new_idx + 1, len(sorted_clusters))

    data["clustered_at"] = datetime.now().isoformat()
    data["people"] = people
    db.save(db_path, data)
