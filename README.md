# gopro-360-merge

Interactive CLI and desktop GUI to merge **chaptered GoPro `.360` files** by recording block, following the [GoPro Labs chapter workflow](https://gopro.github.io/labs/control/chapters/).

GoPro splits long recordings into `GS01…`, `GS02…`, … chapters. Files that share the same trailing recording ID belong to one block:

```
GS011623.360  GS021623.360  …  GS051623.360   →  block 1623
GS011624.360  GS021624.360  …  GS051624.360   →  block 1624
```

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) (includes `ffprobe`) — duration probing and ffmpeg concat fallback; e.g. `brew install ffmpeg` (macOS) or `winget install Gyan.FFmpeg` (Windows)
- [`mp4-merge`](https://github.com/gyroflow/mp4-merge) — downloaded on first use (keeps GoPro Player–compatible `.360` structure)
- [`udtacopy`](https://gopro.github.io/labs/control/chapters/) — bundled fallback if mp4-merge is unavailable

On macOS, Gatekeeper may block the first run of the bundled binary; use **System Settings → Privacy & Security → Open Anyway** if prompted.

## Install

```bash
git clone https://github.com/miguelcarrascoq/gopro-360-merge.git
cd gopro-360-merge
python3 -m pip install -e .
```

Or use the run / GUI scripts below: they create `.venv`, install the package, and install `ffmpeg` if missing (Homebrew on macOS, winget on Windows).

## Desktop GUI

Cross-platform UI (macOS / Windows / Linux) with [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter): pick a folder, select blocks, set optional start/end trim, and run **Merge**.

```bash
# macOS / Linux
./gui.sh
```

```bat
REM Windows
gui.bat
```

```bash
# After pip install -e .
gopro-360-gui
gopro-360-merge gui
```

### App icon launchers

| Platform | How |
|----------|-----|
| macOS | Open `Gopro360Merge.app` (Dock uses `AppIcon.icns`) |
| Windows | `gui.bat` / `gui.ps1` also refreshes `Gopro360Merge.lnk` with the `.ico` |
| Linux | `./gui.sh` installs `~/.local/share/applications/gopro-360-gui.desktop` |

### Changing the icon

Edit the master SVG, then rebuild derivatives:

```bash
# edit source
# src/gopro_360_merge/assets/app_icon.svg

# Prefer system rsvg-convert (e.g. brew install librsvg), or:
pip install -e ".[icons]"   # needs Cairo if no rsvg-convert
python scripts/build_icons.py
```

This regenerates `app_icon.png`, `app_icon.ico`, and on macOS `AppIcon.icns` (copied into `Gopro360Merge.app`). Commit the generated files so end users do not need Cairo/rsvg.
## CLI Usage

```bash
# macOS / Linux: bootstrap + ask/confirm folder, then merge UI
./run.sh
./run.sh /path/to/gopro/folder
./run.sh /path/to/gopro/folder --all -y
```

```bat
REM Windows (CMD / Explorer): same flow via run.bat → run.ps1
run.bat
run.bat D:\path\to\gopro\folder
run.bat D:\path\to\gopro\folder --all -y
```

```bash
# Direct CLI (after pip install -e .)
gopro-360-merge /path/to/gopro/folder
gopro-360-merge /path/to/gopro/folder --all -y
gopro-360-merge /path/to/gopro/folder -o /path/to/output
```

The run scripts ask for the GoPro folder first (or show the path you passed and only confirm), then run the merge flow.

After selecting block(s), the CLI probes chapter durations and shows each **total length** (no need to wait for the merge). Interactively it asks start/end **per block**; `--start` / `--end` apply the same range to every selected block. Leave times empty for a full merge of that block.

For each selected block the tool joins chapters with [mp4-merge](https://github.com/gyroflow/mp4-merge), which concatenates `mdat` and rewrites sample tables while keeping the camera’s tracks and `udta` metadata. That is what GoPro Player needs for a file larger than 4 GB. If mp4-merge is unavailable, it falls back to ffmpeg concat + `udtacopy`.

Outputs land in `<directory>/merged/` by default (`final_<id>.360`). With
`--start` / `--end` (or interactive times), mid-range cuts remux from the
original `GS*.360` chapters (keyframe-aligned, about ±1 s) and write
`final_<id>_crop.360` — do not ffmpeg-trim a finished multi‑GB `.360` if you
need GoPro Player compatibility.

## License

MIT
