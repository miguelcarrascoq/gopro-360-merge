"""Merge a GoPro .360 block with ffmpeg + udtacopy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from gopro_360_merge.detect import Block
from gopro_360_merge.progress import FfmpegProgressTracker
from gopro_360_merge.udtacopy_tool import ensure_udtacopy, resolve_udtacopy

ProgressCallback = Callable[[str, float, float], None]


def which_or_none(name: str) -> str | None:
    return shutil.which(name)


def require_tools() -> list[str]:
    """Return names of missing required tools."""
    missing: list[str] = []
    for tool in ("ffmpeg", "ffprobe"):
        if which_or_none(tool) is None:
            missing.append(tool)
    if ensure_udtacopy() is None:
        missing.append("udtacopy")
    return missing


def probe_duration_seconds(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return 0.0
    try:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def estimate_block_duration(block: Block) -> float:
    total = 0.0
    for chapter in block.chapters:
        total += probe_duration_seconds(chapter.path)
    return total


def write_filelist(block: Block, filelist_path: Path) -> Path:
    lines = []
    for chapter in block.chapters:
        # ffmpeg concat demuxer: escape single quotes in paths
        escaped = str(chapter.path.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    filelist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return filelist_path


def run_ffmpeg_concat(
    filelist_path: Path,
    output_mp4: Path,
    total_seconds: float,
    on_progress: Callable[[float, float], None] | None = None,
) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(filelist_path),
        "-c",
        "copy",
        "-map",
        "0:0",
        "-map",
        "0:1",
        "-map",
        "0:3",
        "-map",
        "0:5",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_mp4),
    ]

    tracker = FfmpegProgressTracker(total_seconds, on_progress or (lambda *_: None))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        tracker.feed(line)
    stderr = proc.stderr.read() if proc.stderr else ""
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg failed (exit {code}): {stderr.strip()}")


def run_udtacopy(source_360: Path, dest_mp4: Path) -> None:
    udtacopy = resolve_udtacopy()
    cmd = [str(udtacopy), str(source_360), str(dest_mp4)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"udtacopy failed (exit {result.returncode}): {detail}")


def merge_block(
    block: Block,
    output_dir: Path,
    *,
    keep_filelist: bool = True,
    on_stage: ProgressCallback | None = None,
) -> Path:
    """
    Merge all chapters in *block* into ``final_<id>.360`` under *output_dir*.

    Stages reported via on_stage(stage, current, total):
      - probe / ffmpeg / udtacopy / rename
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    filelist_path = output_dir / f"filelist_{block.block_id}.txt"
    output_mp4 = output_dir / f"final_{block.block_id}.mp4"
    output_360 = output_dir / f"final_{block.block_id}.360"

    if on_stage:
        on_stage("probe", 0.0, 1.0)
    total_seconds = estimate_block_duration(block)
    if on_stage:
        on_stage("probe", 1.0, 1.0)

    write_filelist(block, filelist_path)

    def ffmpeg_progress(current: float, total: float) -> None:
        if on_stage:
            on_stage("ffmpeg", current, total)

    if on_stage:
        on_stage("ffmpeg", 0.0, max(total_seconds, 0.001))
    run_ffmpeg_concat(filelist_path, output_mp4, total_seconds, ffmpeg_progress)

    if on_stage:
        on_stage("udtacopy", 0.0, 1.0)
    run_udtacopy(block.first.path, output_mp4)
    if on_stage:
        on_stage("udtacopy", 1.0, 1.0)

    if on_stage:
        on_stage("rename", 0.0, 1.0)
    if output_360.exists():
        output_360.unlink()
    os.replace(output_mp4, output_360)
    if on_stage:
        on_stage("rename", 1.0, 1.0)

    if not keep_filelist and filelist_path.exists():
        filelist_path.unlink()

    return output_360
