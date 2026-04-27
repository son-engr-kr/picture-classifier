"""Face Mesh + Eye Aspect Ratio (EAR) via mediapipe.

Returns face dicts compatible with `faces.detect` plus an `ear` value per face.
EAR is the *minimum* of left- and right-eye openness on that face — so a half-blink
on one eye scores low.
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

_face_mesh = None

LEFT_EYE = (33, 160, 158, 133, 153, 144)
RIGHT_EYE = (263, 387, 385, 362, 380, 373)


def _get_face_mesh():
    global _face_mesh
    if _face_mesh is None:
        import mediapipe as mp
        _face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=4,
            refine_landmarks=False,
            min_detection_confidence=0.4,
        )
    return _face_mesh


def _ear(landmarks, idx) -> float:
    pts = np.array([(landmarks[i].x, landmarks[i].y) for i in idx])
    a = np.linalg.norm(pts[1] - pts[5])
    b = np.linalg.norm(pts[2] - pts[4])
    c = np.linalg.norm(pts[0] - pts[3])
    return float((a + b) / (2.0 * c + 1e-9))


def detect(img_path: str) -> tuple[list[dict[str, Any]], int, int]:
    img = cv2.imread(img_path)
    assert img is not None, f"failed to read {img_path}"
    h, w = img.shape[:2]
    longest = max(h, w)
    scale = 1.0
    if longest > 1280:
        scale = 1280 / longest
        img_small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        img_small = img
    sh, sw = img_small.shape[:2]
    inv = 1.0 / scale

    rgb = cv2.cvtColor(img_small, cv2.COLOR_BGR2RGB)
    result = _get_face_mesh().process(rgb)
    if not result.multi_face_landmarks:
        return [], w, h

    faces: list[dict[str, Any]] = []
    for face_lm in result.multi_face_landmarks:
        xs = [lm.x for lm in face_lm.landmark]
        ys = [lm.y for lm in face_lm.landmark]
        x0, y0 = max(0.0, min(xs)), max(0.0, min(ys))
        x1, y1 = min(1.0, max(xs)), min(1.0, max(ys))
        bbox = [
            int(x0 * sw * inv),
            int(y0 * sh * inv),
            int((x1 - x0) * sw * inv),
            int((y1 - y0) * sh * inv),
        ]
        ear = min(_ear(face_lm.landmark, LEFT_EYE), _ear(face_lm.landmark, RIGHT_EYE))
        faces.append({"bbox_xywh": bbox, "ear": ear})
    return faces, w, h
