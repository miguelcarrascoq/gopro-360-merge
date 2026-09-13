"""Trim an MP4/``.360`` moov so all tracks end at the last healthy gpmd sample."""

from __future__ import annotations

from pathlib import Path

from gopro_360_merge.gpmf import inspect_gpmd
from gopro_360_merge.mp4_atoms import (
    Atom,
    find_child,
    find_path,
    parse_moov,
    write_atom,
)


def _u32(n: int) -> bytes:
    return int(n).to_bytes(4, "big")


def _u64(n: int) -> bytes:
    return int(n).to_bytes(8, "big")


def _read_fullbox(payload: bytes) -> tuple[int, int, bytes]:
    ver = payload[0]
    flags = int.from_bytes(payload[1:4], "big")
    return ver, flags, payload[4:]


def _fullbox(ver: int, flags: int, body: bytes) -> bytes:
    return bytes([ver]) + flags.to_bytes(3, "big") + body


def _stts_entries(payload: bytes) -> list[tuple[int, int]]:
    _ver, _flags, body = _read_fullbox(payload)
    count = int.from_bytes(body[0:4], "big")
    out: list[tuple[int, int]] = []
    off = 4
    for _ in range(count):
        out.append(
            (
                int.from_bytes(body[off : off + 4], "big"),
                int.from_bytes(body[off + 4 : off + 8], "big"),
            )
        )
        off += 8
    return out


def _pack_stts(entries: list[tuple[int, int]]) -> bytes:
    body = _u32(len(entries))
    for count, delta in entries:
        body += _u32(count) + _u32(delta)
    return _fullbox(0, 0, body)


def _truncate_stts(
    entries: list[tuple[int, int]], keep_samples: int
) -> tuple[list[tuple[int, int]], int]:
    """Keep the first *keep_samples* samples; return (new_entries, media_duration)."""
    new: list[tuple[int, int]] = []
    remaining = keep_samples
    duration = 0
    for count, delta in entries:
        if remaining <= 0:
            break
        take = min(count, remaining)
        new.append((take, delta))
        duration += take * delta
        remaining -= take
    # Collapse adjacent identical deltas
    collapsed: list[tuple[int, int]] = []
    for count, delta in new:
        if collapsed and collapsed[-1][1] == delta:
            prev_c, prev_d = collapsed[-1]
            collapsed[-1] = (prev_c + count, prev_d)
        else:
            collapsed.append((count, delta))
    return collapsed, duration


def _sample_count_from_stts(entries: list[tuple[int, int]]) -> int:
    return sum(c for c, _ in entries)


def _samples_for_duration(
    entries: list[tuple[int, int]], max_media_duration: int
) -> tuple[int, int]:
    """
    Largest sample count whose cumulative duration is <= *max_media_duration*.

    Returns (keep_samples, actual_duration). Always keeps at least 1 sample when
    the track has samples (unless empty).

    When all sample deltas are 0 (seen on GoPro ``fdsc``), duration cannot be
    derived from stts — callers must handle that case separately.
    """
    total_samples = _sample_count_from_stts(entries)
    if total_samples == 0:
        return 0, 0
    if all(delta == 0 for _count, delta in entries):
        return total_samples, 0
    kept = 0
    duration = 0
    for count, delta in entries:
        if count <= 0:
            continue
        if delta == 0:
            # Zero-delta run: keep all of these samples without advancing time.
            kept += count
            continue
        # How many more samples fit without exceeding max_media_duration?
        room = max_media_duration - duration
        # Need at least one sample overall; allow first sample even if large.
        max_fit = room // delta
        if kept == 0 and max_fit < 1:
            max_fit = 1
        take = min(count, max_fit)
        if take <= 0:
            break
        kept += take
        duration += take * delta
        if take < count:
            break
    return kept, duration


def _mdhd_duration(payload: bytes) -> int:
    ver, _flags, body = _read_fullbox(payload)
    if ver == 1:
        return int.from_bytes(body[20:28], "big")
    return int.from_bytes(body[12:16], "big")


