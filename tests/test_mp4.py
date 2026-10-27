"""Basic tests for tiktok-quality transform."""

from __future__ import annotations

import os
import struct
import tempfile
import unittest

from tiktok_quality.mp4.builder import (
    build_box,
    build_ftyp,
    build_stco,
    build_stts,
    build_udta_comment,
)
from tiktok_quality.mp4.parser import find_box, parse_stsz, parse_stts, read_u32


class TestMp4(unittest.TestCase):
    def test_build_box(self):
        box = build_box('test', b'\x01\x02\x03')
        self.assertEqual(len(box), 11)
        self.assertEqual(struct.unpack('>I', box[:4])[0], 11)
        self.assertEqual(box[4:8], b'test')
        self.assertEqual(box[8:], b'\x01\x02\x03')

    def test_build_ftyp(self):
        ftyp = build_ftyp()
        self.assertEqual(len(ftyp), 32)
        self.assertEqual(ftyp[8:12], b'isom')

    def test_build_stts(self):
        stts = build_stts([(1494, 1500), (13446, 1500)])
        self.assertEqual(len(stts), 32)
        self.assertEqual(struct.unpack('>I', stts[:4])[0], 32)

    def test_build_stco(self):
        stco = build_stco([100, 200, 300])
        self.assertEqual(len(stco), 28)

    def test_build_udta_comment(self):
        udta = build_udta_comment('TestTag123')
        self.assertIn(b'TestTag123', udta)
        self.assertIn(b'\xa9cmt', udta)

    def test_find_box(self):
        box1 = build_box('aaaa', b'\x00' * 4)
        box2 = build_box('bbbb', b'\x01' * 8)
        data = box1 + box2
        pos, size = find_box(data, 'bbbb')
        self.assertEqual(pos, len(box1))
        self.assertEqual(size, len(box2))

    def test_parse_stts(self):
        entries = [(1494, 1500), (13446, 1500)]
        stts = build_stts(entries)
        parsed = parse_stts(stts, 0)
        self.assertEqual(parsed, entries)

    def test_parse_stsz(self):
        from tiktok_quality.mp4.builder import build_stsz
        sizes = [519, 145, 67, 67, 190]
        stsz = build_stsz(sizes)
        parsed = parse_stsz(stsz, 0)
        self.assertEqual(parsed, sizes)

    def test_parse_co64(self):
        from tiktok_quality.mp4.parser import parse_co64
        offsets = [1000, 5000000000, 10000000000]
        box_body = struct.pack('>II', 0, len(offsets)) + b''.join(struct.pack('>Q', o) for o in offsets)
        box = struct.pack('>I', 8 + len(box_body)) + b'co64' + box_body
        parsed = parse_co64(box, 0)
        self.assertEqual(parsed, offsets)

    def test_invalid_container_raises_value_error(self):
        from tiktok_quality.transform import transform
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f_in, \
             tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as f_out:
            f_in.write(b'not a real mp4 container')
            f_in.flush()
            in_path, out_path = f_in.name, f_out.name

        try:
            with self.assertRaises(ValueError):
                transform(in_path, out_path, verbose=False)
        finally:
            if os.path.exists(in_path): os.remove(in_path)
            if os.path.exists(out_path): os.remove(out_path)

    def test_transform_safe_mode_1x(self):
        from tiktok_quality.transform import transform
        from tiktok_quality.mp4.parser import find_box_path, parse_stts
        sample = 'C:/Users/H4B/Downloads/Keyboard_zooming_in_and_out_20261022172506.mp4'
        if not os.path.exists(sample):
            self.skipTest("Sample video not found")

        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
            out_path = tmp.name

        try:
            stats = transform(sample, out_path, multiplier=1, comment='', verbose=False)
            self.assertEqual(stats['multiplier'], 1)
            self.assertEqual(stats['declared_frames'], stats['original_frames'])
            self.assertEqual(stats['ghost_frames'], 0)

            with open(out_path, 'rb') as f:
                data = f.read()

            self.assertNotIn(b'TK8vY5VqBA6hUlo1yuGvNA', data)
            stts_pos, _ = find_box_path(data, ['moov', 'trak', 'mdia', 'minf', 'stbl', 'stts'])
            entries = parse_stts(data, stts_pos)
            self.assertEqual(sum(c for c, _ in entries), stats['original_frames'])
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)


if __name__ == '__main__':
    unittest.main()
