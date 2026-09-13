"""Inspect GoPro GPMF (gpmd) telemetry tracks for usable stabilization data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gopro_360_merge.mp4_atoms import find_child, find_path, parse_moov


@dataclass(frozen=True)
class GpmdSampleTables:
    """Sample tables for the gpmd track."""

    timescale: int
    sample_sizes: list[int]
    chunk_offsets: list[int]  # absolute file offsets
    # stsc: (first_chunk, samples_per_chunk, sample_description_index) 1-based first_chunk
    stsc: list[tuple[int, int, int]]
    # stts: (sample_count, sample_delta)
    stts: list[tuple[int, int]]
    co64: bool


@dataclass(frozen=True)
class GpmdHealth:
    """Result of scanning a file's gpmd track for CORI+GYRO."""

    sample_count: int
    last_good_index: int | None  # inclusive; None if no good sample
    good_count: int
    empty_count: int
    timescale: int
    # Media duration of samples [0 .. last_good_index] inclusive, in timescale units.
    good_media_duration: int
    # Total media duration of all gpmd samples.
    total_media_duration: int

    @property
    def all_empty(self) -> bool:
        return self.sample_count > 0 and self.good_count == 0

    @property
    def needs_trim(self) -> bool:
        return (
            self.last_good_index is not None
            and self.last_good_index < self.sample_count - 1
        )

    @property
    def trimmed_seconds(self) -> float:
        if not self.needs_trim or self.timescale <= 0:
            return 0.0
        dropped = self.total_media_duration - self.good_media_duration
        return dropped / self.timescale

    @property
    def good_seconds(self) -> float:
        if self.timescale <= 0:
            return 0.0
        return self.good_media_duration / self.timescale


def gpmf_payload_sizes(data: bytes) -> dict[str, int]:
    """Return total payload bytes per FourCC key in a GPMF sample (nested)."""
    found: dict[str, int] = {}
    _scan_gpmf(data, found)
    return found


def sample_has_stabilization(data: bytes) -> bool:
    """True when the sample carries non-empty CORI and GYRO (Player needs both)."""
    sizes = gpmf_payload_sizes(data)
    return sizes.get("CORI", 0) > 0 and sizes.get("GYRO", 0) > 0


def _scan_gpmf(data: bytes, found: dict[str, int]) -> None:
    i = 0
    n = len(data)
    while i + 8 <= n:
        fourcc = data[i : i + 4]
        typ = data[i + 4]
        size = data[i + 5]
        repeat = int.from_bytes(data[i + 6 : i + 8], "big")
        i += 8
        payload_len = size * repeat
        pad = (4 - (payload_len % 4)) % 4
        if i + payload_len > n:
            break
        payload = data[i : i + payload_len]
        i += payload_len + pad
        try:
            key = fourcc.decode("ascii")
        except UnicodeDecodeError:
            continue
        if key.isprintable():
            found[key] = found.get(key, 0) + payload_len
        if typ == 0:
            _scan_gpmf(payload, found)


def _read_fullbox_ver(payload: bytes) -> tuple[int, bytes]:
    if len(payload) < 4:
        raise ValueError("truncated fullbox")
    ver = payload[0]
    return ver, payload[4:]


def _parse_stts(payload: bytes) -> list[tuple[int, int]]:
    _, body = _read_fullbox_ver(payload)
    count = int.from_bytes(body[0:4], "big")
    entries: list[tuple[int, int]] = []
    off = 4
    for _ in range(count):
        sc = int.from_bytes(body[off : off + 4], "big")
        delta = int.from_bytes(body[off + 4 : off + 8], "big")
        entries.append((sc, delta))
        off += 8
    return entries


def _parse_stsz(payload: bytes) -> list[int]:
    _, body = _read_fullbox_ver(payload)
    sample_size = int.from_bytes(body[0:4], "big")
    count = int.from_bytes(body[4:8], "big")
    if sample_size != 0:
        return [sample_size] * count
    sizes: list[int] = []
    off = 8
    for _ in range(count):
        sizes.append(int.from_bytes(body[off : off + 4], "big"))
        off += 4
    return sizes


