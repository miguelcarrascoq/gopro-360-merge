# gopro-360-merge

Interactive CLI to merge **chaptered GoPro `.360` files** by recording block, following the [GoPro Labs chapter workflow](https://gopro.github.io/labs/control/chapters/).

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

Or use the run scripts below: they create `.venv`, install the package, and install `ffmpeg` if missing (Homebrew on macOS, winget on Windows).

## Usage

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

Outputs land in `<directory>/merged/` by default (`final_<id>.360`).

## Crop start/end

Do **not** ffmpeg-trim a 20+ GB `final_*.360` — GoPro Player will not open that remux. Keep the original `GS*.360` chapters.

After watching the merged file in Player, run:

```bat
crop.bat
crop.bat D:\path\to\gopro\folder
crop.bat D:\path\to\gopro\folder --start 00:02:00 --end 00:50:00 -y
```

```bash
./crop.sh
./crop.sh /path/to/gopro/folder --start 00:02:00 --end 00:50:00 -y
gopro-360-merge crop /path/to/gopro/folder --start 2:00 --end 50:00
```

The crop tool maps the range onto the original chapters. Cuts on **chapter
boundaries** only drop whole `GS*.360` files and join with mp4-merge. A
**mid-chapter** cut remuxes every selected chapter with the same recipe,
normalizes timestamps, then joins with mp4-merge and copies GoPro `udta` from
the camera original (needed for 360 pan in Player). Stream-copy trim is
keyframe-aligned (about ±1 s). Output is `merged/final_<id>_crop.360`.

`--start` / `--end` also work on the merge command if you already know the times (same range for every selected block).

## License

MIT
