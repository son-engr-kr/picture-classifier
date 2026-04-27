"""Atomic JSON-backed store for photo decisions and scores."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DB_VERSION = 1


def load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("version") == DB_VERSION, f"unsupported db version: {data.get('version')}"
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def init_db(photo_root: Path, jpeg_subdir: str) -> dict[str, Any]:
    return {
        "version": DB_VERSION,
        "photo_root": str(photo_root),
        "jpeg_subdir": jpeg_subdir,
        "scored_at": None,
        "photos": [],
    }
