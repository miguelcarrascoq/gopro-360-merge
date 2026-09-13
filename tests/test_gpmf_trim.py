"""Unit tests for GPMF inspection and sample-table trimming helpers."""

from __future__ import annotations

import unittest

from gopro_360_merge.gpmf import gpmf_payload_sizes, sample_has_stabilization
from gopro_360_merge.moov_trim import (
    _pack_stts,
    _samples_for_duration,
    _truncate_chunks,
    _truncate_stts,
)


def _klv(fourcc: bytes, typ: int, size: int, repeat: int, payload: bytes) -> bytes:
    pad = (4 - (len(payload) % 4)) % 4
    assert len(payload) == size * repeat, (len(payload), size, repeat)
    return fourcc + bytes([typ, size]) + repeat.to_bytes(2, "big") + payload + (b"\x00" * pad)


def _nest(fourcc: bytes, children: bytes) -> bytes:
    """Container KLV (type 0): size=1, repeat=len(payload)."""
    return _klv(fourcc, 0, 1, len(children), children)


def _make_devc(*, cori: int, gyro: int) -> bytes:
    """Minimal DEVC sample with optional CORI/GYRO payloads."""
    streams = b""
    cori_payload = b"\x00" * cori
    gyro_payload = b"\x00" * gyro
    cori_klv = _klv(
        b"CORI",
        ord("s"),
        8 if cori else 8,
        (cori // 8) if cori else 0,
        cori_payload,
    )
    gyro_klv = _klv(
        b"GYRO",
        ord("s"),
        6 if gyro else 6,
        (gyro // 6) if gyro else 0,
        gyro_payload,
    )
    streams += _nest(b"STRM", cori_klv)
    streams += _nest(b"STRM", gyro_klv)
    if cori == 0 and gyro == 0:
        streams += _klv(b"EMPT", ord("L"), 4, 1, b"\x00\x00\x00\x00")
    return _nest(b"DEVC", streams)


class GpmfParseTests(unittest.TestCase):
    def test_healthy_sample_has_cori_and_gyro(self) -> None:
        data = _make_devc(cori=240, gyro=4782)
        sizes = gpmf_payload_sizes(data)
        self.assertGreater(sizes.get("CORI", 0), 0)
        self.assertGreater(sizes.get("GYRO", 0), 0)
        self.assertTrue(sample_has_stabilization(data))

    def test_empty_sample_rejected(self) -> None:
        data = _make_devc(cori=0, gyro=0)
        sizes = gpmf_payload_sizes(data)
        self.assertEqual(sizes.get("CORI", 0), 0)
        self.assertEqual(sizes.get("GYRO", 0), 0)
        self.assertIn("EMPT", sizes)
        self.assertFalse(sample_has_stabilization(data))


class SttsTrimTests(unittest.TestCase):
    def test_truncate_stts_partial_entry(self) -> None:
        entries = [(482, 1001), (12, 1001)]
        new, dur = _truncate_stts(entries, 343)
        self.assertEqual(new, [(343, 1001)])
        self.assertEqual(dur, 343 * 1001)

    def test_samples_for_duration_bulk_entry(self) -> None:
        entries = [(1_000_000, 1)]
        keep, dur = _samples_for_duration(entries, 5000)
        self.assertEqual(keep, 5000)
        self.assertEqual(dur, 5000)

    def test_zero_delta_stts_returns_all(self) -> None:
        entries = [(1000, 0)]
        keep, dur = _samples_for_duration(entries, 5000)
        self.assertEqual(keep, 1000)
        self.assertEqual(dur, 0)

    def test_pack_roundtrip_length(self) -> None:
        payload = _pack_stts([(100, 1001), (1, 500)])
        self.assertEqual(payload[0], 0)  # version
        self.assertEqual(int.from_bytes(payload[4:8], "big"), 2)


class ChunkTrimTests(unittest.TestCase):
    def test_one_sample_per_chunk(self) -> None:
        stsc = [(1, 1, 1)]
        offsets = list(range(0, 1000, 10))  # 100 chunks
        new_stsc, new_off = _truncate_chunks(stsc, offsets, 50)
        self.assertEqual(len(new_off), 50)
        self.assertEqual(new_stsc, [(1, 1, 1)])

    def test_partial_last_chunk(self) -> None:
        stsc = [(1, 10, 1)]
        offsets = [100, 200, 300]
        new_stsc, new_off = _truncate_chunks(stsc, offsets, 25)
        self.assertEqual(len(new_off), 3)
        self.assertEqual(new_stsc, [(1, 10, 1), (3, 5, 1)])


if __name__ == "__main__":
    unittest.main()
