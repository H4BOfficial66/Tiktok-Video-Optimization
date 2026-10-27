"""Unit tests for 7-u8 FPS patch and linkcheck modules."""

from __future__ import annotations

import os
import tempfile
import unittest
from fractions import Fraction

from tiktok_quality.fps_patch import detect_fps, exact_fps, patch_fps, patch_fps_bytes
from tiktok_quality.mp4.reader import Mp4Error, Mp4File


class TestFpsPatch(unittest.TestCase):
    def test_exact_fps(self):
        self.assertEqual(exact_fps(60), Fraction(60, 1))
        self.assertEqual(exact_fps(30), Fraction(30, 1))
        self.assertEqual(exact_fps(59.94), Fraction(60000, 1001))
        self.assertEqual(exact_fps(29.97), Fraction(30000, 1001))
        self.assertEqual(exact_fps(23.976), Fraction(24000, 1001))

    def test_patch_fps_sample(self):
        sample = "C:/Users/H4B/Downloads/Keyboard_zooming_in_and_out_20261022172506.mp4"
        if not os.path.exists(sample):
            self.skipTest("Sample video not found")

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            out_path = tmp.name

        try:
            report = patch_fps(sample, out_path, fps_in=24, fps_out=12, mode="method")
            self.assertGreaterEqual(len(report), 2)
            self.assertIn("FPS patch (method)", report[0])

            m = Mp4File(out_path)
            self.assertIsNotNone(m.video)
            self.assertAlmostEqual(m.video.fps, 12.0, delta=0.1)

            # Duration must remain exactly identical to original video (0% stretch, no 4-minute bug!)
            orig = Mp4File(sample)
            self.assertIsNotNone(orig.video)
            self.assertAlmostEqual(m.video.seconds, orig.video.seconds, delta=0.05)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_patch_fps_bytes(self):
        sample = "C:/Users/H4B/Downloads/Keyboard_zooming_in_and_out_20261022172506.mp4"
        if not os.path.exists(sample):
            self.skipTest("Sample video not found")

        with open(sample, "rb") as f:
            in_bytes = f.read()

        out_bytes, stats = patch_fps_bytes(in_bytes, fps_in=24, fps_out=12)
        self.assertEqual(len(out_bytes), len(in_bytes))
        self.assertEqual(stats["duration_sec"], 10.0)
        self.assertEqual(stats["target_fps"], 12.0)


if __name__ == "__main__":
    unittest.main()
