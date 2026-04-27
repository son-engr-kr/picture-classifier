"""Scene grouping: from folder structure or from EXIF capture-time gaps."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

_DATETIME_TAG = next(t for t, n in ExifTags.TAGS.items() if n == "DateTimeOriginal")


def read_capture_time(image_path: Path) -> datetime | None:
    """Best-effort EXIF DateTimeOriginal read. None if missing/invalid."""
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            ifd = exif.get_ifd(0x8769)  # ExifOffset sub-IFD
            raw = ifd.get(_DATETIME_TAG)
        if not raw:
            return None
        return datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S")
    except (OSError, ValueError, AttributeError):
        return None


def group_by_folder(photos: list[dict[str, Any]]) -> None:
    """Set photo['scene'] from the first directory component of rel_path.
    Loose files (no subdir) become '(none)'."""
    for p in photos:
        parts = p["rel_path"].split("/", 1)
        p["scene"] = parts[0] if len(parts) > 1 else "(none)"


def group_by_time_gap(
    photos: list[dict[str, Any]],
    jpeg_root: Path,
    gap_minutes: int = 30,
) -> None:
    """Sort photos by capture time, start a new scene whenever the gap exceeds
    `gap_minutes`. Photos missing EXIF time go to '(no_time)'."""
    times: list[datetime | None] = [
        read_capture_time(jpeg_root / p["rel_path"]) for p in photos
    ]
    timed = sorted(
        ((i, t) for i, t in enumerate(times) if t is not None),
        key=lambda kv: kv[1],
    )
    gap = timedelta(minutes=gap_minutes)
    scene_idx = 0
    last_t: datetime | None = None
    for i, t in timed:
        if last_t is None or (t - last_t) > gap:
            scene_idx += 1
        photos[i]["scene"] = f"Scene {scene_idx:02d}"
        last_t = t
    for i, t in enumerate(times):
        if t is None:
            photos[i]["scene"] = "(no_time)"


def regroup(
    photos: list[dict[str, Any]],
    jpeg_root: Path,
    mode: str,
    gap_minutes: int = 30,
) -> None:
    """Apply the chosen mode in-place."""
    assert mode in ("folder", "time_gap"), f"unknown scene mode: {mode}"
    if mode == "folder":
        group_by_folder(photos)
    else:
        group_by_time_gap(photos, jpeg_root, gap_minutes)
