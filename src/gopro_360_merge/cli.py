"""Interactive CLI for merging GoPro .360 chapter blocks."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import questionary
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from gopro_360_merge import __version__
from gopro_360_merge.detect import Block, scan_directory
from gopro_360_merge.merge import estimate_block_duration, merge_block, require_tools
from gopro_360_merge.trim import format_timecode, parse_timecode
from gopro_360_merge.udtacopy_tool import resolve_udtacopy

console = Console()


def format_size(num_bytes: int) -> str:
    gb = num_bytes / (1024**3)
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = num_bytes / (1024**2)
    return f"{mb:.1f} MB"


def print_blocks_table(blocks: list[Block]) -> None:
    table = Table(title="Detected .360 blocks", show_lines=False)
    table.add_column("Block", style="cyan", no_wrap=True)
    table.add_column("Chapters", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Files")
    for block in blocks:
        files = " → ".join(c.path.name for c in block.chapters)
        table.add_row(
            block.block_id,
            str(len(block.chapters)),
            format_size(block.size_bytes),
            files,
        )
    console.print(table)


def parse_optional_timecode(value: str | None, *, empty: float | None) -> float | None:
    if value is None:
        return empty
    stripped = value.strip()
    if not stripped:
        return empty
    return parse_timecode(stripped)


def prompt_time_range(total: float) -> tuple[float, float] | None:
    console.print(f"Total duration: [bold]{format_timecode(total)}[/bold]")
    console.print(
        "[dim]Keyframe-aligned copy trim (about ±1s). "
        "Empty start = 0, empty end = total.[/dim]"
    )
    start_raw = questionary.text("Start (hh:mm:ss):", default="").ask()
    if start_raw is None:
        return None
    end_raw = questionary.text(
        f"End (hh:mm:ss, empty = {format_timecode(total)}):",
        default="",
    ).ask()
    if end_raw is None:
        return None
    try:
        start = parse_optional_timecode(start_raw, empty=0.0) or 0.0
        end = parse_optional_timecode(end_raw, empty=total)
        if end is None:
            end = total
    except ValueError as exc:
        console.print(f"[red]Invalid time: {exc}[/red]")
        return None
    if start < 0 or end > total + 0.5 or end <= start:
        console.print("[red]Start/end must satisfy 0 ≤ start < end ≤ total.[/red]")
        return None
    return start, min(end, total)


def select_blocks(blocks: list[Block]) -> list[Block]:
    choices = [
        questionary.Choice(title=block.label, value=block.block_id)
        for block in blocks
    ]
    selected_ids = questionary.checkbox(
        "Select block(s) to merge (space to toggle, enter to confirm):",
        choices=choices,
    ).ask()

    if selected_ids is None:
        return []
    id_set = set(selected_ids)
    return [b for b in blocks if b.block_id in id_set]


def check_dependencies() -> bool:
    missing = require_tools()
    if not missing:
        udtacopy = resolve_udtacopy()
        if shutil.which("udtacopy") is None:
            console.print(f"[dim]Using bundled udtacopy → {udtacopy}[/dim]")
        return True
    console.print("[red]Missing required tools:[/red] " + ", ".join(missing))
    if "udtacopy" in missing:
        console.print(
            "  Could not extract the bundled udtacopy binary for this platform."
        )
    if "ffmpeg" in missing or "ffprobe" in missing:
        console.print("  Install ffmpeg (includes ffprobe), e.g. `brew install ffmpeg`")
    return False


def run_merges(
    blocks: list[Block],
    output_dir: Path,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
) -> int:
    failures = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.fields[block_id]}"),
        TextColumn("{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        overall = progress.add_task(
            "Overall",
            total=len(blocks),
            block_id="ALL",
        )
        for block in blocks:
            task = progress.add_task(
                "starting…",
                total=100.0,
                block_id=f"#{block.block_id}",
            )

            def on_stage(
                stage: str,
                current: float,
                total: float,
                *,
                _task: TaskID = task,
                _block: Block = block,
            ) -> None:
                has_trim = start_s is not None or end_s is not None
                labels = {
                    "probe": "probing duration",
                    "trim": "trimming chapters",
                    "join": "joining chapters",
                    "ffmpeg": "joining chapters",
                    "udtacopy": "copying udta metadata",
                    "rename": "renaming to .360",
                }
                label = labels.get(stage, stage)
                stage_base = {
                    "probe": 0.0,
                    "trim": 5.0,
                    "join": 40.0 if has_trim else 5.0,
                    "ffmpeg": 40.0 if has_trim else 5.0,
                    "udtacopy": 90.0,
                    "rename": 97.0,
                }
                stage_span = {
                    "probe": 5.0,
                    "trim": 35.0,
                    "join": 50.0 if has_trim else 85.0,
                    "ffmpeg": 50.0 if has_trim else 85.0,
                    "udtacopy": 7.0,
                    "rename": 3.0,
                }
                frac = 0.0 if total <= 0 else min(max(current / total, 0.0), 1.0)
                completed = stage_base[stage] + stage_span[stage] * frac
                progress.update(_task, completed=completed, description=label)

            try:
                out = merge_block(
                    block,
                    output_dir,
                    on_stage=on_stage,
                    start_s=start_s,
                    end_s=end_s,
                )
                progress.update(task, completed=100.0, description=f"done → {out.name}")
                console.print(
                    f"[green]✓[/green] Block {block.block_id} → {out}"
                )
            except Exception as exc:  # noqa: BLE001 — show any merge failure cleanly
                failures += 1
                progress.update(task, description=f"[red]failed[/red]")
                console.print(f"[red]✗[/red] Block {block.block_id}: {exc}")
            finally:
                progress.advance(overall)

    return failures


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory containing GS*.360 chapters (default: current directory)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output directory (default: <directory>/merged)",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Start time (hh:mm:ss, mm:ss, or seconds). Default: 0",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End time (hh:mm:ss, mm:ss, or seconds). Default: full duration",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Use all detected blocks without interactive selection",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gopro-360-merge",
        description=(
            "Detect chaptered GoPro .360 files, select recording blocks, "
            "and merge them. Use `crop` to trim start/end after viewing."
        ),
    )
    _add_common_args(parser)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _load_blocks(source: Path) -> list[Block] | int:
    try:
        blocks = scan_directory(source)
    except NotADirectoryError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    if not blocks:
        console.print(f"[yellow]No GS*.360 chapter files found in {source}[/yellow]")
        return 1
    return blocks


def _resolve_range(
    selected: list[Block],
    *,
    start_arg: str | None,
    end_arg: str | None,
    interactive: bool,
    require_crop: bool,
) -> tuple[float | None, float | None] | None:
    totals = [estimate_block_duration(b) for b in selected]
    max_total = max(totals) if totals else 0.0
    for block, total in zip(selected, totals, strict=True):
        console.print(
            f"  • {block.block_id}: {len(block.chapters)} chapters, "
            f"{format_timecode(total)}"
        )

    start_s: float | None
    end_s: float | None
    try:
        start_s = parse_optional_timecode(start_arg, empty=None)
        end_s = parse_optional_timecode(end_arg, empty=None)
    except ValueError as exc:
        console.print(f"[red]Invalid --start/--end: {exc}[/red]")
        return None

    if start_s is None and end_s is None and interactive:
        prompted = prompt_time_range(max_total if len(selected) == 1 else totals[0])
        if prompted is None:
            return None
        start_s, end_s = prompted
        if not require_crop and start_s <= 0.05 and abs(end_s - totals[0]) < 0.5:
            return (None, None)

    if start_s is None and end_s is None:
        if require_crop:
            console.print("[red]crop needs --start and/or --end, or an interactive range.[/red]")
            return None
        return (None, None)

    if start_s is None:
        start_s = 0.0
    if end_s is None:
        end_s = totals[0] if len(selected) == 1 else max_total
    return (start_s, end_s)


def _run_selected(
    selected: list[Block],
    output_dir: Path,
    *,
    start_s: float | None,
    end_s: float | None,
    yes: bool,
    verb: str,
) -> int:
    console.print()
    console.print(
        f"Will {verb} [bold]{len(selected)}[/bold] block(s) into {output_dir}"
    )
    if start_s is not None or end_s is not None:
        start_label = format_timecode(start_s or 0.0)
        end_label = format_timecode(end_s) if end_s is not None else "end"
        console.print(f"  Range: {start_label} → {end_label}")

    if not yes:
        confirmed = questionary.confirm(f"Proceed with {verb}?", default=True).ask()
        if not confirmed:
            console.print("[yellow]Cancelled.[/yellow]")
            return 0

    console.print()
    failures = run_merges(selected, output_dir, start_s=start_s, end_s=end_s)
    if failures:
        console.print(f"\n[red]Finished with {failures} failure(s).[/red]")
        return 1
    past = "cropped" if verb == "crop" else "merged"
    console.print(f"\n[green]All selected blocks {past} successfully.[/green]")
    return 0


def merge_main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.directory).expanduser().resolve()
    output_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else source / "merged"
    )

    if not check_dependencies():
        return 1

    loaded = _load_blocks(source)
    if isinstance(loaded, int):
        return loaded
    blocks = loaded

    console.print(f"Scanning [bold]{source}[/bold]\n")
    print_blocks_table(blocks)

    if args.all:
        selected = blocks
    else:
        selected = select_blocks(blocks)

    if not selected:
        console.print("[yellow]No blocks selected. Exiting.[/yellow]")
        return 0

    console.print()
    resolved = _resolve_range(
        selected,
        start_arg=args.start,
        end_arg=args.end,
        interactive=not args.yes,
        require_crop=False,
    )
    if resolved is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return 0
    start_s, end_s = resolved
    return _run_selected(
        selected,
        output_dir,
        start_s=start_s,
        end_s=end_s,
        yes=args.yes,
        verb="merge",
    )


def crop_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gopro-360-merge crop",
        description=(
            "Trim start/end of a chaptered .360 block using the original GS* "
            "files (required for GoPro Player). Output is final_<id>_crop.360."
        ),
    )
    _add_common_args(parser)
    args = parser.parse_args(argv)
    source = Path(args.directory).expanduser().resolve()
    output_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else source / "merged"
    )

    if not check_dependencies():
        return 1

    loaded = _load_blocks(source)
    if isinstance(loaded, int):
        return loaded
    blocks = loaded

    console.print(f"Scanning [bold]{source}[/bold]\n")
    print_blocks_table(blocks)

    if args.all:
        selected = blocks
    elif len(blocks) == 1:
        selected = blocks
        console.print(f"Using block [cyan]{selected[0].block_id}[/cyan]")
    else:
        selected = select_blocks(blocks)

    if not selected:
        console.print("[yellow]No blocks selected. Exiting.[/yellow]")
        return 0

    console.print()
    resolved = _resolve_range(
        selected,
        start_arg=args.start,
        end_arg=args.end,
        interactive=not (args.start or args.end),
        require_crop=True,
    )
    if resolved is None:
        console.print("[yellow]Cancelled.[/yellow]")
        return 0
    start_s, end_s = resolved
    if start_s is None and end_s is None:
        return 1
    return _run_selected(
        selected,
        output_dir,
        start_s=start_s,
        end_s=end_s,
        yes=args.yes,
        verb="crop",
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "crop":
        return crop_main(argv[1:])
    return merge_main(argv)


if __name__ == "__main__":
    sys.exit(main())
