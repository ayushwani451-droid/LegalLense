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


def _build_ocr_process_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=1)


async def _warm_up_ocr_pool(executor: ProcessPoolExecutor) -> None:
    """Pre-load the PaddleOCR engine in `executor`'s worker process so the
    next job submitted to it doesn't pay the ~20s model-load cost inline.
    Failure here is logged but not fatal - a per-job engine_init_failed
    error (or, on a genuinely dead worker, another crash-and-recreate
    cycle) will surface it instead."""
    try:
        logger.info("Warming up PaddleOCR engine in the OCR worker process...")
        await asyncio.get_running_loop().run_in_executor(executor, init_ocr_worker_engine)
        logger.info("PaddleOCR engine ready in worker process.")
    except Exception:
        logger.exception("PaddleOCR engine failed to warm up in worker process; will retry on first job.")


async def recreate_ocr_process_pool(app: FastAPI, broken_executor: ProcessPoolExecutor) -> ProcessPoolExecutor:
    """Replace app.state.ocr_process_pool after it has broken (e.g. the
    single worker process was OOM-killed mid-inference) so the NEXT job
    gets a working pool instead of the permanent
    "BrokenProcessPool...not usable anymore" state a ProcessPoolExecutor
    is left in once its worker dies.

    Safe to call concurrently from multiple in-flight OCR jobs: a broken
    pool fails ALL of its pending/queued futures at once (not just the one
    that was actually running), so several backend/api/ocr.py request
    tasks can all land here at nearly the same moment. app.state.ocr_pool_lock
    (created in lifespan below) plus the compare-and-swap check just below
    ensure only the first caller actually builds a replacement - every
    other caller sees that app.state.ocr_process_pool no longer `is` the
    broken executor it was given, and simply returns the pool the winner
    already created instead of building (and leaking) another one.
    """
    async with app.state.ocr_pool_lock:
        current = app.state.ocr_process_pool
        if current is not broken_executor:
            return current  # someone else already replaced it

        logger.warning("OCR process pool is broken - creating a replacement worker process...")
        try:
            broken_executor.shutdown(wait=False)
        except Exception:
            logger.exception("Error shutting down the already-broken OCR process pool (ignoring)")

        new_executor = _build_ocr_process_pool()
        app.state.ocr_process_pool = new_executor
        # Fire-and-forget: don't make the caller (itself in the middle of
        # handling a crashed job) wait ~20s for the new pool's engine to
        # warm up. If the next job arrives before this finishes, it just
        # pays that cold-start cost inline instead, same as the very first
        # job after a fresh app start.
        asyncio.create_task(_warm_up_ocr_pool(new_executor))
        logger.info("OCR process pool replaced; subsequent jobs will use the new worker process.")
        return new_executor


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

    app.state.ocr_pool_lock and app.state.ocr_pool_recreate back the
    self-healing behavior in recreate_ocr_process_pool() above - stored on
    app.state (rather than imported directly) so backend/api/ocr.py can
    trigger a recreation after a BrokenProcessPool without importing this
    module and creating a main.py <-> api/ocr.py import cycle (main.py
    already imports from api/ocr.py).

    The engine load (~20s) happens once, lazily, inside the worker process
    on first use - so it is warmed up here at startup, the same reasoning
    the previous single-process version used, just aimed at the process
    that now actually performs inference instead of this one.
    """
    app.state.ocr_pool_lock = asyncio.Lock()
    app.state.ocr_pool_recreate = recreate_ocr_process_pool

    executor = _build_ocr_process_pool()
    app.state.ocr_process_pool = executor
    await _warm_up_ocr_pool(executor)

    yield

    logger.info("Shutting down OCR worker process...")
    app.state.ocr_process_pool.shutdown(wait=True)


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
