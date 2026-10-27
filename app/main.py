"""Web backend: runs the original tiktok_quality.transform() on uploaded MP4s."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from tiktok_quality import transform

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))      # per-file limit
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))  # RAM protection
DEFAULT_COMMENT = "TK8vY5VqBA6hUlo1yuGvNA"

app = FastAPI(title="TikTok Quality", docs_url=None, redoc_url=None)
jobs = asyncio.Semaphore(MAX_CONCURRENT_JOBS)


def _run(src: Path, dst: Path, multiplier: int, comment: str) -> dict:
    """Call the original script; turn its sys.exit()/crashes into clean errors."""
    try:
        return transform(str(src), str(dst), multiplier=multiplier,
                         comment=comment, verbose=False)
    except SystemExit:
        raise ValueError("Unsupported file: no moov/mdat box or video track found. "
                         "Use a standard H.264 MP4.")
    except Exception as e:  # malformed / unsupported container layout
        raise ValueError(f"Could not process this file ({type(e).__name__}). "
                         "It must be a standard H.264 MP4.")


@app.get("/api/health")
def health():
    return {"ok": True, "max_upload_mb": MAX_UPLOAD_MB}


@app.post("/api/transform")
async def api_transform(
    file: UploadFile = File(...),
    multiplier: int = Form(10),
    comment: str = Form(DEFAULT_COMMENT),
):
    name = file.filename or "video.mp4"
    if not name.lower().endswith(".mp4"):
        raise HTTPException(400, "Only .mp4 files are supported.")
    multiplier = max(2, min(50, multiplier))
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
            stats = await run_in_threadpool(_run, src, dst, multiplier, comment)
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