def _parse_stsz(payload: bytes) -> tuple[int, int, list[int]]:
    """Return (sample_size, count, sizes). sizes is empty when sample_size != 0."""
    _ver, _flags, body = _read_fullbox(payload)
    sample_size = int.from_bytes(body[0:4], "big")
    count = int.from_bytes(body[4:8], "big")
    if sample_size != 0:
        return sample_size, count, []
    sizes = [
        int.from_bytes(body[8 + i * 4 : 12 + i * 4], "big") for i in range(count)
    ]
    return 0, count, sizes


def _pack_stsz(sample_size: int, count: int, sizes: list[int]) -> bytes:
    if sample_size != 0:
        body = _u32(sample_size) + _u32(count)
    else:
        body = _u32(0) + _u32(len(sizes))
        for s in sizes:
            body += _u32(s)
    return _fullbox(0, 0, body)


def _parse_stsc(payload: bytes) -> list[tuple[int, int, int]]:
    _ver, _flags, body = _read_fullbox(payload)
    count = int.from_bytes(body[0:4], "big")
    out: list[tuple[int, int, int]] = []
    off = 4
    for _ in range(count):
        out.append(
            (
                int.from_bytes(body[off : off + 4], "big"),
                int.from_bytes(body[off + 4 : off + 8], "big"),
                int.from_bytes(body[off + 8 : off + 12], "big"),
            )
        )
        off += 12
    return out


def _pack_stsc(entries: list[tuple[int, int, int]]) -> bytes:
    body = _u32(len(entries))
    for first, spc, sdi in entries:
        body += _u32(first) + _u32(spc) + _u32(sdi)
    return _fullbox(0, 0, body)


def _parse_chunk_offsets(payload: bytes, *, co64: bool) -> list[int]:
    _ver, _flags, body = _read_fullbox(payload)
    count = int.from_bytes(body[0:4], "big")
    width = 8 if co64 else 4
    return [
        int.from_bytes(body[4 + i * width : 4 + (i + 1) * width], "big")
        for i in range(count)
    ]


def _pack_chunk_offsets(offsets: list[int], *, co64: bool) -> bytes:
    body = _u32(len(offsets))
    for off in offsets:
        body += _u64(off) if co64 else _u32(off)
    return _fullbox(0, 0, body)


def _parse_stss(payload: bytes) -> list[int]:
    _ver, _flags, body = _read_fullbox(payload)
    count = int.from_bytes(body[0:4], "big")
    return [int.from_bytes(body[4 + i * 4 : 8 + i * 4], "big") for i in range(count)]


def _pack_stss(indices: list[int]) -> bytes:
    body = _u32(len(indices))
    for idx in indices:
        body += _u32(idx)
    return _fullbox(0, 0, body)


def _expand_spc(stsc: list[tuple[int, int, int]], chunk_count: int) -> list[int]:
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


def _truncate_chunks(
    stsc: list[tuple[int, int, int]],
    chunk_offsets: list[int],
    keep_samples: int,
) -> tuple[list[tuple[int, int, int]], list[int]]:
    """Keep enough chunks to hold *keep_samples*; adjust last stsc if partial."""
    if keep_samples <= 0:
        return [], []
    spc = _expand_spc(stsc, len(chunk_offsets))
    kept_chunks = 0
    samples = 0
    last_spc = 0
    for i, n in enumerate(spc):
        if samples + n >= keep_samples:
            kept_chunks = i + 1
            last_spc = keep_samples - samples
            break
        samples += n
    else:
        kept_chunks = len(chunk_offsets)
        last_spc = spc[-1] if spc else keep_samples

    new_offsets = chunk_offsets[:kept_chunks]
    # Rebuild compact stsc for kept chunks.
    new_stsc: list[tuple[int, int, int]] = []
    # Use original sample_description_index from first entry (usually 1).
    sdi = stsc[0][2] if stsc else 1
    # Walk original expanded map for kept full chunks, then partial last.
    cursor = 0
    for chunk_i in range(kept_chunks):
        this_spc = last_spc if chunk_i == kept_chunks - 1 else spc[chunk_i]
        chunk_1based = chunk_i + 1
        if not new_stsc or new_stsc[-1][1] != this_spc:
            new_stsc.append((chunk_1based, this_spc, sdi))
        cursor += this_spc
    return new_stsc, new_offsets


