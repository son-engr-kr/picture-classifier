"""Blur score via variance of Laplacian. Higher = sharper."""
from __future__ import annotations

import cv2


def blur_score(img_path: str) -> float:
    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    assert img is not None, f"failed to read {img_path}"
    h, w = img.shape
    longest = max(h, w)
    if longest > 1024:
        scale = 1024 / longest
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())
