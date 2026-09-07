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
from gopro_360_merge.merge import merge_block, require_tools
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


def run_merges(blocks: list[Block], output_dir: Path) -> int:
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
                labels = {
                    "probe": "probing duration",
                    "ffmpeg": "ffmpeg concat",
                    "udtacopy": "copying udta metadata",
                    "rename": "renaming to .360",
                }
                label = labels.get(stage, stage)
                # Map stages onto a 0–100 overall bar for this block
                stage_base = {
                    "probe": 0.0,
                    "ffmpeg": 5.0,
                    "udtacopy": 90.0,
                    "rename": 97.0,
                }
                stage_span = {
                    "probe": 5.0,
                    "ffmpeg": 85.0,
                    "udtacopy": 7.0,
                    "rename": 3.0,
                }
                frac = 0.0 if total <= 0 else min(max(current / total, 0.0), 1.0)
                completed = stage_base[stage] + stage_span[stage] * frac
                progress.update(_task, completed=completed, description=label)

            try:
                out = merge_block(block, output_dir, on_stage=on_stage)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gopro-360-merge",
        description=(
            "Detect chaptered GoPro .360 files, select recording blocks, "
            "and merge them with ffmpeg + udtacopy."
        ),
    )
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
        help="Output directory for merged files (default: <directory>/merged)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Merge all detected blocks without interactive selection",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt before merging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.directory).expanduser().resolve()
    output_dir = (
        Path(args.output).expanduser().resolve()
        if args.output
        else source / "merged"
    )

    if not check_dependencies():
        return 1

    try:
        blocks = scan_directory(source)
    except NotADirectoryError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    if not blocks:
        console.print(f"[yellow]No GS*.360 chapter files found in {source}[/yellow]")
        return 1

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
    console.print(f"Will merge [bold]{len(selected)}[/bold] block(s) into {output_dir}")
    for block in selected:
        console.print(f"  • {block.block_id}: {len(block.chapters)} chapters")

    if not args.yes:
        confirmed = questionary.confirm("Proceed with merge?", default=True).ask()
        if not confirmed:
            console.print("[yellow]Cancelled.[/yellow]")
            return 0

    console.print()
    failures = run_merges(selected, output_dir)
    if failures:
        console.print(f"\n[red]Finished with {failures} failure(s).[/red]")
        return 1
    console.print("\n[green]All selected blocks merged successfully.[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