def _patch_mdhd_duration(payload: bytes, duration: int) -> bytes:
    ver, flags, body = _read_fullbox(payload)
    body = bytearray(body)
    if ver == 1:
        body[20:28] = _u64(duration)
    else:
        if duration > 0xFFFFFFFF:
            # Promote to version 1 if needed.
            creation = int.from_bytes(body[0:4], "big")
            modification = int.from_bytes(body[4:8], "big")
            timescale = int.from_bytes(body[8:12], "big")
            rest = bytes(body[16:])  # language + pre_defined after old duration
            new_body = (
                _u64(creation)
                + _u64(modification)
                + _u32(timescale)
                + _u64(duration)
                + rest
            )
            return _fullbox(1, flags, new_body)
        body[12:16] = _u32(duration)
    return _fullbox(ver, flags, bytes(body))


def _patch_tkhd_duration(payload: bytes, duration: int) -> bytes:
    ver, flags, body = _read_fullbox(payload)
    body = bytearray(body)
    if ver == 1:
        # creation(8) modification(8) track_ID(4) reserved(4) duration(8)
        body[20:28] = _u64(duration)
    else:
        # creation(4) modification(4) track_ID(4) reserved(4) duration(4)
        if duration > 0xFFFFFFFF:
            creation = int.from_bytes(body[0:4], "big")
            modification = int.from_bytes(body[4:8], "big")
            track_id = int.from_bytes(body[8:12], "big")
            reserved = body[12:16]
            rest = bytes(body[20:])
            new_body = (
                _u64(creation)
                + _u64(modification)
                + _u32(track_id)
                + reserved
                + _u64(duration)
                + rest
            )
            return _fullbox(1, flags, new_body)
        body[16:20] = _u32(duration)
    return _fullbox(ver, flags, bytes(body))


def _patch_mvhd_duration(payload: bytes, duration: int) -> bytes:
    ver, flags, body = _read_fullbox(payload)
    body = bytearray(body)
    if ver == 1:
        # creation(8) modification(8) timescale(4) duration(8)
        body[20:28] = _u64(duration)
    else:
        if duration > 0xFFFFFFFF:
            creation = int.from_bytes(body[0:4], "big")
            modification = int.from_bytes(body[4:8], "big")
            timescale = int.from_bytes(body[8:12], "big")
            rest = bytes(body[16:])
            new_body = (
                _u64(creation)
                + _u64(modification)
                + _u32(timescale)
                + _u64(duration)
                + rest
            )
            return _fullbox(1, flags, new_body)
        body[12:16] = _u32(duration)
    return _fullbox(ver, flags, bytes(body))


def _mvhd_timescale(payload: bytes) -> int:
    ver, _flags, body = _read_fullbox(payload)
    if ver == 1:
        return int.from_bytes(body[16:20], "big")
    return int.from_bytes(body[8:12], "big")


def _mdhd_timescale(payload: bytes) -> int:
    ver, _flags, body = _read_fullbox(payload)
    if ver == 1:
        return int.from_bytes(body[16:20], "big")
    return int.from_bytes(body[8:12], "big")


