"""Entry point for the bundled macOS .app.

When launched from Finder/Dock there are no CLI arguments, so we default to
`serve --open`. When run from the command line, all the regular `pcls`
subcommands still work.

Eagerly imports the package's heavy submodules so that PyInstaller's static
analysis pulls in their transitive native dependencies (opencv, onnxruntime,
insightface, scikit-learn, fastapi, etc.) into the bundle.
"""
from __future__ import annotations

import sys

# Static-analysis anchors: do NOT remove. These trigger PyInstaller to bundle
# the entire dependency graph. Lazy-imported in cli.py at runtime, but bundled
# here at build time.
from . import cli, cluster, db, scenes, scorer, server, userstate  # noqa: F401
from .scoring import blur, exposure, faces  # noqa: F401


def run() -> None:
    if len(sys.argv) == 1:
        sys.argv.extend(["serve", "--open"])
    cli.main()


if __name__ == "__main__":
    run()
