"""Entry point for the bundled macOS .app.

When launched from Finder/Dock there are no CLI arguments, so we default to
`serve --open`. When run from the command line, all the regular `pcls`
subcommands still work."""
from __future__ import annotations

import sys

from .cli import main


def run() -> None:
    if len(sys.argv) == 1:
        sys.argv.extend(["serve", "--open"])
    main()


if __name__ == "__main__":
    run()
