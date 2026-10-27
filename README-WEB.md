# TikTok Quality – Web version (Python backend)

The website uploads the MP4 to a FastAPI backend, which runs the original
`tiktok_quality.transform()` and sends the result back. Temp files are deleted
right after the response.

## Run locally
```bash
pip install -r requirements.txt
pip install -e .
uvicorn app.main:app --reload --port 8000     # open http://localhost:8000
```

## Docker
```bash
docker build -t tiktok-quality-web .
docker run -p 8000:8000 -e MAX_UPLOAD_MB=500 tiktok-quality-web
```

## Settings (env vars)
| Name | Default | Meaning |
|---|---|---|
| `MAX_UPLOAD_MB` | 500 | max upload size per file |
| `MAX_CONCURRENT_JOBS` | 2 | parallel transforms (each uses ~2x file size in RAM) |
| `PORT` | 8000 | Docker only |

## Deploy notes
- Needs a host that runs Python/Docker (VPS, Render, Railway, Fly.io...). Static-only hosts will not work.
- Reverse proxies/CDNs have their own upload caps (nginx `client_max_body_size`, Cloudflare free plan = 100 MB). Raise them or the upload fails before reaching the app.
