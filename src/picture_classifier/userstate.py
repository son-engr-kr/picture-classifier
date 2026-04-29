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


def remember_open(
    db_path: Path,
    photo_dir: Path,
    jpeg_subdir: str,
    *,
    kind: str = "legacy",
    project_dir: Path | None = None,
) -> None:
    data = _load()
    entry: dict[str, Any] = {
        "kind": kind,
        "db_path": str(db_path),
        "photo_dir": str(photo_dir),
        "jpeg_subdir": jpeg_subdir,
        "opened_at": datetime.now().isoformat(),
    }
    if project_dir is not None:
        entry["project_dir"] = str(project_dir)
        entry["name"] = project_dir.name
    else:
        entry["name"] = photo_dir.name

    def _key(r: dict[str, Any]) -> str:
        return r.get("project_dir") or r.get("db_path") or ""
    new_key = entry.get("project_dir") or entry["db_path"]
    recents = [r for r in data.get("recents", []) if _key(r) != new_key]
    recents.insert(0, entry)
    data["recents"] = recents[:MAX_RECENTS]
    data["last_db_path"] = str(db_path)
    _save(data)


def forget(key: Path) -> None:
    """Remove a recent entry. `key` may be a db_path or project_dir."""
    data = _load()
    s = str(key)
    data["recents"] = [
        r for r in data.get("recents", [])
        if r.get("db_path") != s and r.get("project_dir") != s
    ]
    if data.get("last_db_path") == s:
        data["last_db_path"] = None
    _save(data)
