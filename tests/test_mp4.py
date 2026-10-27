"""Basic tests for tiktok-quality transform."""

from __future__ import annotations

import struct
from tiktok_quality.mp4.parser import find_box, parse_stts, parse_stsz, read_u32
from tiktok_quality.mp4.builder import build_box, build_ftyp, build_stts, build_stco, build_udta_comment


def test_build_box():
    box = build_box('test', b'\x01\x02\x03')
    assert len(box) == 11
    assert struct.unpack('>I', box[:4])[0] == 11
    assert box[4:8] == b'test'
    assert box[8:] == b'\x01\x02\x03'


def test_build_ftyp():
    ftyp = build_ftyp()
    assert len(ftyp) == 32
    assert ftyp[8:12] == b'isom'


def test_build_stts():
    stts = build_stts([(1494, 1500), (13446, 1500)])
    # header(8) + version/flags(4) + count(4) + 2*8 = 32
    assert len(stts) == 32
    assert struct.unpack('>I', stts[:4])[0] == 32


def test_build_stco():
    stco = build_stco([100, 200, 300])
    # header(8) + version/flags(4) + count(4) + 3*4 = 28
    assert len(stco) == 28


def test_build_udta_comment():
    udta = build_udta_comment('TestTag123')
    assert b'TestTag123' in udta
    assert b'\xa9cmt' in udta


def test_find_box():
    box1 = build_box('aaaa', b'\x00' * 4)
    box2 = build_box('bbbb', b'\x01' * 8)
    data = box1 + box2

    pos, size = find_box(data, 'bbbb')
    assert pos == len(box1)
    assert size == len(box2)


def test_parse_stts():
    entries = [(1494, 1500), (13446, 1500)]
    stts = build_stts(entries)
    parsed = parse_stts(stts, 0)
    assert parsed == entries


def test_parse_stsz():
    from tiktok_quality.mp4.builder import build_stsz
    sizes = [519, 145, 67, 67, 190]
    stsz = build_stsz(sizes)
    parsed = parse_stsz(stsz, 0)
    assert parsed == sizes


def test_transform_safe_mode():
    from tiktok_quality.transform import transform
    import os, tempfile

    sample = 'C:/Users/H4B/Downloads/Keyboard_zooming_in_and_out_20261022172506.mp4'
    if not os.path.exists(sample):
        return

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        out_path = tmp.name

    try:
        stats = transform(sample, out_path, multiplier=1, comment='', verbose=False)
        assert stats['ghost_frames'] == 0
        assert stats['declared_frames'] == stats['original_frames']
        assert stats['multiplier'] == 1
        assert stats['duration_sec'] == 10.0

        # Verify output file
        with open(out_path, 'rb') as f:
            data = f.read()
        assert b'isom' in data[:32]
        assert b'TK8vY5VqBA6hUlo1yuGvNA' not in data
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


def test_transform_inflation_mode():
    from tiktok_quality.transform import transform
    import os, tempfile

    sample = 'C:/Users/H4B/Downloads/Keyboard_zooming_in_and_out_20261022172506.mp4'
    if not os.path.exists(sample):
        return

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        out_path = tmp.name

    try:
        stats = transform(sample, out_path, multiplier=2, comment='', verbose=False)
        assert stats['ghost_frames'] == stats['original_frames']
        assert stats['declared_frames'] == stats['original_frames'] * 2
        assert stats['multiplier'] == 2
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


def test_build_avcc_no_duplicate_extension():
    from tiktok_quality.mp4.builder import build_avcc
    # Input already has the 4-byte high profile extension
    data_with_ext = b'\x01\x64\x00\x1f\xff\xe1' + b'\xfd\xf8\xf8\x00'
    res = build_avcc(data_with_ext, add_high_ext=True)
    # The box content should not have duplicate extension bytes
    assert res.count(b'\xfd\xf8\xf8\x00') == 1


def test_build_avcc_adds_extension():
    from tiktok_quality.mp4.builder import build_avcc
    data_without_ext = b'\x01\x42\x00\x1f\xff\xe1\x00\x05test'
    res = build_avcc(data_without_ext, add_high_ext=True)
    assert res.endswith(b'\xfd\xf8\xf8\x00')
    assert res.count(b'\xfd\xf8\xf8\x00') == 1


def test_transform_10x_micro_tick_timing():
    from tiktok_quality.transform import transform
    from tiktok_quality.mp4.parser import find_box_path, parse_stts
    import os, tempfile

    sample = 'C:/Users/H4B/Downloads/Keyboard_zooming_in_and_out_20261022172506.mp4'
    if not os.path.exists(sample):
        return

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        out_path = tmp.name

    try:
        stats = transform(sample, out_path, multiplier=10, comment='TK8vY5VqBA6hUlo1yuGvNA', verbose=False)
        assert stats['multiplier'] == 10
        assert stats['declared_frames'] == stats['original_frames'] * 10
        assert stats['ghost_frames'] == stats['original_frames'] * 9

        with open(out_path, 'rb') as f:
            data = f.read()

        # Check comment tag is present
        assert b'TK8vY5VqBA6hUlo1yuGvNA' in data

        # Check stts has native time_delta ghost frames (maintaining 60fps/30fps container)
        stts_pos, _ = find_box_path(data, ['moov', 'trak', 'mdia', 'minf', 'stbl', 'stts'])
        entries = parse_stts(data, stts_pos)
        assert len(entries) == 2
        assert entries[0][0] == stats['original_frames']
        assert entries[1][0] == stats['ghost_frames']
        assert entries[1][1] == entries[0][1], "Ghost frames must preserve native time_delta for valid 60fps"
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


def test_parse_co64():
    from tiktok_quality.mp4.parser import parse_co64
    import struct
    offsets = [1000, 5000000000, 10000000000]
    box_body = struct.pack('>II', 0, len(offsets)) + b''.join(struct.pack('>Q', o) for o in offsets)
    box = struct.pack('>I', 8 + len(box_body)) + b'co64' + box_body
    parsed = parse_co64(box, 0)
    assert parsed == offsets


def test_invalid_container_raises_value_error():
    from tiktok_quality.transform import transform
    import tempfile, os

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f_in, \
         tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f_out:
        f_in.write(b'not a real mp4 container')
        f_in.flush()
        in_path, out_path = f_in.name, f_out.name

    try:
        try:
            transform(in_path, out_path, verbose=False)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unsupported file" in str(e)
    finally:
        if os.path.exists(in_path): os.remove(in_path)
        if os.path.exists(out_path): os.remove(out_path)


def test_transform_safe_mode_1x():
    from tiktok_quality.transform import transform
    from tiktok_quality.mp4.parser import find_box_path, parse_stts
    import os, tempfile

    sample = 'C:/Users/H4B/Downloads/Keyboard_zooming_in_and_out_20261022172506.mp4'
    if not os.path.exists(sample):
        return

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        out_path = tmp.name

    try:
        stats = transform(sample, out_path, multiplier=1, comment='', verbose=False)
        assert stats['multiplier'] == 1
        assert stats['declared_frames'] == stats['original_frames']
        assert stats['ghost_frames'] == 0

        with open(out_path, 'rb') as f:
            data = f.read()

        assert b'TK8vY5VqBA6hUlo1yuGvNA' not in data

        stts_pos, _ = find_box_path(data, ['moov', 'trak', 'mdia', 'minf', 'stbl', 'stts'])
        entries = parse_stts(data, stts_pos)
        assert sum(c for c, _ in entries) == stats['original_frames']
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)



