"""User-level state in `~/.picture-classifier/state.json`: recents + last db."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".picture-classifier"
STATE_FILE = CONFIG_DIR / "state.json"
MAX_RECENTS = 10


def _load() -> dict[str, Any]:
    if not STATE_FILE.is_file():
        return {"recents": [], "last_db_path": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"recents": [], "last_db_path": None}


def _save(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def get_recents() -> list[dict[str, Any]]:
    return _load().get("recents", [])


def get_last_db_path() -> Path | None:
    p = _load().get("last_db_path")
    return Path(p) if p else None


def remember_open(db_path: Path, photo_dir: Path, jpeg_subdir: str) -> None:
    data = _load()
    entry = {
        "db_path": str(db_path),
        "photo_dir": str(photo_dir),
        "jpeg_subdir": jpeg_subdir,
        "opened_at": datetime.now().isoformat(),
    }
    recents = [r for r in data.get("recents", []) if r.get("db_path") != str(db_path)]
    recents.insert(0, entry)
    data["recents"] = recents[:MAX_RECENTS]
    data["last_db_path"] = str(db_path)
    _save(data)


def forget(db_path: Path) -> None:
    data = _load()
    data["recents"] = [r for r in data.get("recents", []) if r.get("db_path") != str(db_path)]
    if data.get("last_db_path") == str(db_path):
        data["last_db_path"] = None
    _save(data)
