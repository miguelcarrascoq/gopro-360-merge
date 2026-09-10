"""Resolve optional binaries shipped in a release ``tools/`` folder."""

from __future__ import annotations

import sys
from pathlib import Path


def release_root() -> Path | None:
    """Directory that contains the app executable (PyInstaller onedir)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def tools_dir() -> Path | None:
    root = release_root()
    if root is None:
        return None
    path = root / "tools"
    return path if path.is_dir() else None


def find_bundled_binary(name: str) -> Path | None:
    """
    Look for ``name`` inside ``tools/`` next to a frozen executable.

    On Windows, ``ffmpeg`` resolves to ``tools/ffmpeg.exe``.
    """
    tools = tools_dir()
    if tools is None:
        return None
    candidates = [name]
    if sys.platform == "win32":
        if not name.lower().endswith(".exe"):
            candidates.insert(0, f"{name}.exe")
    for candidate in candidates:
        path = tools / candidate
        if path.is_file():
            return path
    return None
