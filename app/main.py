"""Web backend: FastAPI service for 7-u8 FPS method & container optimization."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from tiktok_quality import check_tiktok, patch_fps, transform

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))  # per-file limit
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))  # RAM protection
DEFAULT_COMMENT = "TK8vY5VqBA6hUlo1yuGvNA"

app = FastAPI(title="TikTok Quality Optimizer", docs_url=None, redoc_url=None)
jobs = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


def _run_patch(src: Path, dst: Path, fps_in: Optional[float], fps_out: float, mode: str) -> dict:
    try:
        report = patch_fps(str(src), str(dst), fps_in=fps_in, fps_out=fps_out, mode=mode)
        from tiktok_quality.mp4.reader import Mp4File
        m = Mp4File(str(dst))
        return {
            "source_fps": fps_in or (m.video.fps if m.video else 30.0),
            "target_fps": fps_out,
            "duration_sec": m.video.seconds if m.video else 0.0,
            "mode": mode,
            "report": report,
            "method": "7-u8_fps_patch",
        }
    except Exception as e:
        raise ValueError(f"Could not apply 7-u8 FPS patch: {e}")


def _run_ghost(src: Path, dst: Path, multiplier: int, comment: str) -> dict:
    try:
        return transform(str(src), str(dst), multiplier=multiplier, comment=comment, verbose=False)
    except SystemExit:
        raise ValueError("Unsupported file: no moov/mdat box or video track found. Use a standard H.264 MP4.")
    except Exception as e:
        raise ValueError(f"Could not process this file ({type(e).__name__}). It must be a standard H.264 MP4.")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": "1.1.0",
        "features": ["7-u8_fps_patch", "ghost_frames", "tiktok_linkcheck"],
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.get("/api/check_tiktok")
async def api_check_tiktok(url: str = Query(..., description="TikTok video URL")):
    try:
        info = await run_in_threadpool(check_tiktok, url)
        return JSONResponse(content={"ok": True, "data": info})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@app.post("/api/patch_fps")
async def api_patch_fps(
    file: UploadFile = File(...),
    fps_in: Optional[float] = Form(None),
    fps_out: float = Form(30.0),
    mode: str = Form("method"),
):
    name = file.filename or "video.mp4"
    if not name.lower().endswith(".mp4"):
        raise HTTPException(400, "Only .mp4 files are supported.")

    workdir = tempfile.mkdtemp(prefix="tq_patch_")
    src, dst = Path(workdir) / "in.mp4", Path(workdir) / "out.mp4"
    try:
        size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
        with open(src, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB).")
                f.write(chunk)
        async with jobs:
            stats = await run_in_threadpool(_run_patch, src, dst, fps_in, fps_out, mode)
    except ValueError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(422, str(e))
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    return FileResponse(
        dst,
        media_type="video/mp4",
        filename=Path(name).stem + "_smooth_tiktok.mp4",
        headers={"X-Stats": json.dumps(stats)},
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


@app.post("/api/transform")
async def api_transform(
    file: UploadFile = File(...),
    multiplier: int = Form(10),
    comment: str = Form(DEFAULT_COMMENT),
):
    name = file.filename or "video.mp4"
    if not name.lower().endswith(".mp4"):
        raise HTTPException(400, "Only .mp4 files are supported.")
    multiplier = max(1, min(50, multiplier))
    comment = (comment.strip() or DEFAULT_COMMENT)[:64]

    workdir = tempfile.mkdtemp(prefix="tq_")
    src, dst = Path(workdir) / "in.mp4", Path(workdir) / "out.mp4"
    try:
        size, limit = 0, MAX_UPLOAD_MB * 1024 * 1024
        with open(src, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB).")
                f.write(chunk)
        async with jobs:
            stats = await run_in_threadpool(_run_ghost, src, dst, multiplier, comment)
    except ValueError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(422, str(e))
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    return FileResponse(
        dst,
        media_type="video/mp4",
        filename=Path(name).stem + "_tq.mp4",
        headers={"X-Stats": json.dumps(stats)},
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
