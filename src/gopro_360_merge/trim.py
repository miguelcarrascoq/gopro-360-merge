"""Plan and apply start/end trims on chaptered GoPro .360 blocks.

Intra-chapter cuts use ffmpeg stream-copy of a single ~4 GB chapter (the
recipe GoPro Player accepted for short remuxes). Whole chapters outside
the range are dropped. Pieces are then joined with mp4-merge.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gopro_360_merge.detect import Block, ChapterFile
from gopro_360_merge.merge import (
    probe_duration_seconds,
    probe_streams,
    run_udtacopy,
)
from gopro_360_merge.progress import FfmpegProgressTracker

ProgressFn = Callable[[float, float], None]

_BOUNDARY_EPS = 0.05


def parse_timecode(value: str) -> float:
    """Parse ``hh:mm:ss``, ``mm:ss``, or seconds (int/float) into seconds."""
    raw = value.strip()
    if not raw:
        raise ValueError("empty time")
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    parts = raw.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    raise ValueError(f"invalid time: {value!r}")


def format_timecode(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


@dataclass(frozen=True)
class TrimPiece:
    chapter: ChapterFile
    chapter_duration: float
    ss: float
    duration: float

    @property
    def needs_trim(self) -> bool:
        if self.ss > _BOUNDARY_EPS:
            return True
        return (self.ss + self.duration) < (self.chapter_duration - _BOUNDARY_EPS)


def chapter_durations(block: Block) -> list[float]:
    return [probe_duration_seconds(ch.path) for ch in block.chapters]


def block_duration(block: Block, durations: list[float] | None = None) -> float:
    durs = durations if durations is not None else chapter_durations(block)
    return float(sum(durs))


def plan_trim_pieces(
    block: Block,
    start_s: float,
    end_s: float,
    durations: list[float] | None = None,
) -> list[TrimPiece]:
    durs = durations if durations is not None else chapter_durations(block)
    if len(durs) != len(block.chapters):
        raise ValueError("duration list does not match chapters")
    total = float(sum(durs))
    if total <= 0:
        raise RuntimeError(f"block {block.block_id}: could not probe duration")
    if start_s < -_BOUNDARY_EPS:
        raise ValueError("start is negative")
    if end_s <= start_s + _BOUNDARY_EPS:
        raise ValueError("end must be after start")
    if start_s >= total:
        raise ValueError(
            f"start {format_timecode(start_s)} is past total "
            f"{format_timecode(total)}"
        )
    end_s = min(end_s, total)

    pieces: list[TrimPiece] = []
    offset = 0.0
    for chapter, dur in zip(block.chapters, durs, strict=True):
        overlap_start = max(start_s, offset)
        overlap_end = min(end_s, offset + dur)
        keep = overlap_end - overlap_start
        if keep > _BOUNDARY_EPS:
            pieces.append(
                TrimPiece(
                    chapter=chapter,
                    chapter_duration=dur,
                    ss=overlap_start - offset,
                    duration=keep,
                )
            )
        offset += dur
    if not pieces:
        raise RuntimeError("trim range does not overlap any chapter")
    return pieces


def trim_map_args(first_chapter: Path) -> list[str]:
    """Maps for a Player-safe chapter remux (matches known-good short tests).

    Keeps both HEVC lenses, AAC, and GPMF. Skips tmcd/fdsc (ffmpeg cannot mux
    them cleanly) and ambisonic (ffmpeg rewrites ``in32`` → ``lpcm``, which
    breaks uniform tags across remuxed pieces).
    """
    streams = probe_streams(first_chapter)
    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video"
        and (s.get("codec_tag_string") or "").strip() in {"hvc1", "hev1", "avc1"}
    ]
    aac = [
        s for s in streams if (s.get("codec_tag_string") or "").strip() == "mp4a"
    ]
    gpmd = [
        s for s in streams if (s.get("codec_tag_string") or "").strip() == "gpmd"
    ]
    if len(videos) < 2:
        raise RuntimeError(
            f"{first_chapter.name}: expected 2 video tracks for .360, "
            f"found {len(videos)}"
        )
    if not gpmd:
        raise RuntimeError(
            f"{first_chapter.name}: missing GoPro gpmd metadata track"
        )
    selected = videos[:2] + aac[:1] + gpmd[:1]
    args: list[str] = []
    for stream in selected:
        args.extend(["-map", f"0:{stream['index']}"])
    return args


def trim_chapter_file(
    source: Path,
    dest_mp4: Path,
    ss: float,
    duration: float,
    on_progress: ProgressFn | None = None,
) -> None:
    """Stream-copy a time window out of one original .360 chapter."""
    map_args = trim_map_args(source)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-copy_unknown",
        "-strict",
        "unofficial",
    ]
    if ss > _BOUNDARY_EPS:
        cmd.extend(["-ss", f"{ss:.3f}"])
    cmd.extend(["-i", str(source)])
    if duration > 0:
        cmd.extend(["-t", f"{duration:.3f}"])
    cmd.extend(
        [
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
            "-avoid_negative_ts",
            "make_zero",
            "-f",
            "mov",
            "-brand",
            "mp41",
            "-write_tmcd",
            "0",
            "-progress",
            "pipe:1",
            "-nostats",
            str(dest_mp4),
        ]
    )
    tracker = FfmpegProgressTracker(max(duration, 0.001), on_progress or (lambda *_: None))
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
        raise RuntimeError(f"ffmpeg trim failed (exit {code}): {stderr.strip()}")
    if not dest_mp4.is_file() or dest_mp4.stat().st_size < 64:
        raise RuntimeError(f"ffmpeg trim produced no output: {dest_mp4}")
    run_udtacopy(source, dest_mp4)


def materialize_pieces(
    pieces: list[TrimPiece],
    temp_dir: Path,
    on_progress: ProgressFn | None = None,
    *,
    remux_all: bool = False,
) -> list[Path]:
    """Prepare chapter files for joining.

    Untrimmed pieces are passed through as the original ``GS*.360`` paths
    unless *remux_all* is set. Mid-chapter cuts must remux **every** selected
    piece with the same ffmpeg recipe: mp4-merge corrupts the output if a
    remuxed chapter (4–5 tracks) is mixed with camera originals (7 tracks).
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    jobs = pieces if remux_all else [p for p in pieces if p.needs_trim]
    done = 0.0
    total = float(max(len(jobs), 1))

    for piece in pieces:
        if not piece.needs_trim and not remux_all:
            paths.append(piece.chapter.path)
            continue
        dest = temp_dir / f"trim_{piece.chapter.path.stem}.mp4"
        dest_360 = temp_dir / f"trim_{piece.chapter.path.stem}.360"

        def piece_progress(current: float, total_s: float, *, _done: float = done) -> None:
            if on_progress:
                frac = 0.0 if total_s <= 0 else min(current / total_s, 1.0)
                on_progress(_done + frac, total)

        trim_chapter_file(
            piece.chapter.path,
            dest,
            piece.ss,
            piece.duration,
            piece_progress,
        )
        if dest_360.exists():
            dest_360.unlink()
        dest.replace(dest_360)
        paths.append(dest_360)
        done += 1.0
        if on_progress:
            on_progress(done, total)
    return paths