def _parse_stsc(payload: bytes) -> list[tuple[int, int, int]]:
    _, body = _read_fullbox_ver(payload)
    count = int.from_bytes(body[0:4], "big")
    entries: list[tuple[int, int, int]] = []
    off = 4
    for _ in range(count):
        entries.append(
            (
                int.from_bytes(body[off : off + 4], "big"),
                int.from_bytes(body[off + 4 : off + 8], "big"),
                int.from_bytes(body[off + 8 : off + 12], "big"),
            )
        )
        off += 12
    return entries


def _parse_stco(payload: bytes, *, co64: bool) -> list[int]:
    _, body = _read_fullbox_ver(payload)
    count = int.from_bytes(body[0:4], "big")
    width = 8 if co64 else 4
    offsets: list[int] = []
    off = 4
    for _ in range(count):
        offsets.append(int.from_bytes(body[off : off + width], "big"))
        off += width
    return offsets


def _parse_mdhd_timescale(payload: bytes) -> int:
    ver, body = _read_fullbox_ver(payload)
    if ver == 1:
        return int.from_bytes(body[16:20], "big")
    return int.from_bytes(body[8:12], "big")


def _sample_to_chunk_map(
    stsc: list[tuple[int, int, int]], chunk_count: int
) -> list[int]:
    """Return samples_per_chunk for each chunk index 0..chunk_count-1."""
    if not stsc or chunk_count <= 0:
        return []
    samples_per: list[int] = []
    for i, (first, spc, _sdi) in enumerate(stsc):
        next_first = stsc[i + 1][0] if i + 1 < len(stsc) else chunk_count + 1
        for _ in range(first, next_first):
            samples_per.append(spc)
            if len(samples_per) >= chunk_count:
                return samples_per[:chunk_count]
    while len(samples_per) < chunk_count:
        samples_per.append(stsc[-1][1])
    return samples_per[:chunk_count]


def _sample_file_offsets(tables: GpmdSampleTables) -> list[tuple[int, int]]:
    """Return [(file_offset, size), ...] for each sample in order."""
    spc = _sample_to_chunk_map(tables.stsc, len(tables.chunk_offsets))
    result: list[tuple[int, int]] = []
    sample_i = 0
    for chunk_i, base in enumerate(tables.chunk_offsets):
        n = spc[chunk_i] if chunk_i < len(spc) else 1
        cursor = base
        for _ in range(n):
            if sample_i >= len(tables.sample_sizes):
                return result
            sz = tables.sample_sizes[sample_i]
            result.append((cursor, sz))
            cursor += sz
            sample_i += 1
    return result


def _media_duration(stts: list[tuple[int, int]], sample_count: int | None = None) -> int:
    total = 0
    remaining = sample_count
    for count, delta in stts:
        if remaining is not None:
            take = min(count, remaining)
            total += take * delta
            remaining -= take
            if remaining <= 0:
                break
        else:
            total += count * delta
    return total


def load_gpmd_tables(path: Path) -> GpmdSampleTables:
    moov, _off, _size = parse_moov(path)
    for trak in [c for c in moov.children if c.typ == b"trak"]:
        stsd = find_path(trak, b"mdia", b"minf", b"stbl", b"stsd")
        if stsd is None or stsd.payload is None or len(stsd.payload) < 16:
            continue
        # stsd: ver/flags(4) entry_count(4) entry_size(4) format(4)
        codec = stsd.payload[12:16]
        if codec != b"gpmd":
            continue
        stbl = find_path(trak, b"mdia", b"minf", b"stbl")
        mdhd = find_path(trak, b"mdia", b"mdhd")
        if stbl is None or mdhd is None or mdhd.payload is None:
            continue
        stts_a = find_child(stbl, b"stts")
        stsz_a = find_child(stbl, b"stsz")
        stsc_a = find_child(stbl, b"stsc")
        co64_a = find_child(stbl, b"co64")
        stco_a = find_child(stbl, b"stco")
        if not (stts_a and stsz_a and stsc_a and (co64_a or stco_a)):
            raise RuntimeError(f"{path.name}: gpmd track missing sample tables")
        assert stts_a.payload and stsz_a.payload and stsc_a.payload
        co64 = co64_a is not None
        co_atom = co64_a if co64 else stco_a
        assert co_atom is not None and co_atom.payload is not None
        return GpmdSampleTables(
            timescale=_parse_mdhd_timescale(mdhd.payload),
            sample_sizes=_parse_stsz(stsz_a.payload),
            chunk_offsets=_parse_stco(co_atom.payload, co64=co64),
            stsc=_parse_stsc(stsc_a.payload),
            stts=_parse_stts(stts_a.payload),
            co64=co64,
        )
    raise RuntimeError(f"{path.name}: missing GoPro gpmd metadata track")


