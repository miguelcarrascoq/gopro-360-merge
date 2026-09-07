"""Locate or extract the bundled GoPro Labs udtacopy binary."""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from importlib import resources
from pathlib import Path

_CACHE_DIRNAME = "gopro-360-merge"


def _cache_root() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / _CACHE_DIRNAME


def _platform_member() -> tuple[str, str]:
    """Return (zip member path, output binary name) for this OS."""
    if sys.platform == "win32":
        return "win/udtacopy.exe", "udtacopy.exe"
    if sys.platform == "darwin":
        return "mac/udtacopy", "udtacopy"
    if sys.platform.startswith("linux"):
        return "linux/udtacopy", "udtacopy"
    raise RuntimeError(
        f"Unsupported platform for bundled udtacopy: {sys.platform!r}"
    )


def _extract_bundled(dest: Path) -> Path:
    member, out_name = _platform_member()
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / out_name

    zip_ref = resources.files("gopro_360_merge").joinpath("vendor/udtacopy.zip")
    with resources.as_file(zip_ref) as zip_path:
        if not Path(zip_path).is_file():
            raise RuntimeError(f"Bundled udtacopy.zip missing at {zip_path}")
        with zipfile.ZipFile(zip_path) as zf:
            try:
                info = zf.getinfo(member)
            except KeyError as exc:
                raise RuntimeError(
                    f"udtacopy binary {member!r} not found in bundled zip"
                ) from exc
            with zf.open(info) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    if sys.platform != "win32":
        out_path.chmod(out_path.stat().st_mode | 0o755)

    return out_path


def resolve_udtacopy(*, prefer_path: bool = True) -> Path:
    """
    Return a path to an executable udtacopy.

    Prefer a PATH install when present; otherwise extract the bundled binary
    into a per-user cache directory.
    """
    if prefer_path:
        found = shutil.which("udtacopy")
        if found:
            return Path(found)

    _member, out_name = _platform_member()
    cached = _cache_root() / "bin" / out_name
    if cached.is_file() and os.access(cached, os.X_OK):
        return cached

    return _extract_bundled(cached.parent)


def ensure_udtacopy() -> Path | None:
    """Try to resolve udtacopy; return None on failure."""
    try:
        return resolve_udtacopy()
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return None
