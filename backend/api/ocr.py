"""
HTTP layer for OCR. This module owns request validation, temp-file handling,
job bookkeeping, and response shaping ONLY - all actual OCR inference is
delegated to the existing, already-tested PaddleOCRService. No OCR/PaddleOCR
logic is duplicated or modified here.

Two-step async flow (real inference takes 52-168s+ on this CPU-only setup,
which exceeds Railway's/most reverse-proxies' request timeout if run
synchronously - the original single-request design produced 502s):

    POST /api/ocr                  -> validates + schedules, returns 202 + job_id immediately
    GET  /api/ocr/status/{job_id}  -> poll this until status is "done" or "error"

PaddleOCRService.run() is fully synchronous (confirmed: no `async def`
anywhere in it, `engine.predict()` is a direct blocking call), so the
background work is a plain `def`, not a coroutine. FastAPI/Starlette's
BackgroundTasks automatically runs a plain-`def` task in a worker thread
(via anyio.to_thread), which is exactly what's needed - `asyncio.create_task`
would instead run a blocking sync call straight on the event loop and freeze
the whole server, including this same status-polling endpoint, for the
entire 52-168s.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from backend.ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService
    from backend.ocr.schemas import OCRError, OCRResult
except ImportError:
    # See backend/main.py for why both import styles are supported.
    from ocr.paddle_ocr_service import OCRInitializationError, PaddleOCRService  # type: ignore[no-redef]
    from ocr.schemas import OCRError, OCRResult  # type: ignore[no-redef]

try:
    from backend.api.job_store import create_job, get_job, set_error, set_result, set_status
except ImportError:
    from api.job_store import create_job, get_job, set_error, set_result, set_status  # type: ignore[no-redef]

logger = logging.getLogger("legallense.api.ocr")

router = APIRouter()

# MAX_UPLOAD_MB is the single source of truth for the upload size limit.
# The frontend's own client-side check is early-UX-feedback only - this is
# the value that is actually enforced, and the one the frontend copy should
# match (see new_compliance_scan_legallense/code.html's "Max 15MB" text).
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "15")) * 1024 * 1024

# Kept configurable (default: disabled) because MKL-DNN was found to crash
# PaddleOCR's PIR executor on the Windows CPU this was developed/tested on
# (see backend/ocr/README.md). A Linux host may not hit the same bug and
# could re-enable it for a speed gain via OCR_ENABLE_MKLDNN=true, but the
# safe, verified-working default is preserved here rather than assumed.
_ENABLE_MKLDNN = os.environ.get("OCR_ENABLE_MKLDNN", "false").strip().lower() in ("1", "true", "yes")

# One shared service instance: PaddleOCRService caches its underlying
# PaddleOCR engine internally, so the (slow) model load happens once per
# process, not once per request. get_service() lets other modules (e.g.
# the startup warm-up hook in main.py) reuse the exact same instance
# without duplicating construction logic.
_service = PaddleOCRService(enable_mkldnn=_ENABLE_MKLDNN)


def get_service() -> PaddleOCRService:
    return _service


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Uploads are written into the OS temp dir under their own subfolder so we
# never write into (or serve from) the project tree, and never trust a
# client-supplied path.
_UPLOAD_TMP_DIR = Path(tempfile.gettempdir()) / "legallense_ocr_uploads"
_UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)


# --- response models (also drive the /docs OpenAPI schema) ----------------

class JobAcceptedResponse(BaseModel):
    job_id: str
    status: Literal["pending"] = "pending"


class JobStatusResponse(BaseModel):
    status: Literal["pending", "processing", "done", "error"]
    result: Optional[OCRResult] = None
    error: Optional[str] = None


def _error_response(status_code: int, image: str, code: str, message: str) -> JSONResponse:
    result = OCRResult(success=False, image=image, error=OCRError(code=code, message=message))
    return JSONResponse(status_code=status_code, content=result.model_dump())


# --- background worker ------------------------------------------------

def _process_ocr_job(job_id: str, tmp_path: Path, original_name: str) -> None:
    """Runs in a background thread, off the event loop. Never lets an
    exception from PaddleOCRService propagate silently - always records
    either a "done" result or an "error" status for the job."""
    set_status(job_id, "processing")
    try:
        result = _service.run(str(tmp_path))
        # Swap the temp filename back out for the original, client-facing name,
        # and scrub the server-side temp path out of any error message so no
        # internal filesystem detail reaches the browser.
        result.image = original_name
        result.image_path = None
        if result.error is not None:
            result.error.message = result.error.message.replace(str(tmp_path), original_name)

        logger.info(
            "OCR job %s for '%s' complete: success=%s detections=%d",
            job_id, original_name, result.success, result.detection_count,
        )
        # A well-formed OCRResult(success=False) - e.g. an unreadable image -
        # is still a normal completed job, not a job-level error. PaddleOCRService
        # already never raises for expected failure modes; only a genuine
        # exception (below) counts as a job "error".
        set_result(job_id, result.model_dump())

    except OCRInitializationError:
        logger.exception("PaddleOCR engine failed to initialize for job %s", job_id)
        set_error(job_id, "OCR engine failed to initialize")

    except Exception:
        logger.exception("Unexpected error while processing OCR job %s for %s", job_id, original_name)
        set_error(job_id, "Unexpected server error during OCR processing")

    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            logger.warning("Failed to clean up temp file %s", tmp_path, exc_info=True)


# --- routes -----------------------------------------------------------

@router.post("/api/ocr", response_model=JobAcceptedResponse, status_code=202)
async def run_ocr(background_tasks: BackgroundTasks, file: Optional[UploadFile] = File(None)):
    """Validate the upload and schedule PaddleOCR to run in the background.
    Returns immediately with a job_id - poll GET /api/ocr/status/{job_id}
    for the result. Validation failures (bad/missing/oversized file) are
    still reported synchronously here, before any job is created."""

    if file is None or not file.filename:
        logger.warning("OCR request received with no file attached")
        return _error_response(400, "", "missing_file", "No file was uploaded")

    # Path(...).name strips any directory component the client might send
    # (e.g. "../../etc/passwd.jpg") - only the base filename is ever used,
    # and only for display; it is never used to build a filesystem path.
    original_name = Path(file.filename).name
    suffix = Path(original_name).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        logger.warning("Rejected unsupported file type '%s' for %s", suffix, original_name)
        return _error_response(
            400, original_name, "unsupported_file_type",
            f"Unsupported file type '{suffix}'. Supported: {sorted(ALLOWED_EXTENSIONS)}",
        )

    contents = await file.read()
    if not contents:
        logger.warning("Rejected empty upload for %s", original_name)
        return _error_response(400, original_name, "empty_upload", "Uploaded file is empty")

    if len(contents) > MAX_UPLOAD_BYTES:
        logger.warning("Rejected oversized upload for %s (%d bytes)", original_name, len(contents))
        return _error_response(
            400, original_name, "file_too_large",
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
        )

    # Random server-generated filename - never derived from client input.
    tmp_path = _UPLOAD_TMP_DIR / f"{uuid.uuid4().hex}{suffix}"
    tmp_path.write_bytes(contents)
    logger.info("Saved upload '%s' (%d bytes) to temp file for OCR", original_name, len(contents))

    job_id = create_job()
    background_tasks.add_task(_process_ocr_job, job_id, tmp_path, original_name)
    logger.info("Scheduled OCR job %s for '%s'", job_id, original_name)

    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})


@router.get("/api/ocr/status/{job_id}", response_model=JobStatusResponse)
async def get_ocr_status(job_id: str):
    """Poll this for the result of a job created by POST /api/ocr."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] == "done":
        return JobStatusResponse(status="done", result=job["result"])
    if job["status"] == "error":
        return JobStatusResponse(status="error", error=job["error"])
    return JobStatusResponse(status=job["status"])
