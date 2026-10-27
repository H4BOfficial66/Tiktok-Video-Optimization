"""Minimal MP4/ISO-BMFF reader based on 7-u8/TikTok-Quality-Toolkit.

Top-level boxes are walked with file seeks (mdat is never loaded into RAM);
the moov box is loaded into memory and parsed from a buffer.
"""

from __future__ import annotations

import os
import struct
from collections import namedtuple
from typing import Iterator, List, Optional, Tuple


class Mp4Error(Exception):
    pass


class Box(namedtuple("Box", "type pos hlen size")):
    @property
    def body(self) -> int:
        return self.pos + self.hlen

    @property
    def end(self) -> int:
        return self.pos + self.size


def u8(buf: bytes, off: int) -> int:
    return buf[off]


def u16(buf: bytes, off: int) -> int:
    return struct.unpack_from(">H", buf, off)[0]


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def u64(buf: bytes, off: int) -> int:
    return struct.unpack_from(">Q", buf, off)[0]


def _box_at(size: int, typ: bytes, pos: int, hlen: int, end: int) -> Box:
    if size < hlen or pos + size > end:
        raise Mp4Error(f"Not a valid MP4 (corrupt box at offset {pos}).")
    return Box(typ.decode("latin-1"), pos, hlen, size)


def file_boxes(f, start: int, end: int) -> Iterator[Box]:
    """Yield top-level Boxes from an open file between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            break
        size, typ = struct.unpack(">I4s", header)
        hlen = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            size = struct.unpack(">Q", ext)[0]
            hlen = 16
        elif size == 0:
            size = end - pos
        yield _box_at(size, typ, pos, hlen, end)
        pos += size


def children(buf: bytes, start: int, end: int) -> Iterator[Box]:
    """Yield Boxes contained in buf[start:end]."""
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack_from(">I4s", buf, pos)
        hlen = 8
        if size == 1:
            size = u64(buf, pos + 8)
            hlen = 16
        elif size == 0:
            size = end - pos
        yield _box_at(size, typ, pos, hlen, end)
        pos += size


def find(buf: bytes, parent: Box, *path: str) -> Optional[Box]:
    """Follow a path of box types below parent (a Box). Returns Box or None."""
    box = parent
    for name in path:
        for child in children(buf, box.body, box.end):
            if child.type == name:
                box = child
                break
        else:
            return None
    return box


def find_all(buf: bytes, parent: Box, name: str) -> List[Box]:
    return [c for c in children(buf, parent.body, parent.end) if c.type == name]


def header_fields(buf: bytes, box: Box) -> Tuple[int, int, int]:
    """Offsets of (timescale, duration, duration_size) in an mvhd/mdhd box."""
    ver = buf[box.body]
    if ver == 0:
        ts = box.body + 12
        return ts, ts + 4, 4
    if ver == 1:
        ts = box.body + 20
        return ts, ts + 4, 8
    raise Mp4Error(f"Unsupported '{box.type}' version {ver}.")


def read_timescale_duration(buf: bytes, box: Box) -> Tuple[int, int]:
    ts_off, dur_off, dsz = header_fields(buf, box)
    dur = u32(buf, dur_off) if dsz == 4 else u64(buf, dur_off)
    return u32(buf, ts_off), dur


def parse_stts(buf: bytes, box: Box) -> List[Tuple[int, int]]:
    n = u32(buf, box.body + 4)
    return [struct.unpack_from(">II", buf, box.body + 8 + i * 8) for i in range(n)]


class Track:
    def __init__(self, buf: bytes, trak: Box):
        self.trak = trak
        self.tkhd = find(buf, trak, "tkhd")
        self.mdia = find(buf, trak, "mdia")
        if self.mdia is None:
            raise Mp4Error("Track without 'mdia' box.")
        self.mdhd = find(buf, self.mdia, "mdhd")
        self.hdlr = find(buf, self.mdia, "hdlr")
        self.handler = buf[self.hdlr.body + 8 : self.hdlr.body + 12].decode("latin-1") if self.hdlr else "?"
        self.timescale, self.duration = read_timescale_duration(buf, self.mdhd) if self.mdhd else (0, 0)
        self.stbl = find(buf, self.mdia, "minf", "stbl")
        self.codec = None
        self.stts: List[Tuple[int, int]] = []
        self.sample_count = 0
        if self.stbl is not None:
            stsd = find(buf, self.stbl, "stsd")
            if stsd is not None and u32(buf, stsd.body + 4) > 0:
                self.codec = buf[stsd.body + 12 : stsd.body + 16].decode("latin-1")
            stts = find(buf, self.stbl, "stts")
            if stts is not None:
                self.stts = parse_stts(buf, stts)
                self.sample_count = sum(c for c, _ in self.stts)

    @property
    def fps(self) -> Optional[float]:
        total = sum(c * d for c, d in self.stts)
        if not total or not self.timescale:
            return None
        return self.sample_count * self.timescale / total

    @property
    def seconds(self) -> float:
        return self.duration / self.timescale if self.timescale else 0.0

    @property
    def header_fps(self) -> Optional[float]:
        return self.sample_count / self.seconds if self.seconds else None

    @property
    def already_patched(self) -> bool:
        a, b = self.fps, self.header_fps
        return bool(a and b) and max(a, b) / min(a, b) > 1.3


class Mp4File:
    """Parsed layout of an MP4 file. moov is held in memory as buf."""

    def __init__(self, path: str):
        self.path = path
        self.file_size = os.path.getsize(path)
        self.top: List[Box] = []
        with open(path, "rb") as f:
            self.top = list(file_boxes(f, 0, self.file_size))
            moov = [b for b in self.top if b.type == "moov"]
            if not moov:
                raise Mp4Error("No 'moov' box found - this is not a valid MP4 file.")
            m = moov[0]
            f.seek(m.pos)
            self.buf = f.read(m.size)
        self.moov_offset = m.pos
        self.moov = Box("moov", 0, m.hlen, m.size)
        self.mvhd = find(self.buf, self.moov, "mvhd")
        if self.mvhd is None:
            raise Mp4Error("No 'mvhd' box found inside 'moov'.")
        self.movie_timescale, self.movie_duration = read_timescale_duration(self.buf, self.mvhd)
        self.tracks = [Track(self.buf, t) for t in find_all(self.buf, self.moov, "trak")]
        if not self.tracks:
            raise Mp4Error("No tracks found in the MP4.")

    def top_types(self) -> List[str]:
        return [b.type for b in self.top]

    @property
    def fragmented(self) -> bool:
        return "moof" in self.top_types() or find(self.buf, self.moov, "mvex") is not None

    @property
    def faststart(self) -> bool:
        types = self.top_types()
        return "mdat" in types and types.index("moov") < types.index("mdat")

    def track(self, handler: str) -> Optional[Track]:
        for t in self.tracks:
            if t.handler == handler:
                return t
        return None

    @property
    def video(self) -> Optional[Track]:
        return self.track("vide")

    @property
    def audio(self) -> Optional[Track]:
        return self.track("soun")
