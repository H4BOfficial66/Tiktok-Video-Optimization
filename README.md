# TikTok Video Optimizer – 1080p 60fps & 7-u8 Smoothness Toolkit

[![Live Website](https://img.shields.io/badge/Live%20Website-GitHub%20Pages-fe2c55?style=for-the-badge&logo=github)](https://h4bofficial66.github.io/Tiktok-Video-Optimization/)
[![Deploy to Render](https://img.shields.io/badge/Deploy%20Backend-Render-46E3B7?style=for-the-badge&logo=render)](https://render.com/deploy)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)

> 🌐 **Live Web Application**: **[https://h4bofficial66.github.io/Tiktok-Video-Optimization/](https://h4bofficial66.github.io/Tiktok-Video-Optimization/)**  
> ⚡ **100% Client-Side Engine**: Process videos directly in your browser without waiting for uploads or cloud quotas.  
> ☁️ **Docker / FastAPI Cloud API**: Full server backend ready for Docker, Render, Railway, or VPS.

---

## 🚀 Key Features

1. **7-u8 Smoothness Trick (Community 120 FPS / 60 FPS Method)**:
   - **Exact Duration Guaranteed**: A 13-second video remains **EXACTLY 13.00 seconds** (0% stretch, NO 4-minute frozen video bug!).
   - Rewrites `mvhd` and `mdhd` timescale and duration timing headers so TikTok's ingestion transcoder preserves high framerates (60/120 fps) without downscaling to 540p or forcing to private.
   - Zero dummy frames, zero pixel re-encoding.
2. **TikTok Link Inspector (`check`)**:
   - Inspect any published TikTok video URL (`https://www.tiktok.com/@user/video/...`) to see the exact stream resolutions (1080p, 720p, 540p), frame rates (60fps vs 30fps), and bitrates served by TikTok.
3. **Legacy Ghost Frame Container Engine**:
   - Optional frame multiplier (1x Safe Mode for 100% public visibility, 5x - 10x).
4. **Fast-Start MP4 Streaming**:
   - Relocates `moov` ahead of `mdat` for instant playback and processing.

---

## 💻 CLI Usage

Install locally:
```bash
git clone https://github.com/H4BOfficial66/Tiktok-Video-Optimization.git
cd Tiktok-Video-Optimization
pip install -e .
```

### 1. Apply 7-u8 Smoothness Patch (Recommended)
```bash
# Auto-detects source FPS and patches container timing to 30 FPS ingestion
tiktok-quality patch video.mp4 output.mp4

# Explicit frame rate
tiktok-quality patch input.mp4 output.mp4 --fps-in 60 --fps-out 30
```

### 2. Check a Published TikTok Video Link
```bash
tiktok-quality check https://www.tiktok.com/@username/video/1234567890123456789
```

### 3. Legacy Ghost Frame Transform
```bash
tiktok-quality ghost video.mp4 output.mp4 -m 10
```

---

## 🐍 Python API

```python
from tiktok_quality import patch_fps, check_tiktok, transform

# 7-u8 FPS patch (exact duration, no stretch)
report = patch_fps("input.mp4", "output_smooth.mp4", fps_in=60, fps_out=30)
print(report)

# Inspect a live TikTok URL
info = check_tiktok("https://www.tiktok.com/@user/video/123456789")
print(f"Streams: {info['streams']}")

# Ghost frames
stats = transform("input.mp4", "output_ghost.mp4", multiplier=10)
```

---

## 🌐 Web Backend API (FastAPI)

Run local web server:
```bash
python -m uvicorn app.main:app --port 8000
```

- `POST /api/patch_fps`: Upload MP4 -> Returns patched 7-u8 MP4 with exact duration.
- `GET /api/check_tiktok?url=...`: Returns video stream resolutions and FPS breakdown.
- `POST /api/transform`: Legacy ghost frames transform.

---

## 🔍 Frequently Asked Questions (FAQ)

### 1. Why did a 13-second video turn into 4 minutes?
In older ghost frame scripts, thousands of dummy frames were added to the `stts` sample table with real frame duration deltas. A 13-second video with multiplier 18x added 221 seconds of dead silence, turning the video into 4 minutes of frozen screen. TikTok's automated safety scanner flagged the 3.5 minutes of frozen frames and demoted the video to 540p or restricted it to private.  
**The 7-u8 timescale method solves this completely: duration remains 100% exact to the millisecond!**

### 2. Is `TK8vY5VqBA6hUlo1yuGvNA` a secret API key?
**No.** `TK8vY5VqBA6hUlo1yuGvNA` is simply an iTunes metadata comment watermark (`©cmt`) hardcoded by early reverse-engineered tools. TikTok's upload server does not authenticate or require this string. The real secret is the MP4 container timescale patch.

---

## 📜 Credits & License

- 7-u8 Smoothness technique & Linkcheck based on [7-u8/TikTok-Quality-Toolkit](https://github.com/7-u8/TikTok-Quality-Toolkit) (ABUSHADAD | ابو شداد), [ut0ku/120fps-method](https://github.com/ut0ku/120fps-method), and [EditingNews EN-TikTok-60FPS](https://github.com/lamle25/EN-TikTok-60FPS).
- Container parsing based on [BastienGimbert/tiktok-quality](https://github.com/BastienGimbert/tiktok-quality).
- Licensed under [MIT License](LICENSE).
