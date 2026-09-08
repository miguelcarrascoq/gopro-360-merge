"""Merge a GoPro .360 block with ffmpeg + udtacopy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from gopro_360_merge.detect import Block
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


def format_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


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


def _udta_size(path: Path) -> int:
    """Return size of the ``udta`` atom inside ``moov``, or 0 if missing."""
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
                    return 0
                child_size = int.from_bytes(hdr[:4], "big")
                child_tag = hdr[4:8]
                if child_size < 8:
                    return 0
                if child_tag == b"udta":
                    return child_size
                pos += child_size
        return 0
    return 0


def run_udtacopy(source_360: Path, dest_mp4: Path) -> None:
    """
    Copy GoPro udta metadata from *source_360* onto *dest_mp4*.

    Upstream GoPro Labs ``udtacopy`` always exits with status 1, even on
    success, so we accept 0/1 and verify that ``moov`` contains a real
    ``udta`` (not ffmpeg's empty stub of ~32 bytes). Always prefer a camera
    original as *source_360* — copying from a remux sometimes no-ops.
    """
    udtacopy = resolve_udtacopy()
    src_udta = _udta_size(source_360)
    cmd = [str(udtacopy), str(source_360), str(dest_mp4)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # GoPro's binary returns 1 unconditionally after a normal run (success or
    # silent no-op). Treat 0 and 1 as "ran"; confirm with a structure check.
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"udtacopy failed (exit {result.returncode}): {detail}")
    dest_udta = _udta_size(dest_mp4)
    if dest_udta < 64:
        raise RuntimeError(
            "udtacopy finished but destination has no moov/udta metadata; "
            "source may be missing GoPro udta or the copy silently failed"
        )
    # ffmpeg often leaves a ~32-byte empty udta; a real GoPro block is ~20KB+.
    if src_udta >= 1024 and dest_udta < max(1024, src_udta // 2):
        raise RuntimeError(
            f"udtacopy left a stub udta ({dest_udta} bytes) from source "
            f"{source_360.name} ({src_udta} bytes); 360 metadata was not copied"
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
) -> Path:
    """
    Merge all chapters in *block* into ``final_<id>.360`` under *output_dir*.

    Stages reported via on_stage(stage, current, total):
      - probe / join / udtacopy / rename
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

    def join_progress(current: float, total: float) -> None:
        if on_stage:
            on_stage("join", current, total)

    sources = [ch.path for ch in block.chapters]
    udta_source = block.first.path

    merger = ensure_mp4_merge()
    if merger is not None:
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
        if on_stage:
            on_stage("udtacopy", 1.0, 1.0)
            on_stage("rename", 1.0, 1.0)
        if not keep_filelist and filelist_path.exists():
            filelist_path.unlink()
        return output_360

    # Fallback without mp4-merge: ffmpeg concat (may not pan in Player).
    map_args = concat_map_args(sources[0])
    if on_stage:
        on_stage("join", 0.0, max(total_seconds, 0.001))
    if len(sources) == 1:
        shutil.copy2(sources[0], output_mp4)
    else:
        run_ffmpeg_concat(
            filelist_path, output_mp4, total_seconds, map_args, join_progress
        )

    if on_stage:
        on_stage("udtacopy", 0.0, 1.0)
    run_udtacopy(udta_source, output_mp4)
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
