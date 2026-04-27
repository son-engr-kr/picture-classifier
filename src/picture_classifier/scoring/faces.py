"""Face detection + 512-d ArcFace embedding via InsightFace (buffalo_l).

The first call lazily loads the model into a process-global singleton.
Source images are downsampled before detection for speed; bboxes are mapped
back to original-image pixel coordinates.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

DETECT_LONG_EDGE = 1600

_app = None


def _get_app():
    global _app
    if _app is None:
        from insightface.app import FaceAnalysis
        _app = FaceAnalysis(
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        _app.prepare(ctx_id=-1, det_size=(640, 640))
    return _app


def detect(img_path: str) -> tuple[list[dict[str, Any]], int, int]:
    """Detect faces and compute embeddings.

    Returns (faces, original_width, original_height).
    Each face dict contains:
        - bbox_xywh: [x, y, w, h] in original-image pixels
        - ear: None  (kept for API compat with eyes.py)
        - det_score: detection confidence
        - embedding: numpy float32 array of length 512 (popped before JSON write)
    """
    img = cv2.imread(img_path)
    assert img is not None, f"failed to read {img_path}"
    h, w = img.shape[:2]
    longest = max(h, w)
    scale = 1.0
    img_small = img
    if longest > DETECT_LONG_EDGE:
        scale = DETECT_LONG_EDGE / longest
        img_small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    app = _get_app()
    raw = app.get(img_small)

    inv = 1.0 / scale
    faces: list[dict[str, Any]] = []
    for f in raw:
        x1, y1, x2, y2 = f.bbox
        bx = int(max(0, x1) * inv)
        by = int(max(0, y1) * inv)
        bw = int(max(1, (x2 - x1)) * inv)
        bh = int(max(1, (y2 - y1)) * inv)
        faces.append({
            "bbox_xywh": [bx, by, bw, bh],
            "ear": None,
            "det_score": float(f.det_score),
            "embedding": f.embedding.astype(np.float32),
        })
    return faces, w, h