def inspect_gpmd(path: Path) -> GpmdHealth:
    """Scan gpmd samples; find the last sample with non-empty CORI+GYRO."""
    tables = load_gpmd_tables(path)
    offsets = _sample_file_offsets(tables)
    if len(offsets) != len(tables.sample_sizes):
        if len(tables.chunk_offsets) == len(tables.sample_sizes):
            offsets = list(zip(tables.chunk_offsets, tables.sample_sizes, strict=True))
        else:
            raise RuntimeError(
                f"{path.name}: gpmd sample/chunk table mismatch "
                f"({len(offsets)} offsets vs {len(tables.sample_sizes)} sizes)"
            )

    last_good: int | None = None
    with path.open("rb") as fp:
        for idx in range(len(offsets) - 1, -1, -1):
            off, sz = offsets[idx]
            fp.seek(off)
            data = fp.read(sz)
            if sample_has_stabilization(data):
                last_good = idx
                break
        if last_good is None:
            return GpmdHealth(
                sample_count=len(offsets),
                last_good_index=None,
                good_count=0,
                empty_count=len(offsets),
                timescale=tables.timescale,
                good_media_duration=0,
                total_media_duration=_media_duration(tables.stts),
            )
        good_count = last_good + 1
        empty_count = len(offsets) - good_count

    good_dur = _media_duration(tables.stts, sample_count=good_count)
    total_dur = _media_duration(tables.stts)
    return GpmdHealth(
        sample_count=len(offsets),
        last_good_index=last_good,
        good_count=good_count,
        empty_count=empty_count,
        timescale=tables.timescale,
        good_media_duration=good_dur,
        total_media_duration=total_dur,
    )


def filter_trailing_empty_chapters(
    paths: list[Path],
) -> tuple[list[Path], list[tuple[Path, GpmdHealth]]]:
    """
    Drop trailing chapters whose gpmd track is entirely empty.

    Chapters with a healthy prefix (and empty suffix) are kept — the merge
    post-trim will cut the empty tail. Returns (kept_paths, dropped[(path, health)]).
    """
    if not paths:
        return [], []
    healths = [inspect_gpmd(p) for p in paths]
    end = len(paths)
    dropped: list[tuple[Path, GpmdHealth]] = []
    while end > 0 and healths[end - 1].all_empty:
        end -= 1
        dropped.append((paths[end], healths[end]))
    dropped.reverse()
    kept = paths[:end]
    if not kept:
        raise RuntimeError(
            "all chapters lack usable GPMF stabilization data (CORI/GYRO)"
        )
    return kept, dropped


def verify_gpmd_ends_healthy(path: Path) -> GpmdHealth:
    """Raise if the file's last gpmd sample lacks CORI+GYRO."""
    health = inspect_gpmd(path)
    if health.last_good_index is None:
        raise RuntimeError(
            f"{path.name}: no usable GPMF stabilization data (CORI/GYRO)"
        )
    if health.needs_trim:
        raise RuntimeError(
            f"{path.name}: gpmd still ends with {health.empty_count} empty "
            f"sample(s) (~{health.trimmed_seconds:.1f}s); trim failed"
        )
    return health
