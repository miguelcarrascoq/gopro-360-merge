# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: GUI + CLI onedir sharing one COLLECT folder."""

from __future__ import annotations

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
PKG = SRC / "gopro_360_merge"
ICON = PKG / "assets" / "app_icon.ico"

datas: list = collect_data_files("gopro_360_merge")
binaries: list = []
hiddenimports: list = [
    "gopro_360_merge",
    "gopro_360_merge.cli",
    "gopro_360_merge.gui",
    "gopro_360_merge.merge",
    "gopro_360_merge.detect",
    "gopro_360_merge.progress",
    "gopro_360_merge.mp4_merge_tool",
    "gopro_360_merge.udtacopy_tool",
    "gopro_360_merge.bundled_tools",
]

for pkg in ("customtkinter", "rich", "questionary"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

block_cipher = None

gui_a = Analysis(
    [str(ROOT / "packaging" / "entry_gui.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

cli_a = Analysis(
    [str(ROOT / "packaging" / "entry_cli.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

MERGE(
    (gui_a, "gopro-360-gui", "gopro-360-gui"),
    (cli_a, "gopro-360-merge", "gopro-360-merge"),
)

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="gopro-360-gui",
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
    icon=str(ICON) if ICON.is_file() else None,
)

cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="gopro-360-merge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    gui_exe,
    gui_a.binaries,
    gui_a.zipfiles,
    gui_a.datas,
    cli_exe,
    cli_a.binaries,
    cli_a.zipfiles,
    cli_a.datas,
    name="gopro360merge",
)
