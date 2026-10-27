"""FPS patch: rewrite mvhd/mdhd timing so TikTok's transcoder keeps 60/120 frames.

Based on 7-u8/TikTok-Quality-Toolkit (the community "120 FPS / 60 FPS method"):
Timescale AND duration are scaled by target/source (e.g. 60 fps -> 30 fps, scale = 0.5).
The declared duration in seconds stays EXACTLY the same (13s stays 13s, 0 extra minutes!).
Plays normally on phones, and TikTok's transcoder keeps all 60 frames intact without 540p demotion.

Only the timing header fields are touched; video/audio sample data remains 100% intact.
"""

from __future__ import annotations

import os
import shutil
import struct
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from .mp4.reader import Box, Mp4Error, Mp4File, header_fields, u32, u64

MODES = ("method", "slowmo")


def exact_fps(fps: float | int | str) -> Fraction:
    """Turn 59.94 / 29.97 / 23.976 style values into exact NTSC fractions."""
    f = float(fps)
    for base in (24, 30, 48, 60, 90, 120, 144, 240):
        ntsc = Fraction(base * 1000, 1001)
        if abs(f - float(ntsc)) < 0.01:
            return ntsc
    return Fraction(f).limit_denominator(1001)


def detect_fps(path: str) -> Optional[float]:
    v = Mp4File(path).video
    return v.fps if v else None


def plan_patches(mp4: Mp4File, scale: Fraction, mode: str = "method", include_audio: bool = True) -> List[Tuple[int, int, int, int, str]]:
    """Return [(abs_offset, byte_size, old, new, label)]."""
    if mode not in MODES:
        raise Mp4Error(f"Unknown mode '{mode}'.")
    if mp4.video is None:
        raise Mp4Error("No video track found in the MP4.")
    buf, base = mp4.buf, mp4.moov_offset
    patches = []

    def add_header(b: Box, label: str):
        ts_off, dur_off, dsz = header_fields(buf, b)
        old_ts = u32(buf, ts_off)
        new_ts = round(old_ts * float(scale))
        if not 1 <= new_ts <= 0xFFFFFFFF:
            raise Mp4Error(f"Timescale {old_ts} of {label} cannot be scaled.")
        patches.append((base + ts_off, 4, old_ts, new_ts, f"{label} timescale"))
        if mode == "method":
            old_d = u32(buf, dur_off) if dsz == 4 else u64(buf, dur_off)
            new_d = round(old_d * float(scale))
            if new_d >= (1 << (8 * dsz)):
                raise Mp4Error(f"Duration of {label} cannot be scaled.")
            patches.append((base + dur_off, dsz, old_d, new_d, f"{label} duration"))

    add_header(mp4.mvhd, "movie (mvhd)")
    for i, t in enumerate(mp4.tracks, 1):
        if t.handler == "vide" or include_audio:
            if t.mdhd:
                add_header(t.mdhd, f"track {i} [{t.handler}] (mdhd)")

    return patches


def patch_fps(
    in_file: str,
    out_file: str,
    fps_in: Optional[float] = None,
    fps_out: float = 30.0,
    mode: str = "method",
    include_audio: bool = True,
) -> List[str]:
    """Write a patched copy of in_file. Returns a list of report lines."""
    if os.path.abspath(in_file) == os.path.abspath(out_file):
        raise Mp4Error("Output file must be different from the input file.")
    mp4 = Mp4File(in_file)
    if mp4.video is not None and mp4.video.already_patched:
        raise Mp4Error("This file is already patched - use the original video.")
    if fps_in is None:
        fps_in = mp4.video.fps if mp4.video else None
        if not fps_in:
            raise Mp4Error("Could not detect the source FPS - enter it manually.")
    if float(fps_in) <= float(fps_out):
        raise Mp4Error(f"Source FPS ({float(fps_in):g}) must be higher than target FPS ({float(fps_out):g}).")

    scale = exact_fps(fps_out) / exact_fps(fps_in)
    patches = plan_patches(mp4, scale, mode, include_audio)

    shutil.copyfile(in_file, out_file)
    report = [f"FPS patch ({mode}): {float(fps_in):.3f} -> {float(fps_out):g} fps, scale {float(scale):.5f}"]
    try:
        with open(out_file, "r+b") as f:
            for off, size, old, new, label in patches:
                f.seek(off)
                f.write(new.to_bytes(size, "big"))
                note = "" if old * float(scale) == new else " (rounded)"
                report.append(f"   {label}: {old} -> {new}{note}")
    except Exception:
        try:
            os.remove(out_file)
        except OSError:
            pass
        raise
    return report


def patch_fps_bytes(
    data: bytes,
    fps_in: Optional[float] = None,
    fps_out: float = 30.0,
    mode: str = "method",
    include_audio: bool = True,
) -> Tuple[bytes, Dict[str, Any]]:
    """In-memory FPS patch for byte buffers (API / Web)."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as src_tmp:
        src_path = src_tmp.name
        src_tmp.write(data)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as dst_tmp:
        dst_path = dst_tmp.name

    try:
        report = patch_fps(src_path, dst_path, fps_in=fps_in, fps_out=fps_out, mode=mode, include_audio=include_audio)
        m = Mp4File(dst_path)
        with open(dst_path, "rb") as f:
            out_bytes = f.read()

        stats = {
            "source_fps": float(fps_in) if fps_in else (m.video.fps if m.video else 30.0),
            "target_fps": float(fps_out),
            "duration_sec": m.video.seconds if m.video else 0.0,
            "mode": mode,
            "report": report,
            "output_size": len(out_bytes),
        }
        return out_bytes, stats
    finally:
        for p in (src_path, dst_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
