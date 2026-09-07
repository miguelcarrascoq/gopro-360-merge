"""Parse ffmpeg -progress output for rich progress bars."""

from __future__ import annotations

import re
from collections.abc import Callable


_KV_RE = re.compile(r"^(\w+)=(.+)$")


def parse_progress_line(line: str) -> tuple[str, str] | None:
    match = _KV_RE.match(line.strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def out_time_ms_to_seconds(value: str) -> float | None:
    try:
        return int(value) / 1_000_000.0
    except ValueError:
        return None


class FfmpegProgressTracker:
    """Accumulate key=value progress lines from ffmpeg."""

    def __init__(self, total_seconds: float, on_update: Callable[[float, float], None]):
        self.total_seconds = max(total_seconds, 0.001)
        self.on_update = on_update
        self.last_seconds = 0.0

    def feed(self, line: str) -> None:
        parsed = parse_progress_line(line)
        if not parsed:
            return
        key, value = parsed
        if key == "out_time_ms":
            seconds = out_time_ms_to_seconds(value)
            if seconds is None:
                return
            self.last_seconds = min(seconds, self.total_seconds)
            self.on_update(self.last_seconds, self.total_seconds)
        elif key == "progress" and value == "end":
            self.last_seconds = self.total_seconds
            self.on_update(self.last_seconds, self.total_seconds)
