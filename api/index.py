# Vercel entry point: exposes the FastAPI app from app/main.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401  (Vercel looks for `app`)


@app.middleware("http")
async def vercel_runtime_warnings(request, call_next):
    """Prevent unsupported expedition starts on ephemeral serverless workers."""
    from fastapi.responses import JSONResponse
    from os import environ

    if (environ.get("VERCEL") and not (
            environ.get("QUEUE_MODE") == "redis" and environ.get("REDIS_URL")
            and environ.get("QA_S3_BUCKET")) and request.method == "POST"
            and request.url.path == "/api/runs"):
        return JSONResponse(status_code=503, content={"detail":
            "QArena browser expeditions need a persistent worker. This Vercel deployment cannot keep its Playwright job and live event stream running. Use the configured persistent deployment or connect a durable job worker."})
    response = await call_next(request)
    response.headers["X-QArena-Run-Mode"] = (
        "redis-worker" if environ.get("QUEUE_MODE") == "redis"
        else "serverless-limited" if environ.get("VERCEL") else "persistent-worker")
    return response
