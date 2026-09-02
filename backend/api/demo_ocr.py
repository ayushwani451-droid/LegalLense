"""
TEMPORARY / DEMO-ONLY route.

GET /api/demo-ocr-sync runs PaddleOCR synchronously on a small, pre-bundled
test image (backend/test_images/synthetic_label.png, ~51KB) and returns the
full OCRResult directly in the response - no job_id, no polling.

This exists ONLY to make a quick live demo simple (one request, immediate
result) when a full-size real image's 52-168s runtime and the async
job/poll flow (see api/ocr.py + api/job_store.py) would be awkward to
narrate live. It is NOT meant for production traffic and does not have the
job_store's timeout-avoidance behavior - a slow image here would still
tie up the request/proxy exactly like the old synchronous /api/ocr did.

Deliberately isolated from api/ocr.py and api/job_store.py: this file does
not import from, modify, or share any state with either. Delete this file
(and its one-line include_router registration in main.py) once the demo is
over.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

try:
    from backend.ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService
except ImportError:
    from ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService  # type: ignore[no-redef]

logger = logging.getLogger("legallense.api.demo_ocr")

router = APIRouter()

# Small, already-committed sample image - see backend/test_images/.
_DEMO_IMAGE_PATH = Path(__file__).resolve().parent.parent / "test_images" / "synthetic_label.png"

# Separate PaddleOCRService instance from api/ocr.py's - kept intentionally
# uncoupled so this demo route can be deleted without touching the real
# endpoint's service instance or engine cache lifecycle. PaddleOCRService
# caches engines by config at the class level, so this still reuses the
# same underlying engine (no duplicate model load) if api/ocr.py's service
# has already warmed one up with matching settings.
_demo_service = PaddleOCRService()


@router.get("/api/demo-ocr-sync")
def demo_ocr_sync():
    """DEMO-ONLY: run OCR synchronously on a bundled sample image and return
    the result immediately. Do not use for real uploads or production
    traffic - see the module docstring."""
    if not _DEMO_IMAGE_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Demo image not found: {_DEMO_IMAGE_PATH}")

    try:
        result = _demo_service.run(str(_DEMO_IMAGE_PATH))
    except OCRInitializationError as exc:
        logger.exception("Demo OCR engine failed to initialize")
        raise HTTPException(status_code=500, detail=f"OCR engine failed to initialize: {exc}") from exc
    except Exception as exc:
        logger.exception("Unexpected error during demo OCR run")
        raise HTTPException(status_code=500, detail="Unexpected server error during demo OCR") from exc

    return result.model_dump()
