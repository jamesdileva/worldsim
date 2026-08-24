# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the WorldSim desktop app (Sprint 54).

Build:  pyinstaller worldsim.spec
Output: dist/worldsim/worldsim.exe (onedir — fast startup, web/ shipped
        as data next to the binary).
"""
import os

block_cipher = None

a = Analysis(
    ["entry_worldsim.py"],
    pathex=[os.path.join(".", "src")],
    binaries=[],
    datas=[
        # The web frontend must ship inside the package.
        ("src/worldsim/web", "worldsim/web"),
    ],
    hiddenimports=[
        # uvicorn pulls these dynamically; PyInstaller misses them.
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
        "anyio._backends._asyncio",
        # pywebview Windows backend.
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # RL stack is not needed in the desktop app; keeps the bundle small.
        "stable_baselines3",
        "torch",
        "gymnasium",
        "scipy",
        "pytest",
    ],
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="worldsim",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="worldsim",
)
