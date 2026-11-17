"""Read the length and picture size of an MP4 or QuickTime (MOV) file.

Only the box headers and a few small boxes are read (ISO/IEC 14496-12): `ftyp` for the
format, `moov/mvhd` for the duration, and each video track's `tkhd` for width, height and
rotation. The media data (`mdat`), which can be gigabytes, is skipped by seeking.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator

# Brands that mark a QuickTime file rather than an ISO MP4.
QUICKTIME_BRANDS = {b"qt  "}
# Top-level boxes a valid file may start with when it has no ftyp (old QuickTime files).
QUICKTIME_FIRST = {b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"}
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"mvex"}
MAX_SMALL_BOX = 1 << 16  # mvhd, tkhd and hdlr are a few hundred bytes


class NotAVideo(ValueError):
    pass


@dataclass
class VideoInfo:
    mime: str  # video/mp4 | video/quicktime
    extension: str  # mp4 | mov
    duration_seconds: float | None
    width: int | None
    height: int | None


def _boxes(f: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """Yield (type, payload_start, box_end) for the boxes between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        payload = pos + 8
        if size == 1:  # 64-bit size follows the type
            large = f.read(8)
            if len(large) < 8:
                return
            size = struct.unpack(">Q", large)[0]
            payload = pos + 16
        elif size == 0:  # box runs to the end of the file / parent
            size = end - pos
        if size < payload - pos or pos + size > end:
            # A truncated last box (an upload cut short) still lets us read what came before.
            if kind == b"mdat":
                yield kind, payload, end
            return
        yield kind, payload, pos + size
        pos += size


def _read(f: BinaryIO, start: int, end: int) -> bytes:
    if end - start > MAX_SMALL_BOX:
        return b""
    f.seek(start)
    return f.read(end - start)


def _fixed(v: int) -> float:
    """Signed 16.16 fixed point."""
    if v >= 1 << 31:
        v -= 1 << 32
    return v / 65536


def _mvhd_duration(data: bytes) -> float | None:
    if len(data) < 20:
        return None
    version = data[0]
    if version == 1 and len(data) >= 32:
        timescale, duration = struct.unpack(">IQ", data[20:32])
    else:
        timescale, duration = struct.unpack(">II", data[12:20])
    if not timescale or duration in (0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return None
    return duration / timescale


def _tkhd_size(data: bytes) -> tuple[int, int] | None:
    """Display width and height, swapped when the track matrix rotates by 90 or 270 degrees."""
    version = data[0] if data else 0
    matrix_at = 4 + (32 if version == 1 else 20) + 16  # flags, times/ids/duration, reserved/layer/volume
    if len(data) < matrix_at + 36 + 8:
        return None
    a, b, _u, c, d, _v, _x, _y, _w = struct.unpack(">9I", data[matrix_at:matrix_at + 36])
    width, height = struct.unpack(">II", data[matrix_at + 36:matrix_at + 44])
    w, h = round(_fixed(width)), round(_fixed(height))
    if w <= 0 or h <= 0:
        return None
    if _fixed(a) == 0 and _fixed(d) == 0 and _fixed(b) != 0 and _fixed(c) != 0:
        w, h = h, w
    return w, h


def _track(f: BinaryIO, start: int, end: int) -> tuple[bytes | None, tuple[int, int] | None]:
    handler, size = None, None
    for kind, p, e in _boxes(f, start, end):
        if kind == b"tkhd":
            size = _tkhd_size(_read(f, p, e))
        elif kind == b"mdia":
            for k2, p2, e2 in _boxes(f, p, e):
                if k2 == b"hdlr":
                    data = _read(f, p2, e2)
                    if len(data) >= 12:
                        handler = data[8:12]
    return handler, size


def probe(f: BinaryIO, file_size: int) -> VideoInfo:
    """Raise NotAVideo unless the file is an MP4 or MOV with a movie header."""
    first = None
    is_quicktime = False
    duration = None
    size: tuple[int, int] | None = None
    fallback_size: tuple[int, int] | None = None
    saw_moov = False
    for kind, p, e in _boxes(f, 0, file_size):
        if first is None:
            first = kind
            if kind == b"ftyp":
                is_quicktime = _read(f, p, e)[0:4] in QUICKTIME_BRANDS  # the major brand
            elif kind in QUICKTIME_FIRST:
                is_quicktime = True
            else:
                break
        if kind == b"moov":
            saw_moov = True
            for k2, p2, e2 in _boxes(f, p, e):
                if k2 == b"mvhd":
                    duration = _mvhd_duration(_read(f, p2, e2))
                elif k2 == b"trak":
                    handler, track_size = _track(f, p2, e2)
                    if track_size and handler == b"vide" and size is None:
                        size = track_size
                    elif track_size and fallback_size is None:
                        fallback_size = track_size
    if first is None or (first != b"ftyp" and first not in QUICKTIME_FIRST):
        raise NotAVideo("This is not an MP4 or MOV video file.")
    if not saw_moov:
        raise NotAVideo("The video file is incomplete (it has no movie header). Export it again and upload the new file.")
    size = size or fallback_size
    return VideoInfo(
        mime="video/quicktime" if is_quicktime else "video/mp4",
        extension="mov" if is_quicktime else "mp4",
        duration_seconds=round(duration, 3) if duration is not None else None,
        width=size[0] if size else None,
        height=size[1] if size else None,
    )
