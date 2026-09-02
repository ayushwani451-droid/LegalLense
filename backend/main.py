"""
LegalLense backend - production-ready FastAPI entrypoint.

Scope for now: OCR only.
    LegalLense Upload UI -> POST /api/ocr -> PaddleOCRService -> OCR JSON

Local run (from the `stitch_legallense_compliance_portal` directory):
    backend\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload --port 8000

Production run (Render/Railway/any host that sets $PORT), never bind to
localhost/127.0.0.1 in production. The exact module path depends on whether
the host's working/root directory is the repo root or `backend/` itself -
both import styles are supported (see the try/except below):
    uvicorn backend.main:app --host 0.0.0.0 --port $PORT     # root dir = repo root
    uvicorn main:app --host 0.0.0.0 --port $PORT              # root dir = backend/

Everything downstream of OCR (Qwen3-VL extraction, the Legal Metrology rule
engine, persistence, auth, etc.) is intentionally not implemented here yet.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    # Works when the process is started from the repo root, e.g.
    # `uvicorn backend.main:app` (local dev, Render with Root Directory unset).
    from backend.api.ocr import router as ocr_router, init_worker_engine as init_ocr_worker_engine
except ImportError:
    # Works when `backend/` itself is the working root, e.g. Render/Vercel
    # with Root Directory (or a service's "root") set to `backend`, where
    # `backend` is not importable as a top-level package from inside itself.
    from api.ocr import router as ocr_router, init_worker_engine as init_ocr_worker_engine  # type: ignore[no-redef]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("legallense.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the OCR worker process's lifetime alongside the app's.

    A single concurrent.futures.ProcessPoolExecutor(max_workers=1) is
    created here and stored on app.state so backend/api/ocr.py's request
    handler can submit OCR jobs to it (see that module's docstring for why
    inference runs in a separate OS process rather than a BackgroundTasks
    thread). Exactly one worker, never more: PaddleOCR is memory-heavy, and
    running a second OCR job concurrently would mean two full model copies
    competing for RAM on top of each other.

    The engine load (~20s) happens once, lazily, inside that worker
    process on first use - so it is warmed up here at startup, the same
    reasoning the previous single-process version used, just aimed at the
    process that now actually performs inference instead of this one.
    """
    executor = ProcessPoolExecutor(max_workers=1)
    app.state.ocr_process_pool = executor
    try:
        logger.info("Warming up PaddleOCR engine in the OCR worker process...")
        await asyncio.get_running_loop().run_in_executor(executor, init_ocr_worker_engine)
        logger.info("PaddleOCR engine ready in worker process.")
    except Exception:
        logger.exception("PaddleOCR engine failed to warm up in worker process; will retry on first job.")

    yield

    logger.info("Shutting down OCR worker process...")
    executor.shutdown(wait=True)


app = FastAPI(
    title="LegalLense Backend",
    description="OCR integration layer for the LegalLense compliance portal.",
    version="0.1.0",
    lifespan=lifespan,
)

# --- CORS -------------------------------------------------------------
# CORS_ALLOWED_ORIGINS is a comma-separated list of exact origins allowed to
# call this API, e.g.:
#   CORS_ALLOWED_ORIGINS=https://legallense.vercel.app,https://legallense-git-main.vercel.app
# Set this in the backend host's (Render/Railway) environment variables for
# production. It is intentionally NOT hardcoded here, and "*" is
# intentionally never used, since the API accepts file uploads.
_env_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "")
_configured_origins = [o.strip() for o in _env_origins.split(",") if o.strip()]

# Local dev has no fixed frontend port (Vite dev server, `vite preview`,
# a plain static file server, etc. all use different ports), so any
# localhost/127.0.0.1 origin is additionally allowed via regex. This regex
# only ever matches loopback addresses, so it is harmless in production -
# real internet traffic never has a localhost Origin header - and is kept
# unconditionally rather than only when CORS_ALLOWED_ORIGINS is unset, so
# local testing against a deployed backend still works.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_configured_origins,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

if _configured_origins:
    logger.info("CORS: explicit allowed origins = %s", _configured_origins)
else:
    logger.warning(
        "CORS_ALLOWED_ORIGINS is not set - only localhost/127.0.0.1 origins are allowed. "
        "Set it to your Vercel deployment URL(s) before going to production."
    )

app.include_router(ocr_router)


@app.get("/api/health")
def health_check():
    """Safe, minimal health payload - no file paths, keys, or stack traces."""
    return {
        "status": "healthy",
        "ocr": "available",
        "service": "legallense-backend",
        "version": app.version,
    }
