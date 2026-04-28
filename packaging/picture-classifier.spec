# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Picture Classifier (macOS .app bundle).

Run from the project root:
    uvx --with-requirements pyproject.toml pyinstaller packaging/picture-classifier.spec
"""
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
WEB_DIR = ROOT / "src" / "picture_classifier" / "web"
ENTRY = ROOT / "src" / "picture_classifier" / "app_entry.py"
APP_VERSION = os.environ.get("APP_VERSION", "0.0.0-dev")

block_cipher = None

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[(str(WEB_DIR), "picture_classifier/web")],
    hiddenimports=[
        # uvicorn pulls these dynamically; PyInstaller's static analysis misses them.
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="picture-classifier",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="picture-classifier",
)

app = BUNDLE(
    coll,
    name="Picture Classifier.app",
    icon=None,
    bundle_identifier="kr.son-engr.picture-classifier",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": "Picture Classifier",
        "CFBundleDisplayName": "Picture Classifier",
        "CFBundleIdentifier": "kr.son-engr.picture-classifier",
        "CFBundleVersion": APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
    },
)
