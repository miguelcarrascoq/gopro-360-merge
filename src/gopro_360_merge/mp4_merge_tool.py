"""Locate or download the gyroflow mp4-merge binary."""

from __future__ import annotations

import os
import platform
import shutil
import sys
import urllib.request
from pathlib import Path

from gopro_360_merge.bundled_tools import find_bundled_binary

_RELEASE = "v0.1.11"
_RELEASE_BASE = (
    f"https://github.com/gyroflow/mp4-merge/releases/download/{_RELEASE}"
)
_CACHE_DIRNAME = "gopro-360-merge"


def _cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / _CACHE_DIRNAME


def _asset_name() -> str:
    machine = platform.machine().lower()
    is_arm = machine in {"arm64", "aarch64"}
    if sys.platform == "win32":
        return "mp4_merge-windows-arm64.exe" if is_arm else "mp4_merge-windows64.exe"
    if sys.platform == "darwin":
        return "mp4_merge-mac-arm64" if is_arm else "mp4_merge-mac64"
    if sys.platform.startswith("linux"):
        return "mp4_merge-linux-arm64" if is_arm else "mp4_merge-linux64"
    raise RuntimeError(f"Unsupported platform for mp4-merge: {sys.platform!r}")


def _cached_path() -> Path:
    name = "mp4_merge.exe" if sys.platform == "win32" else "mp4_merge"
    return _cache_root() / "bin" / name


def _download(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_RELEASE_BASE}/{_asset_name()}"
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    if sys.platform != "win32":
        dest.chmod(dest.stat().st_mode | 0o755)
    return dest


def resolve_mp4_merge(*, prefer_path: bool = True) -> Path:
    bundled = find_bundled_binary("mp4_merge")
    if bundled is not None:
        return bundled

    if prefer_path:
        found = shutil.which("mp4_merge") or shutil.which("mp4-merge")
        if found:
            return Path(found)

    cached = _cached_path()
    if cached.is_file() and os.access(cached, os.X_OK):
        return cached
    return _download(cached)


def ensure_mp4_merge() -> Path | None:
    try:
        return resolve_mp4_merge()
    except (OSError, RuntimeError):
        return None
