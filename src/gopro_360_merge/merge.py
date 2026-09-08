"""Merge a GoPro .360 block with ffmpeg + udtacopy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from gopro_360_merge.detect import Block, ChapterFile
from gopro_360_merge.mp4_merge_tool import ensure_mp4_merge
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


def probe_streams(path: Path) -> list[dict]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"ffprobe failed for {path}: {detail}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    return list(data.get("streams") or [])


def _codec_tag(stream: dict) -> str:
    return (stream.get("codec_tag_string") or "").strip()


def concat_map_args(first_chapter: Path) -> list[str]:
    """Pick .360 tracks by codec tag so MAX and MAX 2 layouts both work.

    GoPro Labs' hardcoded ``-map 0:0 -map 0:1 -map 0:3 -map 0:5`` assumes
    original MAX order (two videos first). MAX 2 stores the second fisheye
    later and AAC at index 1. Mapping by tag keeps both lenses, AAC, GPMF,
    and ambisonic when present. tmcd/fdsc are skipped: ffmpeg cannot mux them
    cleanly and GoPro Player does not need them to open the file.
    """
    streams = probe_streams(first_chapter)
    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video"
        and _codec_tag(s) in {"hvc1", "hev1", "avc1"}
    ]
    aac = [s for s in streams if _codec_tag(s) == "mp4a"]
    gpmd = [s for s in streams if _codec_tag(s) == "gpmd"]
    amb = [s for s in streams if _codec_tag(s) == "in32"]

    if len(videos) < 2:
        raise RuntimeError(
            f"{first_chapter.name}: expected 2 video tracks for .360, "
            f"found {len(videos)}"
        )
    if not gpmd:
        raise RuntimeError(
            f"{first_chapter.name}: missing GoPro gpmd metadata track"
        )

    selected = videos[:2] + aac[:1] + gpmd[:1] + amb[:1]
    args: list[str] = []
    for stream in selected:
        args.extend(["-map", f"0:{stream['index']}"])
    return args


def run_ffmpeg_concat(
    filelist_path: Path,
    output_mp4: Path,
    total_seconds: float,
    map_args: list[str],
    on_progress: Callable[[float, float], None] | None = None,
) -> None:
    # copy_unknown + unofficial: pass through gpmd (and ambisonic).
    # mov/mp41 + write_tmcd 0: match a camera .360 well enough for GoPro Player.
    # Both video tracks must be default/enabled; otherwise Player/MF ignores the
    # second lens and the merged file will not open as 360.
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-copy_unknown",
        "-strict",
        "unofficial",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(filelist_path),
        *map_args,
        "-c",
        "copy",
        "-copy_unknown",
        "-strict",
        "unofficial",
        "-disposition:v:0",
        "default",
        "-disposition:v:1",
        "default",
        "-f",
        "mov",
        "-brand",
        "mp41",
        "-write_tmcd",
        "0",
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


def _top_level_atoms(path: Path) -> list[tuple[bytes, int, int]]:
    """Return [(fourcc, offset, size), ...] for top-level MP4 atoms."""
    atoms: list[tuple[bytes, int, int]] = []
    file_size = path.stat().st_size
    with path.open("rb") as fp:
        pos = 0
        while pos + 8 <= file_size:
            fp.seek(pos)
            hdr = fp.read(8)
            if len(hdr) < 8:
                break
            size32 = int.from_bytes(hdr[:4], "big")
            tag = hdr[4:8]
            if size32 == 1:
                ext = fp.read(8)
                if len(ext) < 8:
                    break
                size = int.from_bytes(ext, "big")
            elif size32 == 0:
                size = file_size - pos
            else:
                size = size32
            if size < 8:
                break
            atoms.append((tag, pos, size))
            pos += size
    return atoms


def _moov_has_udta(path: Path) -> bool:
    """True if the file's moov atom contains a udta child (GoPro metadata)."""
    for tag, offset, size in _top_level_atoms(path):
        if tag != b"moov":
            continue
        end = offset + size
        pos = offset + 8
        with path.open("rb") as fp:
            while pos + 8 <= end:
                fp.seek(pos)
                hdr = fp.read(8)
                if len(hdr) < 8:
                    return False
                child_size = int.from_bytes(hdr[:4], "big")
                child_tag = hdr[4:8]
                if child_size == 1:
                    ext = fp.read(8)
                    if len(ext) < 8:
                        return False
                    child_size = int.from_bytes(ext, "big")
                elif child_size == 0:
                    child_size = end - pos
                if child_size < 8:
                    return False
                if child_tag == b"udta":
                    return True
                pos += child_size
        return False
    return False


