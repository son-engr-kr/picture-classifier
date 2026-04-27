"""Mean luminance per photo. Outlier-ness is computed per scene in the orchestrator."""
from __future__ import annotations

import cv2
import numpy as np


def brightness(img_path: str) -> float:
    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    assert img is not None, f"failed to read {img_path}"
    h, w = img.shape
    longest = max(h, w)
    if longest > 512:
        scale = 512 / longest
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return float(np.mean(img))
