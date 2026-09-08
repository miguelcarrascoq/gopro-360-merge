#!/usr/bin/env python3
"""Build app icons from the master SVG.

Source of truth:
  src/gopro_360_merge/assets/app_icon.svg

Outputs (committed for end users):
  src/gopro_360_merge/assets/app_icon.png   (512)
  src/gopro_360_merge/assets/app_icon.ico   (multi-size)
  src/gopro_360_merge/assets/AppIcon.icns   (macOS only; iconutil)

Usage:
  pip install -e ".[icons]"   # or install rsvg-convert (librsvg)
  python scripts/build_icons.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "gopro_360_merge" / "assets"
SVG = ASSETS / "app_icon.svg"
PNG_OUT = ASSETS / "app_icon.png"
ICO_OUT = ASSETS / "app_icon.ico"
ICNS_OUT = ASSETS / "AppIcon.icns"
MAC_APP_ICNS = (
    ROOT / "Gopro360Merge.app" / "Contents" / "Resources" / "AppIcon.icns"
)

PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 32, 48, 64, 128, 256)
ICONSET_MAP = {
    16: ("icon_16x16.png",),
    32: ("icon_16x16@2x.png", "icon_32x32.png"),
    64: ("icon_32x32@2x.png",),
    128: ("icon_128x128.png",),
    256: ("icon_128x128@2x.png", "icon_256x256.png"),
    512: ("icon_256x256@2x.png", "icon_512x512.png"),
    1024: ("icon_512x512@2x.png",),
}


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def info(msg: str) -> None:
    print(f"→ {msg}")


def render_svg_png(svg_path: Path, size: int) -> bytes:
    """Return PNG bytes for *size*×*size* from the SVG."""
    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        result = subprocess.run(
            [
                rsvg,
                "-w",
                str(size),
                "-h",
                str(size),
                str(svg_path),
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            die(
                "rsvg-convert failed:\n"
                + (result.stderr.decode("utf-8", errors="replace") or result.stdout.decode())
            )
        return result.stdout

    try:
        import cairosvg  # type: ignore[import-untyped]
    except ImportError:
        die(
            "Need rsvg-convert on PATH, or install build deps: "
            'pip install -e ".[icons]" (also requires the system Cairo library, '
            "e.g. brew install cairo / librsvg)"
        )
    except OSError as exc:
        die(
            f"cairosvg could not load Cairo ({exc}). "
            "Install librsvg (rsvg-convert) or Cairo, e.g. brew install librsvg"
        )

    try:
        return cairosvg.svg2png(
            url=str(svg_path),
            output_width=size,
            output_height=size,
        )
    except OSError as exc:
        die(
            f"cairosvg failed ({exc}). "
            "Install librsvg (rsvg-convert) or Cairo, e.g. brew install librsvg"
        )


def write_pngs(svg_path: Path) -> dict[int, Path]:
    from PIL import Image

    ASSETS.mkdir(parents=True, exist_ok=True)
    paths: dict[int, Path] = {}
    for size in PNG_SIZES:
        data = render_svg_png(svg_path, size)
        img = Image.open(BytesIO(data)).convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.Resampling.LANCZOS)
        out = ASSETS / f"_icon_{size}.png"
        img.save(out, format="PNG")
        paths[size] = out
        info(f"rendered {size}×{size}")
    # Canonical window / Linux icon
    shutil.copyfile(paths[512], PNG_OUT)
    info(f"wrote {PNG_OUT.relative_to(ROOT)}")
    return paths


def write_ico(paths: dict[int, Path]) -> None:
    from PIL import Image

    # Prefer largest available source; Pillow embeds multiple sizes.
    src_size = max(s for s in ICO_SIZES if s in paths)
    img = Image.open(paths[src_size]).convert("RGBA")
    img.save(
        ICO_OUT,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
    )
    info(f"wrote {ICO_OUT.relative_to(ROOT)}")


def write_icns(paths: dict[int, Path]) -> None:
    if sys.platform != "darwin":
        info("skip ICNS (iconutil only on macOS); existing AppIcon.icns kept if present")
        return
    iconutil = shutil.which("iconutil")
    if not iconutil:
        info("skip ICNS (iconutil not found)")
        return

    with tempfile.TemporaryDirectory(prefix="gopro360-iconset-") as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for size, names in ICONSET_MAP.items():
            src = paths.get(size)
            if src is None:
                continue
            for name in names:
                shutil.copyfile(src, iconset / name)
        result = subprocess.run(
            [iconutil, "-c", "icns", str(iconset), "-o", str(ICNS_OUT)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            die(f"iconutil failed: {result.stderr or result.stdout}")
    info(f"wrote {ICNS_OUT.relative_to(ROOT)}")

    if MAC_APP_ICNS.parent.is_dir():
        shutil.copyfile(ICNS_OUT, MAC_APP_ICNS)
        info(f"copied → {MAC_APP_ICNS.relative_to(ROOT)}")


def cleanup_temp_pngs(paths: dict[int, Path]) -> None:
    for path in paths.values():
        if path.name.startswith("_icon_") and path.exists():
            path.unlink()


def main() -> int:
    if not SVG.is_file():
        die(f"missing SVG: {SVG}")
    info(f"source {SVG.relative_to(ROOT)}")
    paths = write_pngs(SVG)
    try:
        write_ico(paths)
        write_icns(paths)
    finally:
        cleanup_temp_pngs(paths)
    info("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
