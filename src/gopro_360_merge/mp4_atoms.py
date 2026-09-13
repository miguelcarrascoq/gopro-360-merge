"""Lightweight MP4 / ISO-BMFF atom walking and rewriting helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


@dataclass
class Atom:
    """One MP4 box. Leaf boxes keep raw ``payload``; containers keep ``children``."""

    typ: bytes
    payload: bytes | None = None
    children: list[Atom] = field(default_factory=list)
    # Original on-disk header was 16 bytes (largesize) — prefer 32-bit when rewriting
    # unless the body forces largesize.
    force_large: bool = False

    @property
    def is_container(self) -> bool:
        return self.payload is None


def read_atoms(fp: BinaryIO, start: int, end: int) -> list[tuple[bytes, int, int, int]]:
    """Return [(fourcc, offset, size, header_len), ...] between *start* and *end*."""
    atoms: list[tuple[bytes, int, int, int]] = []
    pos = start
    while pos + 8 <= end:
        fp.seek(pos)
        hdr = fp.read(8)
        if len(hdr) < 8:
            break
        size32 = int.from_bytes(hdr[:4], "big")
        tag = hdr[4:8]
        header_len = 8
        if size32 == 1:
            ext = fp.read(8)
            if len(ext) < 8:
                break
            size = int.from_bytes(ext, "big")
            header_len = 16
        elif size32 == 0:
            size = end - pos
        else:
            size = size32
        if size < header_len:
            break
        atoms.append((tag, pos, size, header_len))
        pos += size
    return atoms


def top_level_atoms(path: Path) -> list[tuple[bytes, int, int, int]]:
    size = path.stat().st_size
    with path.open("rb") as fp:
        return read_atoms(fp, 0, size)


_CONTAINER_TYPES = frozenset(
    {
        b"moov",
        b"trak",
        b"edts",
        b"mdia",
        b"minf",
        b"stbl",
        b"dinf",
        b"udta",
    }
)


def _parse_tree(fp: BinaryIO, start: int, end: int) -> list[Atom]:
    result: list[Atom] = []
    for tag, offset, size, header_len in read_atoms(fp, start, end):
        body_start = offset + header_len
        body_end = offset + size
        force_large = header_len == 16
        if tag in _CONTAINER_TYPES or (tag == b"stsd"):
            # stsd has a 8-byte fullbox preamble then sample entries (treated as opaque
            # children only when we need them — keep as leaf for simplicity except
            # known containers).
            if tag == b"stsd":
                fp.seek(body_start)
                result.append(
                    Atom(typ=tag, payload=fp.read(body_end - body_start), force_large=force_large)
                )
            else:
                children = _parse_tree(fp, body_start, body_end)
                result.append(Atom(typ=tag, children=children, force_large=force_large))
        else:
            fp.seek(body_start)
            result.append(
                Atom(typ=tag, payload=fp.read(body_end - body_start), force_large=force_large)
            )
    return result


def parse_moov(path: Path) -> tuple[Atom, int, int]:
    """Return (moov Atom, file offset, original size). Raises if moov missing."""
    for tag, offset, size, header_len in top_level_atoms(path):
        if tag != b"moov":
            continue
        with path.open("rb") as fp:
            children = _parse_tree(fp, offset + header_len, offset + size)
        return Atom(typ=b"moov", children=children, force_large=header_len == 16), offset, size
    raise RuntimeError(f"{path.name}: missing moov atom")


def atom_size(atom: Atom) -> int:
    if atom.is_container:
        body = sum(atom_size(c) for c in atom.children)
    else:
        body = len(atom.payload or b"")
    header = 16 if (atom.force_large or body + 8 > 0xFFFFFFFF) else 8
    return header + body


def write_atom(fp: BinaryIO, atom: Atom) -> int:
    """Serialize *atom* and return bytes written."""
    if atom.is_container:
        body_size = sum(atom_size(c) for c in atom.children)
    else:
        body_size = len(atom.payload or b"")
    total = body_size + 8
    use_large = atom.force_large or total > 0xFFFFFFFF
    if use_large:
        total = body_size + 16
        fp.write((1).to_bytes(4, "big"))
        fp.write(atom.typ)
        fp.write(total.to_bytes(8, "big"))
    else:
        fp.write(total.to_bytes(4, "big"))
        fp.write(atom.typ)
    if atom.is_container:
        for child in atom.children:
            write_atom(fp, child)
    else:
        fp.write(atom.payload or b"")
    return total


def find_child(atom: Atom, typ: bytes) -> Atom | None:
    for child in atom.children:
        if child.typ == typ:
            return child
    return None


def find_children(atom: Atom, typ: bytes) -> list[Atom]:
    return [c for c in atom.children if c.typ == typ]


def find_path(atom: Atom, *types: bytes) -> Atom | None:
    current: Atom | None = atom
    for typ in types:
        if current is None:
            return None
        current = find_child(current, typ)
    return current
