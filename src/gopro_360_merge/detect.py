"""Detect and group chaptered GoPro .360 files by recording block."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# GS + chapter (2 digits) + recording id (remaining digits) + .360
CHAPTER_RE = re.compile(r"^GS(\d{2})(\d+)\.360$", re.IGNORECASE)


@dataclass(frozen=True)
class ChapterFile:
    path: Path
    chapter: int
    block_id: str

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


@dataclass(frozen=True)
class Block:
    block_id: str
    chapters: tuple[ChapterFile, ...]

    @property
    def first(self) -> ChapterFile:
        return self.chapters[0]

    @property
    def last(self) -> ChapterFile:
        return self.chapters[-1]

    @property
    def size_bytes(self) -> int:
        return sum(c.size_bytes for c in self.chapters)

    @property
    def label(self) -> str:
        size_gb = self.size_bytes / (1024**3)
        names = ", ".join(c.path.name for c in self.chapters)
        return (
            f"Block {self.block_id}  "
            f"({len(self.chapters)} chapters, {size_gb:.1f} GB)  "
            f"{self.first.path.name} … {self.last.path.name}"
        )


def parse_chapter(path: Path) -> ChapterFile | None:
    match = CHAPTER_RE.match(path.name)
    if not match:
        return None
    return ChapterFile(
        path=path,
        chapter=int(match.group(1)),
        block_id=match.group(2),
    )


def scan_directory(directory: Path) -> list[Block]:
    """Scan *directory* for GS*.360 chapters and group by recording id."""
    directory = directory.resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    by_block: dict[str, list[ChapterFile]] = {}
    for path in directory.iterdir():
        if not path.is_file():
            continue
        # Skip macOS AppleDouble sidecar files
        if path.name.startswith("._"):
            continue
        chapter = parse_chapter(path)
        if chapter is None:
            continue
        by_block.setdefault(chapter.block_id, []).append(chapter)

    blocks: list[Block] = []
    for block_id, chapters in by_block.items():
        chapters.sort(key=lambda c: c.chapter)
        blocks.append(Block(block_id=block_id, chapters=tuple(chapters)))

    blocks.sort(key=lambda b: b.block_id)
    return blocks
