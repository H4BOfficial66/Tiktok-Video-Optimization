# TikTok Video Optimizer – 1080p 60fps Container Tool

[![Live Website](https://img.shields.io/badge/Live%20Website-GitHub%20Pages-fe2c55?style=for-the-badge&logo=github)](https://h4bofficial66.github.io/Tiktok-Video-Optimization/)
[![Deploy to Render](https://img.shields.io/badge/Deploy%20Backend-Render-46E3B7?style=for-the-badge&logo=render)](https://render.com/deploy)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)

> 🌐 **Live Website URL**: **[https://h4bofficial66.github.io/Tiktok-Video-Optimization/](https://h4bofficial66.github.io/Tiktok-Video-Optimization/)**  
> ⚡ **100% Client-Side Engine**: Process videos directly in your browser without waiting for uploads or server limits.  
> ☁️ **Docker / FastAPI Cloud API**: Full server-side backend ready for Docker, Render, Railway, or VPS.

---

## 🚀 Features

- **Zero Compression Loss**: No re-encoding, pure MP4 container metadata modification.
- **Instant In-Browser Processing**: Pure JavaScript Web Worker engine runs locally in your browser.
- **Fast-Start Layout**: Relocates `moov` atom ahead of `mdat` for instant streaming on TikTok.
- **SEI Cleanup**: Strips non-essential SEI NALUs from the first video sample.
- **High Profile Patch**: Patches `avcC` and updates bitrate boxes (`btrt`).
- **Ghost Frame Inflation**: Appends 10x-50x ghost frame entries pointing to an 8-byte filler block.

---


```bash
pip install tiktok-quality
```

Or from source:

```bash
git clone https://github.com/BastienGimbert/tiktok-quality.git
cd tiktok-quality
pip install -e .
```

### Requirements

- **Python 3.10+** - zero third-party dependencies
- **FFmpeg** - optional, for output verification only

```bash
# Check dependencies
tiktok-quality --check-deps

# Install FFmpeg if needed:
# Windows: winget install Gyan.FFmpeg
# macOS:   brew install ffmpeg
# Linux:   sudo apt install ffmpeg
```

## Usage

### CLI

```bash
# Transform a video for full 1080p60 quality (default 10x trigger)
tiktok-quality input.mp4 output.mp4

# Safe mode (1x passthrough without ghost frames)
tiktok-quality input.mp4 output.mp4 -m 1

# Custom metadata tag
tiktok-quality input.mp4 output.mp4 --comment "MyTag123"

# Quiet mode
tiktok-quality input.mp4 output.mp4 -q
```

### Verification

```bash
# Verify output with ffprobe
tiktok-quality --verify output.mp4

# Byte-compare with a reference file
tiktok-quality --compare output.mp4 reference.mp4
```

### Python API

```python
from tiktok_quality import transform

stats = transform("input.mp4", "output.mp4", multiplier=10)
print(f"Declared frames: {stats['declared_frames']}, Duration: {stats['duration_sec']}s")
```

## How It Works

Manipulates the MP4 container metadata to inflate the declared frame count by adding "ghost frames" - filler NALUs pointing to a single 8-byte padding block.

```
Input:  1920x1080 60fps, 1494 real frames, 50.7 MB
Output: Same video,     14940 declared frames, 50.8 MB (+107 KB overhead)
```

No pixels are changed. No re-encoding. The video data is byte-for-byte identical but tiktok-quality tricks TikTok into thinking the video has more frames, allowing it to be uploaded at 1080p60.

### Transformations Applied

| # | What | Detail |
|---|---|---|
| 1 | Brand | `mp42` -> `isom` |
| 2 | Box order | moov moved before mdat (fast-start) |
| 3 | First frame | SEI NALUs stripped, IDR slice kept |
| 4 | avcC | High profile extension bytes added |
| 5 | Video STTS | Ghost frame timing entries appended |
| 6 | Video STSZ | 8-byte entries for each ghost frame |
| 7 | Video STSC | New chunk entry for padding region |
| 8 | Video STCO | Ghost chunks point to single filler NAL |
| 9 | Padding NAL | 8 bytes appended: `00 00 00 04 00 00 00 00` |
| 10 | Audio handler | Renamed to "SoundHandler", lang -> `und` |
| 11 | Audio timing | Last sample trimmed to align with video |
| 12 | Metadata | iTunes-style comment tag in ilst |
| 13 | Bitrates | Recalculated for both tracks |

### Ghost Frame Mechanism

All ghost frames reference a single 8-byte filler NALU at the end of mdat:

```
00 00 00 04   <- NALU length (4 bytes payload)
00 00 00 00   <- type 0 (filler) + empty content
```

13,446 ghost "frames" share this one block via STCO. Only **8 bytes** of media data added. Metadata tables grow by ~107 KB.

## Project Structure

```
tiktok-quality/
├── pyproject.toml
├── README.md
├── LICENSE
├── proof.png
├── .github/workflows/publish.yml
├── src/tiktok_quality/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── transform.py
│   └── mp4/
│       ├── parser.py
│       └── builder.py
└── tests/
    └── test_mp4.py
```

## Background

Reverse-engineered from the "TikTok Enhancer" Chrome extension (Editing News v2.1.4) which uses a server at `v2.editingnews.com` to apply this container manipulation. The extension:

1. Intercepts video file on TikTok upload page
2. Sends to their server for container manipulation
3. Server returns "enhanced" file
4. Extension replaces the file in TikTok's upload form

This tool replicates the exact server output - **byte-for-byte identical**.

![proof](https://raw.githubusercontent.com/BastienGimbert/tiktok-quality/refs/heads/main/proof.png)

**1080p60** upload quality confirmed via re:TikTok Checker & Downloader bot.

## License

MIT see [LICENSE](LICENSE) file for details.