def _patch_elst(payload: bytes, segment_duration: int) -> bytes:
    ver, flags, body = _read_fullbox(payload)
    entry_count = int.from_bytes(body[0:4], "big")
    if entry_count < 1:
        return payload
    out = bytearray(_u32(entry_count))
    off = 4
    for i in range(entry_count):
        if ver == 1:
            _old_dur = int.from_bytes(body[off : off + 8], "big")
            media_time = body[off + 8 : off + 16]
            rate = body[off + 16 : off + 20]
            # Only patch the first editable segment (media_time != -1).
            media_time_val = int.from_bytes(media_time, "big", signed=True)
            dur = segment_duration if i == 0 and media_time_val != -1 else _old_dur
            if i == 0 and media_time_val != -1:
                dur = segment_duration
            out += _u64(dur) + media_time + rate
            off += 20
        else:
            _old_dur = int.from_bytes(body[off : off + 4], "big")
            media_time = body[off + 4 : off + 8]
            rate = body[off + 8 : off + 12]
            media_time_val = int.from_bytes(media_time, "big", signed=True)
            dur = segment_duration if i == 0 and media_time_val != -1 else _old_dur
            if dur > 0xFFFFFFFF:
                # Rare; keep clamped — GoPro uses ver 0 with 32-bit.
                dur = 0xFFFFFFFF
            out += _u32(dur) + media_time + rate
            off += 12
    return _fullbox(ver, flags, bytes(out))


def _hdlr_type(trak: Atom) -> bytes | None:
    hdlr = find_path(trak, b"mdia", b"hdlr")
    if hdlr is None or hdlr.payload is None or len(hdlr.payload) < 12:
        return None
    # fullbox(4) + pre_defined(4) + handler(4)
    return hdlr.payload[8:12]


def _stsd_codec(trak: Atom) -> bytes | None:
    stsd = find_path(trak, b"mdia", b"minf", b"stbl", b"stsd")
    if stsd is None or stsd.payload is None or len(stsd.payload) < 16:
        return None
    return stsd.payload[12:16]


def _trim_sample_tables(stbl: Atom, keep: int) -> None:
    """Truncate stsz/stco|co64/stsc/stss/sdtp to the first *keep* samples."""
    stsz_a = find_child(stbl, b"stsz")
    if stsz_a is not None and stsz_a.payload is not None:
        sample_size, count, sizes = _parse_stsz(stsz_a.payload)
        if sample_size != 0:
            stsz_a.payload = _pack_stsz(sample_size, keep, [])
        else:
            stsz_a.payload = _pack_stsz(0, keep, sizes[:keep])

    co64_a = find_child(stbl, b"co64")
    stco_a = find_child(stbl, b"stco")
    stsc_a = find_child(stbl, b"stsc")
    if stsc_a is not None and stsc_a.payload is not None and (co64_a or stco_a):
        co64 = co64_a is not None
        co_atom = co64_a if co64 else stco_a
        assert co_atom is not None and co_atom.payload is not None
        offsets = _parse_chunk_offsets(co_atom.payload, co64=co64)
        stsc = _parse_stsc(stsc_a.payload)
        new_stsc, new_offsets = _truncate_chunks(stsc, offsets, keep)
        stsc_a.payload = _pack_stsc(new_stsc)
        co_atom.payload = _pack_chunk_offsets(new_offsets, co64=co64)
        co_atom.typ = b"co64" if co64 else b"stco"

    stss_a = find_child(stbl, b"stss")
    if stss_a is not None and stss_a.payload is not None:
        sync = [i for i in _parse_stss(stss_a.payload) if i <= keep]
        stss_a.payload = _pack_stss(sync)

    sdtp_a = find_child(stbl, b"sdtp")
    if sdtp_a is not None and sdtp_a.payload is not None:
        head = sdtp_a.payload[:4]
        table = sdtp_a.payload[4:]
        sdtp_a.payload = head + table[:keep]


