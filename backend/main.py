"""
LegalLense backend - production-ready FastAPI entrypoint.

Scope for now: OCR only.
    LegalLense Upload UI -> POST /api/ocr -> PaddleOCRService -> OCR JSON

Local run (from the `stitch_legallense_compliance_portal` directory):
    backend\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload --port 8000

Production run (Render/Railway/any host that sets $PORT), same command
everywhere - never bind to localhost/127.0.0.1 in production:
    uvicorn backend.main:app --host 0.0.0.0 --port $PORT

Everything downstream of OCR (Qwen3-VL extraction, the Legal Metrology rule
engine, persistence, auth, etc.) is intentionally not implemented here yet.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.ocr import router as ocr_router, get_service as get_ocr_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("legallense.main")

app = FastAPI(
    title="LegalLense Backend",
    description="OCR integration layer for the LegalLense compliance portal.",
    version="0.1.0",
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


@app.on_event("startup")
def warm_up_ocr_engine() -> None:
    """Load the PaddleOCR models once at process startup instead of on the
    first request, so the first real user doesn't pay the ~model-load cost
    on top of inference time. Failure here is logged but not fatal - the
    OCR endpoint will surface a clear engine_init_failed error per-request
    if the engine genuinely cannot initialize."""
    try:
        logger.info("Warming up PaddleOCR engine at startup...")
        get_ocr_service()._get_engine()
        logger.info("PaddleOCR engine ready.")
    except Exception:
        logger.exception("PaddleOCR engine failed to warm up at startup; will retry per-request.")


@app.get("/api/health")
def health_check():
    """Safe, minimal health payload - no file paths, keys, or stack traces."""
    return {
        "status": "healthy",
        "ocr": "available",
        "service": "legallense-backend",
        "version": app.version,
    }
