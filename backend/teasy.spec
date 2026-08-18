# PyInstaller spec for TEasy.
# Build with:  pyinstaller tally_ai.spec   (run from inside backend/, on Windows)
#
# Output: backend/dist/TEasy/TEasy.exe  (a folder you can zip and hand to
# yourself on another PC, or point a Windows installer tool like Inno Setup at)

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# frontend/dist (built via `npm run build`) gets bundled inside the exe as
# "frontend_dist" — see app/paths.py get_frontend_dir(), which looks for
# exactly that folder name at runtime.
FRONTEND_DIST = os.path.join("..", "frontend", "dist")

# PyInstaller's static analysis sometimes misses these two because of how
# they lazy-import internals — collect everything explicitly so this stops
# recurring across rebuilds.
HIDDEN_IMPORTS = (
    [
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
    ]
    + collect_submodules("openpyxl")
    + collect_submodules("pandas")
)

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=[(FRONTEND_DIST, "frontend_dist")],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TEasy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep the console window - it's the app's log/off switch
    icon=None,     # put an .ico path here later if you want a custom icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="TEasy",
)
