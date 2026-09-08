# gopro-360-merge

Interactive CLI to merge **chaptered GoPro `.360` files** by recording block, following the [GoPro Labs chapter workflow](https://gopro.github.io/labs/control/chapters/).

GoPro splits long recordings into `GS01…`, `GS02…`, … chapters. Files that share the same trailing recording ID belong to one block:

```
GS011623.360  GS021623.360  …  GS051623.360   →  block 1623
GS011624.360  GS021624.360  …  GS051624.360   →  block 1624
```

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) (includes `ffprobe`) — e.g. `brew install ffmpeg` (macOS) or `winget install Gyan.FFmpeg` (Windows)
- [`udtacopy`](https://gopro.github.io/labs/control/chapters/) — bundled with this package and extracted automatically on first use if not already on your `PATH`

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

For each selected block the tool:

1. Writes `filelist_<id>.txt` (ffmpeg concat demuxer)
2. Runs:

   ```bash
   ffmpeg -y -f concat -safe 0 -i filelist_<id>.txt \
     -c copy -map 0:0 -map 0:1 -map 0:3 -map 0:5 \
     final_<id>.mp4
   ```

3. Copies GoPro `udta` metadata:

   ```bash
   udtacopy GS01xxxx.360 final_<id>.mp4
   ```

4. Renames to `final_<id>.360`

Outputs land in `<directory>/merged/` by default. Progress bars show per-block status (probe → ffmpeg → udtacopy → rename).

## License

MIT
