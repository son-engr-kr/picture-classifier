"""CLI entry point: `pcls score`, `pcls serve`, `pcls report`."""
from __future__ import annotations

from pathlib import Path

import click


@click.group()
def main() -> None:
    """Picture classifier — score photos and cull via web viewer."""


@main.command()
@click.argument(
    "photo_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--jpeg-subdir",
    default="JPEG",
    show_default=True,
    help="Subdirectory under photo_dir holding the JPEGs (with optional Scene_* subfolders).",
)
@click.option(
    "--db-path",
    "-o",
    type=click.Path(path_type=Path),
    default=Path("picks.json"),
    show_default=True,
    help="Output JSON path.",
)
@click.option(
    "--with-faces",
    is_flag=True,
    help="Also detect closed eyes via mediapipe (requires --extra faces).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Score only the first N photos (for smoke testing).",
)
def score(
    photo_dir: Path,
    jpeg_subdir: str,
    db_path: Path,
    with_faces: bool,
    limit: int | None,
) -> None:
    """Compute scores and write JSON db."""
    from .scorer import run_scoring
    run_scoring(photo_dir, jpeg_subdir, db_path, with_faces, limit)


@main.command()
@click.argument(
    "db_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind address. Use 0.0.0.0 to expose on LAN.",
)
@click.option(
    "--port",
    type=int,
    default=8765,
    show_default=True,
)
@click.option(
    "--open/--no-open",
    "open_browser",
    default=False,
    help="Open the web viewer in the default browser after starting.",
)
def serve(db_path: Path | None, host: str, port: int, open_browser: bool) -> None:
    """Launch the web viewer. With no DB_PATH the landing page lets you pick a folder."""
    from .server import serve as run_serve
    run_serve(db_path, host, port, open_browser=open_browser)


@main.command()
@click.argument(
    "db_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--eps", default=0.55, show_default=True, help="DBSCAN cosine distance threshold")
@click.option("--min-samples", default=3, show_default=True, help="DBSCAN min samples per cluster")
def cluster(db_path: Path, eps: float, min_samples: int) -> None:
    """Cluster detected faces by identity (writes `people[]` to the db)."""
    from .cluster import run_clustering
    click.echo("Clustering faces…")
    run_clustering(db_path, eps=eps, min_samples=min_samples)
    from . import db as db_mod
    data = db_mod.load(db_path)
    click.echo(f"Found {len(data.get('people', []))} person clusters:")
    for p in data.get("people", []):
        click.echo(f"  {p['id']:>4} priority={p['priority']} count={p['count']} label='{p['label']}'")


@main.command()
@click.argument(
    "db_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def report(db_path: Path) -> None:
    """Print decision summary."""
    from . import db as db_mod
    data = db_mod.load(db_path)
    photos = data["photos"]
    auto = {"pick": 0, "review": 0, "reject": 0, None: 0}
    decided = {"pick": 0, "review": 0, "reject": 0, None: 0}
    for p in photos:
        auto[p.get("auto_suggestion")] += 1
        decided[p.get("decision")] += 1
    click.echo(f"Total: {len(photos)}")
    click.echo(
        f"Auto    — pick: {auto['pick']}, review: {auto['review']}, "
        f"reject: {auto['reject']}, none: {auto[None]}"
    )
    click.echo(
        f"Decided — pick: {decided['pick']}, review: {decided['review']}, "
        f"reject: {decided['reject']}, undecided: {decided[None]}"
    )