def _trim_track(trak: Atom, cut_seconds: float, movie_timescale: int) -> int:
    """
    Truncate *trak* sample tables to *cut_seconds*.

    Returns the track's new tkhd duration in movie timescale units.
    Timecode (tmcd) keeps its single sample but gets duration patched.
    """
    stbl = find_path(trak, b"mdia", b"minf", b"stbl")
    mdhd = find_path(trak, b"mdia", b"mdhd")
    tkhd = find_child(trak, b"tkhd")
    if stbl is None or mdhd is None or mdhd.payload is None or tkhd is None:
        return 0

    media_ts = _mdhd_timescale(mdhd.payload)
    cut_media = int(cut_seconds * media_ts)

    stts_a = find_child(stbl, b"stts")
    if stts_a is None or stts_a.payload is None:
        return 0
    stts = _stts_entries(stts_a.payload)
    total_samples = _sample_count_from_stts(stts)

    codec = _stsd_codec(trak)
    handler = _hdlr_type(trak)
    is_tmcd = codec == b"tmcd" or handler == b"tmcd"
    zero_delta = total_samples > 0 and all(d == 0 for _, d in stts)

    if is_tmcd or total_samples <= 1:
        media_duration = cut_media
        if total_samples >= 1:
            stts_a.payload = _pack_stts([(1, media_duration)])
    elif zero_delta:
        # GoPro fdsc uses stts deltas of 0; scale sample count by mdhd duration.
        full_media = _mdhd_duration(mdhd.payload) or cut_media
        keep = total_samples
        if full_media > 0:
            keep = max(1, min(total_samples, int(round(total_samples * cut_media / full_media))))
        media_duration = cut_media
        if keep < total_samples:
            stts_a.payload = _pack_stts([(keep, 0)])
            _trim_sample_tables(stbl, keep)
        else:
            stts_a.payload = _pack_stts([(total_samples, 0)])
    else:
        keep, media_duration = _samples_for_duration(stts, cut_media)
        if keep <= 0:
            keep = 1
            media_duration = stts[0][1] if stts else cut_media
        if keep < total_samples:
            new_stts, media_duration = _truncate_stts(stts, keep)
            stts_a.payload = _pack_stts(new_stts)
            _trim_sample_tables(stbl, keep)
        # else: already within cut — keep tables, patch durations below

    movie_dur = int(round(media_duration * movie_timescale / media_ts)) if media_ts else 0
    mdhd.payload = _patch_mdhd_duration(mdhd.payload, media_duration)
    assert tkhd.payload is not None
    tkhd.payload = _patch_tkhd_duration(tkhd.payload, movie_dur)
    elst = find_path(trak, b"edts", b"elst")
    if elst is not None and elst.payload is not None:
        elst.payload = _patch_elst(elst.payload, movie_dur)
    return movie_dur


def trim_moov_to_gpmf(path: Path) -> float:
    """
    Rewrite *path*'s moov so every track ends at the last healthy gpmd sample.

    Leaves ``mdat`` untouched. Returns seconds trimmed from the end (0 if none).
    """
    health = inspect_gpmd(path)
    if health.last_good_index is None:
        raise RuntimeError(
            f"{path.name}: cannot trim — no usable GPMF stabilization data"
        )
    if not health.needs_trim:
        return 0.0

    cut_seconds = health.good_seconds
    trimmed = health.trimmed_seconds

    moov, moov_offset, _old_size = parse_moov(path)
    mvhd = find_child(moov, b"mvhd")
    if mvhd is None or mvhd.payload is None:
        raise RuntimeError(f"{path.name}: moov missing mvhd")
    movie_ts = _mvhd_timescale(mvhd.payload)

    max_movie_dur = 0
    for trak in [c for c in moov.children if c.typ == b"trak"]:
        dur = _trim_track(trak, cut_seconds, movie_ts)
        if dur > max_movie_dur:
            max_movie_dur = dur

    mvhd.payload = _patch_mvhd_duration(mvhd.payload, max_movie_dur)

    # Write new moov at the same offset (GoPro layout: mdat then moov), then truncate.
    with path.open("r+b") as fp:
        fp.seek(moov_offset)
        written = write_atom(fp, moov)
        fp.truncate(moov_offset + written)

    return trimmed
