from __future__ import annotations

import struct
import sys

from .mp4.parser import (
    find_box, find_box_path, find_track_by_handler,
    parse_stco, parse_co64, parse_stsz, parse_stsc, parse_stts, parse_nalu_list,
    read_u32,
)
from .mp4.builder import (
    build_box, build_ftyp, build_free, build_mvhd, build_mdhd,
    build_tkhd, build_hdlr, build_stts, build_stsc, build_stsz,
    build_stco, build_stsd_video, build_avcc, build_btrt, build_udta_comment,
)

# 8-byte filler NAL: length=4, type=0 (filler), empty payload
PADDING_NAL = b'\x00\x00\x00\x04\x00\x00\x00\x00'
PADDING_SIZE = 8

# NALU types to keep in first sample (slices + SPS/PPS parameters, strip SEI etc.)
KEEP_NALU_TYPES = {1, 5, 7, 8}  # non-IDR slice, IDR slice, SPS, PPS

# ISO 639-2/T packed 'und'
LANG_UND = 0x55c4
DEFAULT_COMMENT = 'TK8vY5VqBA6hUlo1yuGvNA'


def transform(input_path: str, output_path: str, multiplier: int = 10,
              comment: str = DEFAULT_COMMENT, verbose: bool = True) -> dict:
    """
    Apply the TikTok Video Optimizer container transformation pipeline.

    Args:
        input_path: Path to source MP4 file (H.264/AVC).
        output_path: Path to write the manipulated MP4.
        multiplier: Frame count multiplier (default 10x for 1080p60 trigger).
        comment: Metadata bypass signature string (default TK8vY5VqBA6hUlo1yuGvNA).
        verbose: Print progress messages.

    Returns:
        Dict with stats about the transformation.
    """
    with open(input_path, 'rb') as f:
        data = f.read()

    file_size = len(data)
    if verbose:
        print(f"[*] Input: {input_path} ({file_size:,} bytes)")

    # Parse source
    moov_pos, moov_size = find_box(data, 'moov')
    mdat_pos, mdat_size = find_box(data, 'mdat')
    if moov_pos is None or mdat_pos is None:
        raise ValueError("Unsupported file: missing moov or mdat box. Use a standard H.264 MP4.")

    mvhd_pos, mvhd_size = find_box(data, 'mvhd', moov_pos + 8, moov_pos + moov_size)
    mv_timescale = read_u32(data, mvhd_pos + 20) if mvhd_pos is not None else 1000
    if mv_timescale == 0:
        mv_timescale = 1000

    mdat_data_start = mdat_pos + 8

    # Video track
    vt_pos, vt_size = find_track_by_handler(data, moov_pos, moov_size, b'vide')
    if vt_pos is None:
        raise ValueError("Unsupported file: no video track found with handler 'vide'.")

    vt_start, vt_end = vt_pos + 8, vt_pos + vt_size
    stbl_pos, stbl_size = find_box_path(data, ['mdia', 'minf', 'stbl'], vt_start, vt_end)
    if stbl_pos is None:
        raise ValueError("Invalid MP4 structure: missing video sample table (stbl).")
    sb_start, sb_end = stbl_pos + 8, stbl_pos + stbl_size

    stco_pos, _ = find_box(data, 'stco', sb_start, sb_end)
    co64_pos, _ = find_box(data, 'co64', sb_start, sb_end) if stco_pos is None else (None, None)
    stsz_pos, _ = find_box(data, 'stsz', sb_start, sb_end)
    stsc_pos, _ = find_box(data, 'stsc', sb_start, sb_end)
    stts_pos, _ = find_box(data, 'stts', sb_start, sb_end)
    stsd_pos, stsd_size = find_box(data, 'stsd', sb_start, sb_end)
    stss_pos, stss_size = find_box(data, 'stss', sb_start, sb_end)
    sdtp_pos, sdtp_size = find_box(data, 'sdtp', sb_start, sb_end)
    ctts_pos, ctts_size = find_box(data, 'ctts', sb_start, sb_end)

    if stco_pos is None and co64_pos is None:
        raise ValueError("Invalid MP4: missing stco/co64 chunk offsets in video track.")
    if stsz_pos is None or stsc_pos is None or stts_pos is None or stsd_pos is None:
        raise ValueError("Invalid MP4: missing critical sample tables in video track.")

    video_chunks = parse_stco(data, stco_pos) if stco_pos is not None else parse_co64(data, co64_pos)
    video_sizes = parse_stsz(data, stsz_pos)
    video_stsc = parse_stsc(data, stsc_pos)
    video_stts = parse_stts(data, stts_pos)

    if not video_chunks or not video_sizes or not video_stts:
        raise ValueError("Invalid MP4: video stream contains 0 frames or empty sample tables.")

    orig_frames = len(video_sizes)
    time_delta = video_stts[0][1]
    multiplier = max(1, multiplier)
    pad_count = orig_frames * (multiplier - 1) if multiplier > 1 else 0
    total_frames = orig_frames + pad_count

    v_mdhd_pos, v_mdhd_size = find_box_path(data, ['mdia', 'mdhd'], vt_start, vt_end)
    if v_mdhd_pos is None:
        raise ValueError("Invalid MP4: missing video media header (mdhd).")
    v_timescale = read_u32(data, v_mdhd_pos + 20)
    v_duration = read_u32(data, v_mdhd_pos + 24)
    v_duration_sec = (v_duration / v_timescale) if v_timescale > 0 else 0.0

    if verbose:
        fps = (v_timescale / time_delta) if time_delta > 0 else 0
        print(f"[*] Video: {orig_frames} frames @ {fps:.0f} fps, {v_duration_sec:.2f}s")
        if pad_count > 0:
            print(f"[*] Ghost frames: +{pad_count} -> {total_frames} declared (1080p60 Trigger • Micro-tick timing)")
        else:
            print(f"[*] Safe mode (1x): Exact duration {v_duration_sec:.2f}s preserved (Public Safe / No Shadowban)")

    # Strip SEI from first sample
    first_off = video_chunks[0]
    first_size = video_sizes[0]
    first_sample = data[first_off:first_off + first_size]

    nalus = parse_nalu_list(first_sample)
    kept = [d for t, _, d in nalus if t in KEEP_NALU_TYPES]
    new_first = b''.join(kept) if kept else first_sample
    new_first_size = len(new_first)
    sei_removed = first_size - new_first_size

    if verbose:
        print(f"[*] First sample: {first_size} -> {new_first_size} bytes (-{sei_removed} SEI)")

    # Audio track
    at_pos, at_size = find_track_by_handler(data, moov_pos, moov_size, b'soun')
    has_audio = at_pos is not None

    if has_audio:
        at_start, at_end = at_pos + 8, at_pos + at_size
        a_stbl_pos, _ = find_box_path(data, ['mdia', 'minf', 'stbl'], at_start, at_end)
        a_sb_start, a_sb_end = a_stbl_pos + 8, a_stbl_pos + _

        a_stco_pos, _ = find_box(data, 'stco', a_sb_start, a_sb_end)
        a_co64_pos, _ = find_box(data, 'co64', a_sb_start, a_sb_end) if a_stco_pos is None else (None, None)
        a_stsz_pos, a_stsz_size = find_box(data, 'stsz', a_sb_start, a_sb_end)
        a_stsc_pos, a_stsc_size = find_box(data, 'stsc', a_sb_start, a_sb_end)
        a_stts_pos, _ = find_box(data, 'stts', a_sb_start, a_sb_end)
        a_stsd_pos, a_stsd_size = find_box(data, 'stsd', a_sb_start, a_sb_end)
        a_sgpd_pos, a_sgpd_size = find_box(data, 'sgpd', a_sb_start, a_sb_end)
        a_sbgp_pos, a_sbgp_size = find_box(data, 'sbgp', a_sb_start, a_sb_end)

        if (a_stco_pos is None and a_co64_pos is None) or a_stsz_pos is None or a_stts_pos is None or a_stsd_pos is None:
            has_audio = False
        else:
            audio_chunks = parse_stco(data, a_stco_pos) if a_stco_pos is not None else parse_co64(data, a_co64_pos)
            audio_sizes = parse_stsz(data, a_stsz_pos)
            audio_stts = parse_stts(data, a_stts_pos)

        a_mdhd_pos, a_mdhd_size = find_box_path(data, ['mdia', 'mdhd'], at_start, at_end)
        a_timescale = read_u32(data, a_mdhd_pos + 20)

        # Read audio ELST for timing alignment
        a_elst_pos, _ = find_box_path(data, ['edts', 'elst'], at_start, at_end)
        a_elst_seg_dur = read_u32(data, a_elst_pos + 16) if a_elst_pos else 0
        a_elst_media_time = read_u32(data, a_elst_pos + 20) if a_elst_pos else 0

        if a_elst_pos and a_elst_seg_dur > 0:
            new_audio_dur_ms = a_elst_seg_dur
        else:
            a_mdhd_dur = read_u32(data, a_mdhd_pos + 24)
            new_audio_dur_ms = (a_mdhd_dur * mv_timescale // a_timescale) if a_timescale > 0 else 0
        new_audio_dur = (new_audio_dur_ms * a_timescale // mv_timescale) if a_timescale > 0 else 0

        # STTS: align to mdhd + media_time
        a_delta = audio_stts[0][1] if audio_stts else 1024
        a_count = len(audio_sizes)
        target_stts = new_audio_dur + a_elst_media_time
        main_n = a_count - 1
        last_delta = target_stts - main_n * a_delta

        if main_n > 0 and 0 < last_delta <= a_delta:
            new_a_stts = [(main_n, a_delta), (1, last_delta)]
        else:
            new_a_stts = audio_stts
            new_audio_dur = read_u32(data, a_mdhd_pos + 24)
            new_audio_dur_ms = (new_audio_dur * mv_timescale // a_timescale) if a_timescale > 0 else 0

        # Bitrates
        total_a_bytes = sum(audio_sizes)
        old_a_ticks = sum(c * d for c, d in audio_stts)
        new_a_ticks = sum(c * d for c, d in new_a_stts)
        old_a_br = (total_a_bytes * 8 * a_timescale // old_a_ticks) if old_a_ticks > 0 else 128000
        new_a_br = (total_a_bytes * 8 * a_timescale // new_a_ticks) if new_a_ticks > 0 else 128000

    # Build new mdat
    mdat_before = data[mdat_data_start:first_off]
    mdat_after = data[first_off + first_size:mdat_pos + mdat_size]
    new_mdat_content = mdat_before + new_first + mdat_after + (PADDING_NAL if pad_count > 0 else b'')
    new_mdat = struct.pack('>I', 8 + len(new_mdat_content)) + b'mdat' + new_mdat_content

    # Build new moov
    ftyp = build_ftyp()
    free = build_free()

    # MVHD
    mvhd_pos, mvhd_size = find_box(data, 'mvhd', moov_pos + 8, moov_pos + moov_size)
    mvhd_content = data[mvhd_pos + 8:mvhd_pos + mvhd_size]
    v_tkhd_pos, _ = find_box(data, 'tkhd', vt_start, vt_end)
    v_tkhd_dur_ms = read_u32(data, v_tkhd_pos + 28)
    new_mvhd_dur = max(v_tkhd_dur_ms, new_audio_dur_ms) if has_audio else v_tkhd_dur_ms
    mvhd = build_mvhd(mvhd_content, new_mvhd_dur)

    # Video track components
    video_tkhd = data[vt_start:vt_start + read_u32(data, vt_start)]
    edts_pos, edts_size = find_box(data, 'edts', vt_start, vt_end)
    if edts_pos:
        video_edts = data[edts_pos:edts_pos + edts_size]
    elif v_tkhd_dur_ms > 0:
        elst_body = struct.pack('>IIIIiHH', 0, 1, v_tkhd_dur_ms, 0, 1, 0)
        video_edts = build_box('edts', build_box('elst', elst_body))
    else:
        video_edts = b''
    video_mdhd = data[v_mdhd_pos:v_mdhd_pos + v_mdhd_size]
    v_hdlr_pos, v_hdlr_size = find_box_path(data, ['mdia', 'hdlr'], vt_start, vt_end)
    video_hdlr = data[v_hdlr_pos:v_hdlr_pos + v_hdlr_size]
    vmhd_pos, vmhd_size = find_box_path(data, ['mdia', 'minf', 'vmhd'], vt_start, vt_end)
    video_vmhd = data[vmhd_pos:vmhd_pos + vmhd_size]
    dinf_pos, dinf_size = find_box_path(data, ['mdia', 'minf', 'dinf'], vt_start, vt_end)
    video_dinf = data[dinf_pos:dinf_pos + dinf_size]

    # Video STSD (avcC + btrt patched)
    stsd_content = data[stsd_pos + 8:stsd_pos + stsd_size]
    avc1_fixed = stsd_content[16:94]

    avcc_rel = stsd_content.find(b'avcC')
    avcc_start = avcc_rel - 4
    avcc_orig_size = read_u32(stsd_content, avcc_start)
    avcc_end = avcc_start + avcc_orig_size
    avcc_new = build_avcc(stsd_content[avcc_start + 8:avcc_start + avcc_orig_size])

    colr_box = b''
    colr_rel = stsd_content.find(b'colr', avcc_end)
    if colr_rel >= 0:
        cs = colr_rel - 4
        colr_box = stsd_content[cs:cs + read_u32(stsd_content, cs)]

    pasp_box = b''
    pasp_rel = stsd_content.find(b'pasp', avcc_end)
    if pasp_rel >= 0:
        ps = pasp_rel - 4
        pasp_box = stsd_content[ps:ps + read_u32(stsd_content, ps)]

    total_v_bytes = sum(video_sizes) - first_size + new_first_size
    new_v_avg_br = int(total_v_bytes * 8 / v_duration_sec) if v_duration_sec > 0 else 10_000_000
    btrt_rel = stsd_content.find(b'btrt', avcc_end)
    max_br = read_u32(stsd_content, btrt_rel + 8) if btrt_rel >= 0 else new_v_avg_br
    btrt_new = build_btrt(0, max_br, new_v_avg_br)

    video_stsd_new = build_stsd_video(avc1_fixed, avcc_new, colr_box, pasp_box, btrt_new)

    # Video tables
    if pad_count > 0:
        # Ghost frames inherit native time_delta so the container declared framerate
        # remains exactly 60.0 fps (or 30.0 fps) — avoiding TikTok transcoder 540p downscale rejection.
        # Playback duration remains clipped to exact original length via mvhd/tkhd/edts.
        video_stts_new = build_stts(list(video_stts) + [(pad_count, time_delta)])
        video_stsc_new = build_stsc(list(video_stsc) + [(len(video_chunks) + 1, 1, 1)])
        video_stsz_new = build_stsz([new_first_size] + video_sizes[1:] + [PADDING_SIZE] * pad_count)
    else:
        video_stts_new = build_stts(video_stts)
        video_stsc_new = build_stsc(list(video_stsc))
        video_stsz_new = build_stsz([new_first_size] + video_sizes[1:])

    video_stss = data[stss_pos:stss_pos + stss_size] if stss_pos else b''
    video_sdtp = data[sdtp_pos:sdtp_pos + sdtp_size] if sdtp_pos else b''
    video_ctts = data[ctts_pos:ctts_pos + ctts_size] if ctts_pos else b''

    # STCO placeholder
    total_v_chunks = len(video_chunks) + pad_count
    stco_ph_size = 16 + total_v_chunks * 4
    v_stco_ph = struct.pack('>I', stco_ph_size) + b'stco' + b'\x00' * (stco_ph_size - 8)

    v_stbl_parts = [video_stsd_new, video_stts_new]
    if video_stss: v_stbl_parts.append(video_stss)
    if video_sdtp: v_stbl_parts.append(video_sdtp)
    if video_ctts: v_stbl_parts.append(video_ctts)
    v_stbl_parts += [video_stsc_new, video_stsz_new, v_stco_ph]

    video_stbl = build_box('stbl', b''.join(v_stbl_parts))
    video_minf = build_box('minf', video_vmhd + video_dinf + video_stbl)
    video_mdia = build_box('mdia', video_mdhd + video_hdlr + video_minf)
    video_trak = build_box('trak', video_tkhd + video_edts + video_mdia)

    # Audio track
    if has_audio:
        a_tkhd_pos, a_tkhd_size = find_box(data, 'tkhd', at_start, at_end)
        audio_tkhd = build_tkhd(data[a_tkhd_pos:a_tkhd_pos + a_tkhd_size], new_audio_dur_ms)

        a_edts_pos, a_edts_size = find_box(data, 'edts', at_start, at_end)
        audio_edts = data[a_edts_pos:a_edts_pos + a_edts_size] if a_edts_pos else b''

        audio_mdhd = build_mdhd(data[a_mdhd_pos + 8:a_mdhd_pos + a_mdhd_size],
                                duration=new_audio_dur, language=LANG_UND)
        audio_hdlr = build_hdlr(b'soun', 'SoundHandler')

        smhd_pos, smhd_size = find_box_path(data, ['mdia', 'minf', 'smhd'], at_start, at_end)
        audio_smhd = data[smhd_pos:smhd_pos + smhd_size]
        a_dinf_pos, a_dinf_size = find_box_path(data, ['mdia', 'minf', 'dinf'], at_start, at_end)
        audio_dinf = data[a_dinf_pos:a_dinf_pos + a_dinf_size]

        # Patch audio stsd bitrate safely (only if old_a_br is non-zero)
        if old_a_br > 0 and old_a_br != new_a_br:
            audio_stsd = bytearray(data[a_stsd_pos:a_stsd_pos + a_stsd_size])
            old_br = struct.pack('>I', old_a_br)
            new_br = struct.pack('>I', new_a_br)
            p = 0
            while (idx := audio_stsd.find(old_br, p)) != -1:
                audio_stsd[idx:idx + 4] = new_br
                p = idx + 4
            audio_stsd = bytes(audio_stsd)
        else:
            audio_stsd = data[a_stsd_pos:a_stsd_pos + a_stsd_size]

        audio_stts_new = build_stts(new_a_stts)
        audio_stsc = data[a_stsc_pos:a_stsc_pos + a_stsc_size]
        audio_stsz = data[a_stsz_pos:a_stsz_pos + a_stsz_size]

        a_stco_ph_size = 16 + len(audio_chunks) * 4
        a_stco_ph = struct.pack('>I', a_stco_ph_size) + b'stco' + b'\x00' * (a_stco_ph_size - 8)

        a_sgpd = data[a_sgpd_pos:a_sgpd_pos + a_sgpd_size] if a_sgpd_pos else b''
        a_sbgp = data[a_sbgp_pos:a_sbgp_pos + a_sbgp_size] if a_sbgp_pos else b''

        audio_stbl = build_box('stbl', audio_stsd + audio_stts_new + audio_stsc +
                               audio_stsz + a_stco_ph + a_sgpd + a_sbgp)
        audio_minf = build_box('minf', audio_smhd + audio_dinf + audio_stbl)
        audio_mdia = build_box('mdia', audio_mdhd + audio_hdlr + audio_minf)
        audio_trak = build_box('trak', audio_tkhd + audio_edts + audio_mdia)
    else:
        audio_trak = b''

    udta = build_udta_comment(comment) if comment and comment.strip() else b''
    moov = build_box('moov', mvhd + video_trak + audio_trak + udta)
    moov_size_final = len(moov)

    # Fill STCO offsets
    new_mdat_start = len(ftyp) + len(free) + moov_size_final + 8
    first_video_rel = first_off - mdat_data_start
    pad_abs = new_mdat_start + len(new_mdat_content) - PADDING_SIZE

    new_v_offsets = []
    for off in video_chunks:
        rel = off - mdat_data_start
        new_v_offsets.append(new_mdat_start + rel if rel <= first_video_rel
                            else new_mdat_start + rel - sei_removed)
    if pad_count > 0:
        new_v_offsets.extend([pad_abs] * pad_count)

    new_a_offsets = []
    if has_audio:
        for off in audio_chunks:
            rel = off - mdat_data_start
            new_a_offsets.append(new_mdat_start + rel if rel < first_video_rel
                                else new_mdat_start + rel - sei_removed)

    # Rebuild with final STCOs
    v_stbl_parts[-1] = build_stco(new_v_offsets)
    video_stbl = build_box('stbl', b''.join(v_stbl_parts))
    video_minf = build_box('minf', video_vmhd + video_dinf + video_stbl)
    video_mdia = build_box('mdia', video_mdhd + video_hdlr + video_minf)
    video_trak = build_box('trak', video_tkhd + video_edts + video_mdia)

    if has_audio:
        audio_stbl = build_box('stbl', audio_stsd + audio_stts_new + audio_stsc +
                               audio_stsz + build_stco(new_a_offsets) + a_sgpd + a_sbgp)
        audio_minf = build_box('minf', audio_smhd + audio_dinf + audio_stbl)
        audio_mdia = build_box('mdia', audio_mdhd + audio_hdlr + audio_minf)
        audio_trak = build_box('trak', audio_tkhd + audio_edts + audio_mdia)

    moov = build_box('moov', mvhd + video_trak + audio_trak + udta)
    assert len(moov) == moov_size_final

    # Write output
    output = ftyp + free + moov + new_mdat
    with open(output_path, 'wb') as f:
        f.write(output)

    stats = {
        'input_size': file_size,
        'output_size': len(output),
        'size_delta': len(output) - file_size,
        'original_frames': orig_frames,
        'declared_frames': total_frames,
        'ghost_frames': pad_count,
        'multiplier': multiplier,
        'duration_sec': round(v_duration_sec, 2),
    }

    if verbose:
        print(f"[+] Output: {output_path} ({len(output):,} bytes)")
        print(f"[+] Delta: {stats['size_delta']:+,} bytes")
        print(f"[+] Frames: {orig_frames} -> {total_frames} (x{multiplier})")
        print(f"[+] Duration: {stats['duration_sec']}s")

    return stats
