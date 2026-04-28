# Picture Classifier

Score photos for blur, exposure, and face presence — then cull them fast in a
local web viewer with face-cluster-aware sorting and pick/review/reject
decisions.

Designed for the post-shoot triage workflow on a personal collection of a few
hundred to a few thousand JPEGs. Runs entirely on your machine; no network.

## Features

- **Per-photo scoring**: Laplacian blur, brightness exposure, optional
  closed-eye detection.
- **Per-scene auto-suggestion**: top 30% pick / middle review / bottom 30%
  reject, normalized within each scene.
- **Face clustering**: detects faces with `insightface` and clusters them
  per-person via DBSCAN on embeddings.
- **Drag-and-drop people priority**: rank face clusters by importance; photos
  containing higher-priority people sort to the top within each scene.
- **Exclude clusters**: hide irrelevant clusters (background people, false
  positives) from sorting and chips.
- **Scene grouping**: by folder structure, or by EXIF capture-time gaps
  (configurable in minutes). Switch any time without re-scoring.
- **Bulk actions**: reject all undecided in a scene; export all picks to a
  folder (preserving structure or flattened).
- **Keyboard-driven culling**: `R` reject, `V` review, `A`/`P` pick, `U` undo,
  arrow keys to navigate, `Enter` to open the modal viewer, `[`/`]` for pages.
- **Project history**: recent folders are remembered so you can reopen them
  from the landing page.

## Requirements

- Python 3.12+
- macOS (developed on macOS 15; Linux/Windows likely work but the native
  folder-picker uses AppleScript on macOS and Tk elsewhere)
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## Install

Pick whichever installer you prefer. The first run downloads the
`insightface` `buffalo_l` model (~280 MB) into `~/.insightface/`.

### macOS app bundle (no terminal needed)

Grab the latest `.dmg` from
[Releases](https://github.com/son-engr-kr/picture-classifier/releases),
drag **Picture Classifier.app** into `/Applications`, and double-click to
launch. The app opens the landing page in your default browser
automatically.

Apple Silicon (arm64) only for now. The app is unsigned, so the first
launch needs **Right-click → Open → Open** to clear Gatekeeper.

### `uv tool` (cross-platform — macOS, Linux, Windows)

If you don't have [uv](https://github.com/astral-sh/uv) yet:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then:

```bash
uv tool install git+https://github.com/son-engr-kr/picture-classifier
pcls serve
```

To upgrade later: `uv tool upgrade picture-classifier`.

### Homebrew (macOS / Linux)

```bash
brew tap son-engr-kr/picture-classifier
brew install picture-classifier
pcls serve
```

### From source

```bash
git clone https://github.com/son-engr-kr/picture-classifier.git
cd picture-classifier
uv sync
uv run pcls serve
```

## Usage

### Open the landing page

```bash
uv run pcls serve
```

This starts the server at <http://127.0.0.1:8765> and opens a landing page
where you can pick a photo folder. Recent projects are listed there too.

### Open an existing project directly

```bash
uv run pcls serve /path/to/picks.json
```

### From the CLI

You can also score and cluster from the terminal:

```bash
uv run pcls score /path/to/photos -o /path/to/picks.json
uv run pcls cluster /path/to/picks.json
uv run pcls report /path/to/picks.json
```

`pcls score --help` for all options.

## Folder layouts

All three of these are supported. Switch scene grouping (by folder vs. by
time gap) inside the app.

```
# Flat
my-photos/
  IMG_0001.JPG
  IMG_0002.JPG
  picks.json   (auto-created)
```

```
# Pre-grouped folders (each subfolder becomes a scene)
wedding-2025/
  Scene_001/
    IMG_0001.JPG
  Scene_002/
    IMG_0010.JPG
  picks.json
```

```
# Project root with a JPEG subfolder (set "JPEG subfolder" under Advanced)
shoot/
  RAW/
  JPEG/
    Scene_001/
      IMG_0001.JPG
  picks.json   (at shoot/)
```

Supported extensions: `.jpg`, `.jpeg`, `.png` (case-insensitive). Other files
(videos, RAW, sidecars) are ignored.

## How decisions are persisted

Everything lives in `<photo_dir>/picks.json` next to your images. Re-scoring
preserves your pick/review/reject decisions by relative path. Re-clustering
resets cluster labels and priorities (face indices change), but per-photo
decisions are kept.

Caches (`picks.json.thumbs/`, `picks.json.faces/`,
`picks.json.embeddings.npy`) are recreated on demand and safe to delete.

## Releasing (maintainer notes)

Bump the version, tag, and push. The
`Update Homebrew tap` GitHub Action picks up `v*` tags and updates the
[homebrew tap](https://github.com/son-engr-kr/homebrew-picture-classifier)
formula automatically.

```bash
# bump version in pyproject.toml first
git tag v0.1.1
git push origin v0.1.1
```

The action requires a `TAP_TOKEN` repository secret — a fine-grained PAT
with `Contents: Write` permission on `son-engr-kr/homebrew-picture-classifier`.

## License

MIT — see [LICENSE](LICENSE).