def run_udtacopy(source_360: Path, dest_mp4: Path) -> None:
    """
    Copy GoPro udta metadata from *source_360* onto *dest_mp4*.

    Upstream GoPro Labs ``udtacopy`` always exits with status 1, even on
    success, so we accept 0/1 and verify that ``moov`` contains ``udta``.
    """
    udtacopy = resolve_udtacopy()
    cmd = [str(udtacopy), str(source_360), str(dest_mp4)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # GoPro's binary returns 1 unconditionally after a normal run (success or
    # silent no-op). Treat 0 and 1 as "ran"; confirm with a structure check.
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"udtacopy failed (exit {result.returncode}): {detail}")
    if not _moov_has_udta(dest_mp4):
        raise RuntimeError(
            "udtacopy finished but destination has no moov/udta metadata; "
            "source may be missing GoPro udta or the copy silently failed"
        )


def run_mp4_merge(
    sources: list[Path],
    output_360: Path,
    on_progress: Callable[[float, float], None] | None = None,
    expected_bytes: int | None = None,
) -> None:
    """Join chapters with gyroflow mp4-merge, preserving camera MP4 structure."""
    merger = ensure_mp4_merge()
    if merger is None:
        raise RuntimeError("mp4-merge is not available")
    if len(sources) < 1:
        raise RuntimeError("mp4-merge needs at least one source file")
    if expected_bytes is None:
        expected_bytes = sum(p.stat().st_size for p in sources)
    expected = float(max(expected_bytes, 1))
    if output_360.exists():
        output_360.unlink()
    cmd = [
        str(merger),
        *[str(p.resolve()) for p in sources],
        "--out",
        str(output_360),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    while proc.poll() is None:
        if on_progress and output_360.exists():
            on_progress(float(min(output_360.stat().st_size, int(expected))), expected)
        time.sleep(0.4)
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0:
        raise RuntimeError(f"mp4-merge failed (exit {proc.returncode}): {(stderr or '').strip()}")
    if not output_360.is_file() or output_360.stat().st_size < 64:
        raise RuntimeError("mp4-merge finished but output is missing or empty")
    if on_progress:
        on_progress(expected, expected)


def merge_block(
    block: Block,
    output_dir: Path,
    *,
    keep_filelist: bool = True,
    on_stage: ProgressCallback | None = None,
    start_s: float | None = None,
    end_s: float | None = None,
    output_stem: str | None = None,
) -> Path:
    """
    Merge all chapters in *block* into ``final_<id>.360`` under *output_dir*.

    Optional *start_s* / *end_s* trim the timeline (see ``trim.py``). A trim
    writes ``final_<id>_crop.360`` unless *output_stem* is set.

    Stages reported via on_stage(stage, current, total):
      - probe / trim / join / udtacopy / rename
    """
    from gopro_360_merge.trim import materialize_pieces, plan_trim_pieces

    output_dir.mkdir(parents=True, exist_ok=True)
    filelist_path = output_dir / f"filelist_{block.block_id}.txt"
    output_mp4 = output_dir / f"final_{block.block_id}.mp4"
    trimmed = start_s is not None or end_s is not None
    stem = output_stem or (
        f"final_{block.block_id}_crop" if trimmed else f"final_{block.block_id}"
    )
    output_360 = output_dir / f"{stem}.360"
    temp_dir = output_dir / f".trim_{block.block_id}"

    if on_stage:
        on_stage("probe", 0.0, 1.0)
    total_seconds = estimate_block_duration(block)
    if on_stage:
        on_stage("probe", 1.0, 1.0)

    write_filelist(block, filelist_path)

    def join_progress(current: float, total: float) -> None:
        if on_stage:
            on_stage("join", current, total)

    sources = [ch.path for ch in block.chapters]
    # Soft (mid-chapter) cuts remux every selected piece to one track layout.
    # mp4-merge of remuxed+original mixes track counts and writes garbage;
    # mp4-merge of remuxed+remuxed also breaks timestamps (start≈duration).
    remuxed_sources = False
    join_seconds = total_seconds
    if trimmed:
        range_start = 0.0 if start_s is None else start_s
        range_end = total_seconds if end_s is None else end_s
        pieces = plan_trim_pieces(block, range_start, range_end)
        join_seconds = float(sum(p.duration for p in pieces))
        soft_trim = any(p.needs_trim for p in pieces)
        if on_stage:
            on_stage("trim", 0.0, 1.0)
        try:
            if soft_trim:
                sources = materialize_pieces(
                    pieces,
                    temp_dir,
                    (lambda c, t: on_stage("trim", c, t) if on_stage else None),
                    remux_all=True,
                )
                remuxed_sources = True
            else:
                # Whole chapters only: keep camera files and mp4-merge them.
                sources = [p.chapter.path for p in pieces]
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        if on_stage:
            on_stage("trim", 1.0, 1.0)

    def cleanup_temp() -> None:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    merger = ensure_mp4_merge()
    if merger is not None and not remuxed_sources:
        try:
            if len(sources) == 1:
                if on_stage:
                    on_stage("join", 0.0, 1.0)
                if output_360.exists():
                    output_360.unlink()
                shutil.copy2(sources[0], output_360)
                if on_stage:
                    on_stage("join", 1.0, 1.0)
            else:
                if on_stage:
                    on_stage(
                        "join",
                        0.0,
                        float(max(sum(p.stat().st_size for p in sources), 1)),
                    )
                run_mp4_merge(sources, output_360, join_progress)
        finally:
            cleanup_temp()
        if on_stage:
            on_stage("udtacopy", 1.0, 1.0)
            on_stage("rename", 1.0, 1.0)
        if not keep_filelist and filelist_path.exists():
            filelist_path.unlink()
        return output_360

    map_args = concat_map_args(sources[0])
    if on_stage:
        on_stage("join", 0.0, max(join_seconds, 0.001))
    try:
        if len(sources) == 1:
            if remuxed_sources:
                # Already a Player-safe remux; copy as final .360
                if output_360.exists():
                    output_360.unlink()
                shutil.copy2(sources[0], output_360)
                cleanup_temp()
                if on_stage:
                    on_stage("udtacopy", 1.0, 1.0)
                    on_stage("rename", 1.0, 1.0)
                if not keep_filelist and filelist_path.exists():
                    filelist_path.unlink()
                return output_360
            shutil.copy2(sources[0], output_mp4)
        else:
            trim_list = output_dir / f"filelist_{block.block_id}_crop.txt"
            write_filelist(
                Block(
                    block_id=block.block_id,
                    chapters=tuple(
                        ChapterFile(path=p, chapter=i + 1, block_id=block.block_id)
                        for i, p in enumerate(sources)
                    ),
                ),
                trim_list,
            )
            run_ffmpeg_concat(
                trim_list, output_mp4, join_seconds, map_args, join_progress
            )
            if not keep_filelist and trim_list.exists():
                trim_list.unlink()
    finally:
        if not (remuxed_sources and len(sources) == 1):
            cleanup_temp()

    if on_stage:
        on_stage("udtacopy", 0.0, 1.0)
    run_udtacopy(sources[0], output_mp4)
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
